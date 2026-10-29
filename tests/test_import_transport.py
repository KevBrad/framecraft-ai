"""Transport boundaries use real HTTPResponse EOF behavior and pinned fake sockets."""
import hashlib
import http.client
import io
import json
import socket
import threading
import time

import pytest

from backend import import_transport as transport
from backend import ingestion


MEDIA = b'\x00\x00\x00\x18ftypmp42' + b'video bytes' * 60_000
URL = 'https://media.example/original.mp4?signature=PRIVATE'


class Response:
    def __init__(self, data=MEDIA, status=200, headers=None, fail_after=None):
        self.data, self.status = data, status
        self.headers = {'Content-Type': 'video/mp4', 'ETag': '"version-1"', **(headers or {})}
        self.fail_after, self.reads = fail_after, 0

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def isclosed(self):
        return not self.data

    def read1(self, count):
        assert count <= 256 * 1024
        if self.fail_after is not None and self.reads >= self.fail_after:
            raise ConnectionResetError('simulated interrupted socket')
        self.reads += 1
        chunk, self.data = self.data[:count], self.data[count:]
        return chunk


class Connection:
    sock = None

    def __init__(self, response):
        self.response, self.requests, self.closed = response, [], False

    def request(self, method, target, headers):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


@pytest.fixture
def network(monkeypatch):
    responses, calls = [], []
    monkeypatch.setattr(ingestion, '_resolve_public', lambda host, deadline: [('public-record', host)])

    def connect(host, records, deadline):
        response = responses.pop(0)
        if callable(response):
            response = response()
        connection = Connection(response)
        calls.append((host, connection))
        return connection

    monkeypatch.setattr(ingestion, '_connection', connect)
    # Keep retry tests fast without bypassing cancellation checkpoints.
    monkeypatch.setattr(transport.time, 'sleep', lambda seconds: None)
    return responses, calls


def download(destination, **kwargs):
    return transport.download_resumable(URL, destination, max_bytes=2_000_000,
        progress=kwargs.pop('progress', lambda value: None),
        checkpoint=kwargs.pop('checkpoint', lambda: None), **kwargs)


def partial_state(destination, data, validator=None, total=len(MEDIA)):
    destination.with_name(destination.name + '.part').write_bytes(data)
    destination.with_name(destination.name + '.part.json').write_text(json.dumps({
        'url_hash': hashlib.sha256(URL.encode()).hexdigest(),
        'validator': validator or {'header': 'ETag', 'value': '"version-1"'}, 'total': total}))


def test_streams_bounded_chunks_and_redacts_credentials(tmp_path, network):
    responses, calls = network
    responses.append(Response(headers={'Content-Length': str(len(MEDIA))}))
    progress = []
    result = download(tmp_path / 'source', progress=progress.append,
                      expected_md5=hashlib.md5(MEDIA).hexdigest(), expected_size=len(MEDIA))
    assert (tmp_path / 'source').read_bytes() == MEDIA
    assert result['sha256'] == hashlib.sha256(MEDIA).hexdigest()
    assert result['size_bytes'] == len(MEDIA)
    assert progress[0]['received_bytes'] == 0 and progress[-1]['received_bytes'] == len(MEDIA)
    assert len(progress) >= 4 and calls[0][1].closed
    assert not list(tmp_path.glob('*.part*')) and 'PRIVATE' not in str(result)


def test_real_httpresponse_eof_does_not_touch_closed_socket(tmp_path, network):
    class ClosedSensitiveSocket:
        def __init__(self):
            self.fp = io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\n'
                + f'Content-Length: {len(MEDIA)}\r\n'.encode() + b'Connection: close\r\n\r\n' + MEDIA)

        def makefile(self, *args, **kwargs):
            return self.fp

        def settimeout(self, timeout):
            assert not self.fp.closed, 'attempted to configure a socket after HTTPResponse closed it'

    sock = ClosedSensitiveSocket()
    response = http.client.HTTPResponse(sock)
    response.begin()
    network[0].append(response)
    original = ingestion._connection

    def connection(host, records, deadline):
        result = original(host, records, deadline)
        result._transport_socket = sock
        return result

    from unittest.mock import patch
    with patch.object(ingestion, '_connection', connection):
        result = download(tmp_path / 'source')
    assert result['size_bytes'] == len(MEDIA)


