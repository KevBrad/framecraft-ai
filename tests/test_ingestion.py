"""Import boundary tests: no network calls and no modifications to user data."""
import hashlib
import io
import ipaddress
from pathlib import Path
import socket
import threading
import time

import pytest
from backend import ingestion


def address(ip='93.184.216.34'):
    family = socket.AF_INET6 if ':' in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (ip, 443))


class Response:
    def __init__(self, data=b'\x00\x00\x00\x18ftypmp42real-media-bytes', status=200, headers=None):
        self.data, self.status = data, status
        self.headers = {'Content-Type': 'video/mp4', **(headers or {})}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, count):
        chunk, self.data = self.data[:count], self.data[count:]
        return chunk

    def isclosed(self):
        return not self.data


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
def transport(monkeypatch):
    calls, replies = [], []
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args: [address()])

    def make(host, records, deadline):
        connection = Connection(replies.pop(0))
        calls.append((host, records, connection))
        return connection

    monkeypatch.setattr(ingestion, '_connection', make)
    return replies, calls


def test_signed_file_streamed_hash_and_safe_provenance(tmp_path, transport):
    replies, calls = transport
    media = b'\x00\x00\x00\x18ftypmp42' + b'a' * 600_000
    replies.append(Response(media, headers={'Content-Length': str(len(media))}))
    budgets, stages = [], []
    destination = tmp_path / 'source'
    result = ingestion.download_media('https://media.example/original.mp4?signature=SECRET', destination,
        max_bytes=700_000, check_budget=budgets.append, report=stages.append)
    assert destination.read_bytes() == media
    assert result['sha256'] == hashlib.sha256(media).hexdigest()
    assert result['size_bytes'] == len(media)
    assert result['provenance']['source_host'] == 'media.example'
    assert 'SECRET' not in str(result) and 'signature' not in str(result)
    assert calls[0][2].requests[0][1].endswith('?signature=SECRET')
    assert calls[0][2].closed and budgets[0] == 0 and budgets[-1] == len(media)
    assert len(budgets) >= 4  # Incremental writes, including absent/unknown lengths.


@pytest.mark.parametrize('url', [
    'http://media.example/video.mp4', 'file:///C:/private',
    'https://user:pass@media.example/file.mp4', 'https://media.example:8443/file.mp4',
    'https://media.example/file.mp4#fragment', 'https://media.example/\r\nHeader:value',
    'https://media.example\\@127.0.0.1/source.mp4',
])
def test_invalid_urls_rejected_before_network(tmp_path, monkeypatch, url):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args: pytest.fail('Invalid URL reached DNS'))
    with pytest.raises(ingestion.IngestionError):
        ingestion.download_media(url, tmp_path / 'source', max_bytes=100)
    assert not (tmp_path / 'source').exists()


@pytest.mark.parametrize('host', ['youtube.com', 'www.youtube.com', 'youtu.be', 'rr1.googlevideo.com'])
def test_youtube_import_has_actionable_explanation(tmp_path, host):
    with pytest.raises(ingestion.IngestionError, match='YouTube Studio or Google Takeout'):
        ingestion.download_media(f'https://{host}/watch?v=abcdefghijk', tmp_path / 'source', max_bytes=100)


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1',
    'fc00::1', 'fe80::1', '::ffff:127.0.0.1', '64:ff9b::a00:1', '168.63.129.16'])
def test_mixed_dns_records_reject_private_and_translation_addresses(monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args: [address(), address(ip)])
    with pytest.raises(ingestion.IngestionError, match='public internet addresses'):
        ingestion._resolve_public('media.example', time.monotonic() + 5)


def test_redirect_destination_is_resolved_and_checked_again(tmp_path, transport, monkeypatch):
    replies, calls = transport
    replies.append(Response(status=302, headers={'Location': 'https://internal.example/secret'}))
    seen = []

    def resolve(host, *args):
        seen.append(host)
        return [address('127.0.0.1' if host == 'internal.example' else '93.184.216.34')]

    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    with pytest.raises(ingestion.IngestionError, match='public internet addresses'):
        ingestion.download_media('https://media.example/video', tmp_path / 'source', max_bytes=100)
    assert seen == ['media.example', 'internal.example']
    assert len(calls) == 1 and calls[0][2].closed
    assert not (tmp_path / 'source').exists()