def test_interrupt_resumes_exact_range(tmp_path, network):
    responses, calls = network
    chunk = 256 * 1024
    responses.extend([
        Response(headers={'Content-Length': str(len(MEDIA))}, fail_after=1),
        Response(MEDIA[chunk:], 206, {'Content-Length': str(len(MEDIA) - chunk),
            'Content-Range': f'bytes {chunk}-{len(MEDIA)-1}/{len(MEDIA)}'}),
    ])
    result = download(tmp_path / 'source')
    assert result['sha256'] == hashlib.sha256(MEDIA).hexdigest()
    assert calls[1][1].requests[0][2]['Range'] == f'bytes={chunk}-'
    assert calls[1][1].requests[0][2]['If-Range'] == '"version-1"'


def test_cancel_preserves_partial_and_next_call_resumes(tmp_path, network):
    responses, calls = network
    responses.append(Response(headers={'Content-Length': str(len(MEDIA))}))
    destination = tmp_path / 'source'

    class Cancelled(RuntimeError):
        pass

    def checkpoint():
        partial = destination.with_name('source.part')
        if partial.exists() and partial.stat().st_size:
            raise Cancelled()

    with pytest.raises(Cancelled):
        download(destination, checkpoint=checkpoint)
    chunk = 256 * 1024
    assert destination.with_name('source.part').stat().st_size == chunk
    assert 'PRIVATE' not in destination.with_name('source.part.json').read_text()
    responses.append(Response(MEDIA[chunk:], 206, {'Content-Length': str(len(MEDIA)-chunk),
        'Content-Range': f'bytes {chunk}-{len(MEDIA)-1}/{len(MEDIA)}'}))
    download(destination)
    assert destination.read_bytes() == MEDIA and len(calls) == 2


@pytest.mark.parametrize('headers', [{'ETag': None}, {'ETag': 'W/"weak"'}])
def test_no_strong_validator_restarts_after_interruption(tmp_path, network, headers):
    responses, calls = network
    responses.extend([Response(headers={**headers, 'Content-Length': str(len(MEDIA))}, fail_after=1),
                      Response(headers={**headers, 'Content-Length': str(len(MEDIA))})])
    download(tmp_path / 'source')
    assert 'Range' not in calls[1][1].requests[0][2]
    assert (tmp_path / 'source').read_bytes() == MEDIA


def test_last_modified_allows_resume(tmp_path, network):
    responses, calls = network
    destination = tmp_path / 'source'
    modified = 'Wed, 21 Oct 2026 07:28:00 GMT'
    partial_state(destination, MEDIA[:100], {'header': 'Last-Modified', 'value': modified})
    responses.append(Response(MEDIA[100:], 206, {'ETag': 'W/"weak"', 'Last-Modified': modified,
        'Content-Length': str(len(MEDIA)-100), 'Content-Range': f'bytes 100-{len(MEDIA)-1}/{len(MEDIA)}'}))
    download(destination)
    assert calls[0][1].requests[0][2]['If-Range'] == modified


def test_200_after_range_safely_restarts(tmp_path, network):
    destination = tmp_path / 'source'
    partial_state(destination, MEDIA[:100])
    network[0].append(Response(headers={'Content-Length': str(len(MEDIA)), 'ETag': '"new-version"'}))
    download(destination)
    assert destination.read_bytes() == MEDIA


def test_changed_validator_on_206_restarts(tmp_path, network):
    destination = tmp_path / 'source'
    partial_state(destination, MEDIA[:100])
    network[0].extend([Response(MEDIA[100:], 206, {'ETag': '"new"',
        'Content-Length': str(len(MEDIA)-100), 'Content-Range': f'bytes 100-{len(MEDIA)-1}/{len(MEDIA)}'}),
        Response(headers={'ETag': '"new"', 'Content-Length': str(len(MEDIA))})])
    download(destination)
    assert 'Range' not in network[1][1][1].requests[0][2]
    assert destination.read_bytes() == MEDIA


@pytest.mark.parametrize('content_range', ['bytes 99-659999/660012', 'nonsense', 'bytes 100-100/1'])
def test_mismatched_content_range_rejected(tmp_path, network, content_range):
    destination = tmp_path / 'source'
    partial_state(destination, MEDIA[:100])
    network[0].append(Response(MEDIA[100:], 206, {'Content-Range': content_range}))
    with pytest.raises(ingestion.IngestionError, match='partial response|byte range'):
        download(destination)
    assert not destination.exists()


def test_416_completed_partial_is_verified(tmp_path, network):
    destination = tmp_path / 'source'
    partial_state(destination, MEDIA)
    network[0].append(Response(b'', 416, {'Content-Range': f'bytes */{len(MEDIA)}'}))
    result = download(destination)
    assert result['sha256'] == hashlib.sha256(MEDIA).hexdigest()


def test_416_without_matching_validator_restarts(tmp_path, network):
    destination = tmp_path / 'source'
    partial_state(destination, MEDIA)
    network[0].extend([Response(b'', 416, {'ETag': '"new"', 'Content-Range': f'bytes */{len(MEDIA)}'}), Response()])
    download(destination)
    assert 'Range' not in network[1][1][1].requests[0][2]


def test_unknown_length_exceeding_limit_never_completes(tmp_path, network):
    network[0].append(Response(MEDIA * 4))
    with pytest.raises(ingestion.IngestionError, match='size limit'):
        download(tmp_path / 'source')
    assert not (tmp_path / 'source').exists()
    assert (tmp_path / 'source.part').stat().st_size <= 2_000_000


@pytest.mark.parametrize('status', [401, 403, 404])
def test_revoked_or_missing_media_does_not_retry(tmp_path, network, status):
    network[0].append(Response(b'', status))
    with pytest.raises(ingestion.IngestionError, match=f'HTTP {status}'):
        download(tmp_path / 'source')
    assert len(network[1]) == 1


def test_transient_http_retries(tmp_path, network):
    network[0].extend([Response(b'', 503), Response()])
    download(tmp_path / 'source')
    assert len(network[1]) == 2


def test_checksum_mismatch_does_not_leave_reusable_corrupt_partial(tmp_path, network):
    network[0].append(Response())
    with pytest.raises(ingestion.IngestionError, match='integrity'):
        download(tmp_path / 'source', expected_md5='0' * 32)
    assert not list(tmp_path.glob('source*'))


def test_authorization_is_restricted_to_drive_api(tmp_path, network):
    with pytest.raises(ingestion.IngestionError, match='approved Google Drive'):
        download(tmp_path / 'source', headers_factory=lambda: {'Authorization': 'Bearer PRIVATE'},
                 allowed_auth_host='www.googleapis.com')
    assert not network[1]


def test_drive_auth_cannot_cross_redirect_host(tmp_path, network):
    network[0].append(Response(b'', 302, {'Location': 'https://other.example/video.mp4'}))
    with pytest.raises(ingestion.IngestionError, match='outside its authorized'):
        transport.download_resumable('https://www.googleapis.com/drive/v3/files/123?alt=media',
            tmp_path / 'source', max_bytes=2_000_000, progress=lambda value: None,
            checkpoint=lambda: None, headers_factory=lambda: {'Authorization': 'Bearer PRIVATE'},
            allowed_auth_host='www.googleapis.com')
    assert len(network[1]) == 1