def test_relative_redirect_and_final_host_are_supported(tmp_path, transport):
    replies, calls = transport
    replies.extend([Response(status=302, headers={'Location': '/actual/video.webm?token=HIDDEN'}),
                    Response(headers={'Content-Type': 'video/webm'})])
    result = ingestion.download_media('https://media.example/start', tmp_path / 'source', max_bytes=100)
    assert len(calls) == 2 and result['filename'].endswith('.webm')
    assert 'HIDDEN' not in str(result)


@pytest.mark.parametrize('response', [
    Response(headers={'Content-Length': '10000'}),
    Response(headers={'Content-Length': '2'}),
    Response(headers={'Content-Length': '-1'}),
    Response(headers={'Content-Type': 'text/html'}),
    Response(b'<!doctype html><html>Login</html>', headers={'Content-Type': 'application/octet-stream'}),
    Response(b'#EXTM3U\nhttp://internal.example/a.ts', headers={'Content-Type': 'video/mp4'}),
    Response(headers={'Content-Encoding': 'gzip'}), Response(b''),
])
def test_invalid_or_incomplete_response_leaves_no_source(tmp_path, transport, response):
    replies, _ = transport
    replies.append(response)
    with pytest.raises(ingestion.IngestionError):
        ingestion.download_media('https://media.example/file.mp4', tmp_path / 'source', max_bytes=100)
    assert not (tmp_path / 'source').exists()


def test_missing_content_length_still_enforces_cap(tmp_path, transport):
    replies, _ = transport
    replies.append(Response(b'\x00ftyp' + b'a' * 500))
    with pytest.raises(ingestion.IngestionError, match='import limit'):
        ingestion.download_media('https://media.example/file.mp4', tmp_path / 'source', max_bytes=100)
    assert not (tmp_path / 'source').exists()


def test_budget_denial_mid_import_removes_partial_file(tmp_path, transport):
    replies, _ = transport
    replies.append(Response(b'\x00ftyp' + b'a' * 500_000))

    def budget(size):
        if size > 300_000:
            raise RuntimeError('SECRET/local/private/path')

    with pytest.raises(ingestion.IngestionError, match='storage') as error:
        ingestion.download_media('https://media.example/file.mp4', tmp_path / 'source', max_bytes=600_000,
            check_budget=budget)
    assert 'SECRET' not in str(error.value) and not (tmp_path / 'source').exists()


def test_existing_source_is_never_overwritten(tmp_path, transport):
    replies, _ = transport
    replies.append(Response())
    source = tmp_path / 'source'
    source.write_bytes(b'user original')
    with pytest.raises(ingestion.IngestionError, match='already exists'):
        ingestion.download_media('https://media.example/file.mp4', source, max_bytes=100)
    assert source.read_bytes() == b'user original'


def test_redirect_loop_is_bounded(tmp_path, transport):
    replies, calls = transport
    replies.extend(Response(status=302, headers={'Location': '/loop'}) for _ in range(5))
    with pytest.raises(ingestion.IngestionError, match='redirects too many'):
        ingestion.download_media('https://media.example/loop', tmp_path / 'source', max_bytes=100)
    assert len(calls) == 5


def test_connect_uses_pinned_ip_and_verifies_original_tls_hostname(monkeypatch):
    used = []

    class RawSocket:
        def settimeout(self, seconds):
            pass

        def connect(self, sockaddr):
            used.append(('connect', sockaddr))

        def close(self):
            pass

    class Context:
        def wrap_socket(self, raw, server_hostname):
            used.append(('tls_hostname', server_hostname))
            return raw

    monkeypatch.setattr(socket, 'socket', lambda *args: RawSocket())
    connection = ingestion._PinnedHTTPSConnection('media.example', [address()], time.monotonic() + 5)
    assert connection._context.check_hostname and connection._context.verify_mode == ingestion.ssl.CERT_REQUIRED
    connection._context = Context()
    connection.connect()
    assert used == [('connect', ('93.184.216.34', 443)), ('tls_hostname', 'media.example')]
    connection.close()


@pytest.mark.parametrize('detached', [False, True])
def test_absolute_deadline_aborts_slow_response_and_cleans_partial(tmp_path, transport, monkeypatch, detached):
    stopped = threading.Event()

    class Socket:
        def settimeout(self, seconds):
            pass

        def shutdown(self, how):
            stopped.set()

    class SlowResponse(Response):
        def read1(self, count):
            assert stopped.wait(1), 'Deadline watchdog did not abort the transport'
            raise socket.timeout('secret signed URL must not escape')

    connection = Connection(SlowResponse())
    if detached:
        connection._transport_socket = Socket()
    else:
        connection.sock = Socket()
    monkeypatch.setattr(ingestion, '_connection', lambda *args: connection)
    began = time.monotonic()
    with pytest.raises(ingestion.IngestionError) as error:
        ingestion.download_media('https://media.example/file?token=SECRET', tmp_path / 'source',
            max_bytes=100, timeout_seconds=.04)
    assert time.monotonic() - began < 1
    assert 'SECRET' not in str(error.value) and not (tmp_path / 'source').exists()
    assert stopped.is_set() and connection.closed


def test_tls_certificate_rejection_is_safe_and_does_not_retry_unpinned_dns(monkeypatch):
    connected, closed = [], []

    class RawSocket:
        def settimeout(self, seconds):
            pass

        def connect(self, sockaddr):
            connected.append(sockaddr)

        def close(self):
            closed.append(True)

    class Context:
        def wrap_socket(self, raw, server_hostname):
            raise ingestion.ssl.SSLCertVerificationError('private certificate details')

    monkeypatch.setattr(socket, 'socket', lambda *args: RawSocket())
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args: pytest.fail('Pinned connect performed DNS again'))
    connection = ingestion._PinnedHTTPSConnection('media.example', [address()], time.monotonic() + 5)
    connection._context = Context()
    with pytest.raises(ingestion.IngestionError, match='TLS certificate') as error:
        connection.connect()
    assert connected == [('93.184.216.34', 443)] and closed
    assert 'private certificate details' not in str(error.value)


@pytest.mark.parametrize('declared_length', [True, False])
def test_successful_eof_does_not_configure_a_closed_detached_socket(tmp_path, transport, monkeypatch, declared_length):
    media = b'\x00\x00\x00\x18ftypmp42' + b'x' * (ingestion._CHUNK + 20)
    socket_state = {'closed': False, 'timeout_updates': 0}

    class DetachedSocket:
        def settimeout(self, seconds):
            if socket_state['closed']:
                raise OSError(10038, 'An operation was attempted on something that is not a socket')
            socket_state['timeout_updates'] += 1

    class ClosingResponse(Response):
        def read1(self, count):
            chunk = super().read1(count)
            if not self.data:
                # HTTPResponse closes its file when Content-Length is consumed,
                # or after observing EOF on a close-delimited body.
                socket_state['closed'] = True
            return chunk

    headers = {'Content-Length': str(len(media))} if declared_length else {}
    connection = Connection(ClosingResponse(media, headers=headers))
    connection._transport_socket = DetachedSocket()
    monkeypatch.setattr(ingestion, '_connection', lambda *args: connection)
    source = tmp_path / 'source'
    result = ingestion.download_media('https://media.example/video.mp4', source, max_bytes=len(media) + 1)
    assert source.read_bytes() == media and result['sha256'] == hashlib.sha256(media).hexdigest()
    assert socket_state['closed'] and socket_state['timeout_updates'] == 2
    assert connection.closed


@pytest.mark.parametrize('declared_length', [True, False])
def test_actual_http_response_eof_closes_detached_transport_cleanly(tmp_path, transport, monkeypatch, declared_length):
    media = b'\x00\x00\x00\x18ftypmp42' + b'z' * (ingestion._CHUNK + 20)
    state = {'closed': False}

    class ResponseFile(io.BytesIO):
        def close(self):
            state['closed'] = True
            super().close()

    class Socket:
        def makefile(self, mode):
            length_header = f'Content-Length: {len(media)}\r\n' if declared_length else ''
            return ResponseFile(('HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: video/mp4\r\n'
                + length_header + '\r\n').encode('ascii') + media)

        def settimeout(self, seconds):
            if state['closed']:
                raise OSError(10038, 'Operation attempted on something that is not a socket')

    detached = Socket()
    response = ingestion.http.client.HTTPResponse(detached)
    response.begin()
    assert response.will_close and not response.isclosed()
    connection = Connection(response)
    connection._transport_socket = detached
    monkeypatch.setattr(ingestion, '_connection', lambda *args: connection)
    source = tmp_path / 'source'
    result = ingestion.download_media('https://media.example/video.mp4', source, max_bytes=len(media) + 1)
    assert state['closed'] and response.isclosed()
    assert source.read_bytes() == media and result['size_bytes'] == len(media)