def test_every_redirect_revalidates_dns(tmp_path, network, monkeypatch):
    hosts = []

    def resolve(host, deadline):
        hosts.append(host)
        if host == 'private.example':
            raise ingestion.IngestionError('Private internet addresses are blocked.')
        return []

    monkeypatch.setattr(ingestion, '_resolve_public', resolve)
    network[0].append(Response(b'', 302, {'Location': 'https://private.example/video.mp4'}))
    with pytest.raises(ingestion.IngestionError, match='blocked'):
        download(tmp_path / 'source')
    assert hosts == ['media.example', 'private.example']


def test_metadata_uses_head_and_sanitizes_filename(network):
    network[0].append(Response(b'', headers={'Content-Length': '123',
        'Content-Disposition': 'attachment; filename="../../creator.mp4"'}))
    metadata = transport.probe_metadata(URL)
    assert metadata['size_bytes'] == 123 and metadata['downloadable']
    assert '/' not in metadata['filename'] and 'PRIVATE' not in str(metadata)
    assert network[1][0][1].requests[0][0] == 'HEAD'


def test_metadata_rejects_youtube_without_network(network):
    with pytest.raises(ingestion.IngestionError, match='YouTube'):
        transport.probe_metadata('https://youtube.com/watch?v=abcdefghijk')
    assert not network[1]


def test_existing_final_file_is_preserved(tmp_path, network):
    destination = tmp_path / 'source'
    destination.write_bytes(b'preserve this')
    with pytest.raises(ingestion.IngestionError, match='already exists'):
        download(destination)
    assert destination.read_bytes() == b'preserve this' and not network[1]


def test_temporary_dns_failure_retries_without_weakening_policy(tmp_path, network, monkeypatch):
    attempts = []

    def resolve(host, deadline):
        attempts.append(host)
        if len(attempts) == 1:
            raise ingestion.IngestionError('The video host could not be resolved in time.')
        return []

    monkeypatch.setattr(ingestion, '_resolve_public', resolve)
    network[0].append(Response())
    download(tmp_path / 'source')
    assert attempts == ['media.example', 'media.example']


def test_storage_checkpoint_stops_before_write(tmp_path, network):
    network[0].append(Response())
    checks = []

    def checkpoint():
        checks.append(True)
        if len(checks) == 3:
            raise ingestion.IngestionError('Workspace storage exhausted')

    with pytest.raises(ingestion.IngestionError, match='storage'):
        download(tmp_path / 'source', checkpoint=checkpoint)
    assert (tmp_path / 'source.part').stat().st_size == 0
    assert not (tmp_path / 'source').exists()


def test_cancel_during_retry_backoff_keeps_partial(tmp_path, network):
    destination = tmp_path / 'source'
    partial_state(destination, MEDIA[:100])
    network[0].append(Response(b'', 503))
    checks = []

    def checkpoint():
        checks.append(True)
        if len(checks) == 2:
            raise RuntimeError('User cancelled')

    with pytest.raises(RuntimeError, match='cancelled'):
        download(destination, checkpoint=checkpoint)
    assert (tmp_path / 'source.part').read_bytes() == MEDIA[:100]
    assert (tmp_path / 'source.part.json').exists()
    assert len(network[1]) == 1


def test_expected_size_failure_cannot_complete_oversized_unknown_length(tmp_path, network):
    network[0].append(Response())
    with pytest.raises(ingestion.IngestionError, match='size limit'):
        download(tmp_path / 'source', expected_size=100)
    assert not (tmp_path / 'source').exists()


def test_hashing_checks_cancellation_before_publish(tmp_path, network):
    destination = tmp_path / 'source'
    network[0].append(Response())
    def checkpoint():
        partial = destination.with_name('source.part')
        if partial.exists() and partial.stat().st_size == len(MEDIA):
            raise RuntimeError('User cancelled')

    with pytest.raises(RuntimeError, match='cancelled'):
        download(destination, checkpoint=checkpoint)
    assert not destination.exists() and (tmp_path / 'source.part').stat().st_size == len(MEDIA)


@pytest.mark.parametrize('headers,data', [
    ({'Content-Type': 'text/html'}, b'web page'),
    ({'Content-Type': 'video/mp4'}, b'<!doctype html><html>not media'),
    ({'Content-Encoding': 'gzip'}, MEDIA),
    ({'Content-Length': 'invalid'}, MEDIA),
], ids=['html-type', 'html-prefix', 'compressed', 'invalid-length'])
def test_unsafe_content_responses_rejected(tmp_path, network, headers, data):
    network[0].append(Response(data, headers=headers))
    with pytest.raises(ingestion.IngestionError):
        download(tmp_path / 'source')
    assert not (tmp_path / 'source').exists() and len(network[1]) == 1


def test_drive_401_refreshes_exactly_once_without_losing_range(tmp_path, network):
    responses, calls = network
    responses.extend([Response(b'', 401), Response()])
    refreshes = []

    def headers_factory(force_refresh=False):
        refreshes.append(force_refresh)
        return {'Authorization': 'Bearer refreshed' if force_refresh else 'Bearer initial'}

    transport.download_resumable('https://www.googleapis.com/drive/v3/files/123?alt=media',
        tmp_path / 'source', max_bytes=2_000_000, progress=lambda value: None,
        checkpoint=lambda: None, headers_factory=headers_factory, allowed_auth_host='www.googleapis.com')
    assert refreshes == [False, True]
    assert calls[1][1].requests[0][2]['Authorization'] == 'Bearer refreshed'


def test_drive_still_401_after_refresh_is_terminal(tmp_path, network):
    network[0].extend([Response(b'', 401), Response(b'', 401)])

    def headers_factory(force_refresh=False):
        return {'Authorization': 'Bearer initial'}

    with pytest.raises(ingestion.IngestionError, match='HTTP 401'):
        transport.download_resumable('https://www.googleapis.com/drive/v3/files/123?alt=media',
            tmp_path / 'source', max_bytes=2_000_000, progress=lambda value: None,
            checkpoint=lambda: None, headers_factory=headers_factory, allowed_auth_host='www.googleapis.com')
    assert len(network[1]) == 2


def test_progress_budget_check_runs_before_actual_write(tmp_path, network):
    destination = tmp_path / 'source'
    network[0].append(Response())

    def progress(value):
        if value['received_bytes']:
            assert (tmp_path / 'source.part').stat().st_size < value['received_bytes']
            raise ingestion.IngestionError('Quota exhausted')

    with pytest.raises(ingestion.IngestionError, match='Quota'):
        download(destination, progress=progress)
    assert (tmp_path / 'source.part').stat().st_size == 0


@pytest.mark.parametrize('phase', ['headers', 'body'])
def test_blocked_network_cancels_promptly_and_reraises_original_pause(tmp_path, network, monkeypatch, phase):
    destination = tmp_path / 'source'
    partial_state(destination, MEDIA[:100])
    blocked, aborted = threading.Event(), threading.Event()
    pause = ingestion.IngestionError('Paused by caller with original exception identity')

    class BlockedResponse(Response):
        def read1(self, count):
            blocked.set()
            assert aborted.wait(5), 'Cancellation did not abort the blocking read'
            raise ConnectionResetError('Socket aborted')

    class BlockedConnection(Connection):
        def getresponse(self):
            if phase == 'headers':
                blocked.set()
                assert aborted.wait(5), 'Cancellation did not abort the blocking header read'
                raise ConnectionResetError('Socket aborted')
            return self.response

        def close(self):
            super().close()
            aborted.set()

    connections = []

    def connect(host, records, deadline):
        result = BlockedConnection(BlockedResponse(MEDIA[100:], 206,
            {'Content-Range': f'bytes 100-{len(MEDIA)-1}/{len(MEDIA)}',
             'Content-Length': str(len(MEDIA)-100)}))
        connections.append(result)
        return result

    monkeypatch.setattr(ingestion, '_connection', connect)

    def checkpoint():
        if blocked.is_set():
            raise pause

    started = time.monotonic()
    with pytest.raises(ingestion.IngestionError) as error:
        download(destination, checkpoint=checkpoint)
    assert error.value is pause
    assert time.monotonic() - started < 1.5
    assert len(connections) == 1 and connections[0].closed
    assert (tmp_path / 'source.part').read_bytes() == MEDIA[:100]
    assert (tmp_path / 'source.part.json').exists()
    assert not destination.exists()


def test_checkpoint_monitor_serializes_caller_callbacks(tmp_path, network):
    network[0].append(Response())
    held = threading.Lock()
    checks = []

    def checkpoint():
        assert held.acquire(blocking=False), 'Concurrent storage/cancellation checkpoints'
        try:
            checks.append(threading.current_thread().name)
            # Event.wait keeps this test independent of the network fixture's
            # accelerated retry sleeps and lets the monitor contend for the lock.
            threading.Event().wait(.06)
        finally:
            held.release()

    def progress(value):
        checkpoint()

    download(tmp_path / 'source', checkpoint=checkpoint, progress=progress)
    assert 'framecraft-import-cancel' in checks


@pytest.mark.parametrize('head_status', [405, 501])
def test_metadata_head_unsupported_falls_back_to_one_byte_range(network, head_status):
    responses, calls = network
    responses.extend([Response(b'', head_status), Response(MEDIA[:1], 206,
        {'Content-Range': f'bytes 0-0/{len(MEDIA)}', 'Content-Length': '1'})])
    result = transport.probe_metadata(URL)
    assert result['size_bytes'] == len(MEDIA) and result['downloadable']
    assert calls[0][1].requests[0][0] == 'HEAD'
    assert calls[1][1].requests[0][0] == 'GET'
    assert calls[1][1].requests[0][2]['Range'] == 'bytes=0-0'
    assert all(connection.closed for _, connection in calls)


def test_metadata_range_ignored_does_not_consume_large_body(network):
    class DoNotReadResponse(Response):
        def read1(self, count):
            pytest.fail('Discovery attempted to read media bytes')

    network[0].extend([Response(b'', 405), DoNotReadResponse(headers={'Content-Length': '9000000000'})])
    metadata = transport.probe_metadata(URL)
    assert metadata['size_bytes'] == 9_000_000_000
    assert all(connection.closed for _, connection in network[1])


@pytest.mark.parametrize('headers', [
    {'Content-Range': 'bytes 1-1/9000', 'Content-Length': '1'},
    {'Content-Range': 'bytes 0-0/*', 'Content-Length': '1'},
    {'Content-Range': 'bytes 0-0/0', 'Content-Length': '1'},
    {'Content-Range': 'bytes 0-0/9000', 'Content-Length': '500'},
])
def test_metadata_range_fallback_requires_exact_valid_range(network, headers):
    network[0].extend([Response(b'', 405), Response(b'x', 206, headers)])
    with pytest.raises(ingestion.IngestionError, match='discovery byte range'):
        transport.probe_metadata(URL)
    assert all(connection.closed for _, connection in network[1])


def test_metadata_range_fallback_preserves_revoked_status(network):
    network[0].extend([Response(b'', 405), Response(b'', 403)])
    with pytest.raises(ingestion.IngestionError, match='HTTP 403'):
        transport.probe_metadata(URL)
    assert len(network[1]) == 2


def test_metadata_range_fallback_revalidates_redirect_addresses(network, monkeypatch):
    hosts = []

    def resolve(host, deadline):
        hosts.append(host)
        if host == 'private.example':
            raise ingestion.IngestionError('Private internet addresses are blocked.')
        return []

    monkeypatch.setattr(ingestion, '_resolve_public', resolve)
    network[0].extend([Response(b'', 501), Response(b'', 302,
        {'Location': 'https://private.example/secret.mp4'})])
    with pytest.raises(ingestion.IngestionError, match='blocked'):
        transport.probe_metadata(URL)
    assert hosts == ['media.example', 'media.example', 'private.example']
