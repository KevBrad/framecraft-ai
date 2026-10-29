"""Resumable, DNS-pinned HTTPS imports. No network URL reaches FFmpeg."""
from __future__ import annotations

from datetime import datetime, timezone
from email.message import Message
import hashlib
import http.client
import inspect
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import unquote, urljoin

from . import ingestion

IngestionError = ingestion.IngestionError
_CHUNK = 256 * 1024
_REDIRECTS = (301, 302, 303, 307, 308)
_RETRY_STATUS = (408, 425, 429, 500, 502, 503, 504)


class _RetryError(IngestionError):
    pass


class _CheckpointMonitor:
    """Interrupt blocking headers/reads while preserving the caller's pause error.

    Callbacks are serialized with progress callbacks so DB/storage checkpoints
    never run concurrently. Only the owning import worker writes media bytes.
    """
    def __init__(self, callback):
        self.callback = callback
        self.callback_lock = threading.RLock()
        self.stopped = threading.Event()
        self.connection = None
        self.error = None
        self.thread = threading.Thread(target=self._watch, name='framecraft-import-cancel', daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stopped.set()
        self.thread.join(timeout=1)

    def raise_error(self):
        if self.error is not None:
            raise self.error

    def check(self):
        self.raise_error()
        with self.callback_lock:
            self.raise_error()
            self.callback()

    def progress(self, callback, value):
        self.raise_error()
        with self.callback_lock:
            self.raise_error()
            callback(value)

    def set_connection(self, connection):
        self.connection = connection
        if self.error is not None:
            ingestion._abort_connection(connection)
            self.raise_error()

    def _watch(self):
        while not self.stopped.wait(.2):
            try:
                self.check()
            except BaseException as exc:
                self.error = exc
                connection = self.connection
                if connection is not None:
                    ingestion._abort_connection(connection)
                return


def _length(value):
    if value is None:
        return None
    if not re.fullmatch(r'[0-9]+', value.strip()):
        raise IngestionError('The media host returned an invalid file size.')
    return int(value)


def _validator(response):
    etag = (response.getheader('ETag', '') or '').strip()
    if etag and not etag.startswith('W/') and re.fullmatch(r'"[^"\r\n]*"', etag):
        return {'header': 'ETag', 'value': etag}
    modified = (response.getheader('Last-Modified', '') or '').strip()
    if modified and len(modified) < 128 and not re.search(r'[\r\n]', modified):
        return {'header': 'Last-Modified', 'value': modified}
    return None


def _metadata(response, url, initial_host, host):
    parts, _ = ingestion._parse_url(url)
    content_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
    downloadable = bool(content_type.startswith('video/') or content_type in (
        'application/octet-stream', 'binary/octet-stream'))
    message = Message()
    message['Content-Disposition'] = response.getheader('Content-Disposition', '')
    filename = message.get_filename() or unquote(parts.path.rsplit('/', 1)[-1]) or 'Imported video.mp4'
    filename = re.sub(r'[\x00-\x1f\x7f/\\:<>"|?*]', '_', str(filename))[-180:]
    if not filename or filename in ('.', '..'):
        filename = 'Imported video.mp4'
    return {'filename': filename, 'content_type': content_type,
            'size_bytes': _length(response.getheader('Content-Length')),
            'source_host': initial_host, 'final_host': host, 'downloadable': downloadable}


def _open(url, method, deadline, extra_headers=None, headers_factory=None, allowed_auth_host=None,
          force_refresh=False, monitor=None):
    """Revalidate DNS and pin each redirect; auth never crosses a host boundary."""
    current = url
    for redirect in range(5):
        parts, host = ingestion._parse_url(current)
        supplied = (headers_factory(force_refresh=True) if force_refresh else headers_factory()) if headers_factory is not None else {}
        force_refresh = False
        supplied = supplied or {}
        if supplied and (allowed_auth_host != 'www.googleapis.com' or host != allowed_auth_host):
            raise IngestionError('Authorized downloads are restricted to the approved Google Drive API host.')
        # Callers may supply only a Bearer token, never Host/Cookie/Range overrides.
        if any(key.lower() != 'authorization' for key in supplied):
            raise IngestionError('Unsupported authorized media headers.')
        if any(not isinstance(value, str) or '\r' in value or '\n' in value
               or not value.startswith('Bearer ') for value in supplied.values()):
            raise IngestionError('Invalid authorized media credentials.')
        try:
            records = ingestion._resolve_public(host, deadline)
            connection = ingestion._connection(host, records, deadline)
        except IngestionError as exc:
            # Keep SSRF/policy failures terminal; only connection/DNS availability
            # failures from the existing pinned transport may be retried.
            if str(exc).startswith(('The video host could not be resolved', 'Could not securely connect')):
                raise _RetryError('The media host is temporarily unreachable.') from None
            raise
        if monitor is not None:
            monitor.set_connection(connection)
        watchdog = threading.Timer(ingestion._remaining(deadline), ingestion._abort_connection, args=(connection,))
        watchdog.daemon = True
        watchdog.start()
        try:
            target = parts.path or '/'
            if parts.query:
                target += '?' + parts.query
            headers = {'Accept': 'video/*, application/octet-stream', 'Accept-Encoding': 'identity',
                       'User-Agent': 'Framecraft/2.0', 'Connection': 'close', **(extra_headers or {}), **supplied}
            connection.request(method, target, headers=headers)
            response = connection.getresponse()
            if response.status not in _REDIRECTS:
                return connection, response, watchdog, current, host
            location = response.getheader('Location')
            if not location or redirect == 4:
                raise IngestionError('The media link redirects too many times or has no destination.')
            next_url = urljoin(current, location)
            _, next_host = ingestion._parse_url(next_url)
            if supplied and next_host != host:
                raise IngestionError('Google Drive media redirected outside its authorized API host.')
            current = next_url
        except BaseException:
            watchdog.cancel()
            connection.close()
            raise
        watchdog.cancel()
        connection.close()
    raise IngestionError('The media link redirects too many times.')


def probe_metadata(url, *, timeout_seconds=30):
    """Inspect public headers only; this never claims a YouTube preview is media."""
    _, initial_host = ingestion._parse_url(url)
    deadline = time.monotonic() + timeout_seconds
    connection, response, watchdog, current, host = _open(url, 'HEAD', deadline)
    try:
        if response.status in (405, 501):
            # Some object stores expose GET but reject HEAD. Request one byte,
            # inspect only headers and close without reading even if Range was
            # ignored: discovery must never turn into a full video download.
            watchdog.cancel()
            getattr(response, 'close', lambda: None)()
            connection.close()
            connection, response, watchdog, current, host = _open(url, 'GET', deadline,
                extra_headers={'Range': 'bytes=0-0'})
            if response.status == 206:
                match = re.fullmatch(r'bytes 0-0/([0-9]+)', response.getheader('Content-Range', ''))
                if not match or int(match[1]) < 1:
                    raise IngestionError('The media host returned an invalid discovery byte range.')
                result = _metadata(response, current, initial_host, host)
                if result['size_bytes'] not in (None, 1):
                    raise IngestionError('The media host returned an invalid discovery byte range size.')
                result['size_bytes'] = int(match[1])
                if response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                    result['downloadable'] = False
                return result
        if response.status != 200:
            raise IngestionError(f'The media host returned HTTP {response.status} while inspecting this link.')
        result = _metadata(response, current, initial_host, host)
        if response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
            result['downloadable'] = False
        return result
    finally:
        watchdog.cancel()
        getattr(response, 'close', lambda: None)()
        connection.close()


def _save_state(sidecar, value):
    temporary = sidecar.with_suffix(sidecar.suffix + '.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(sidecar)


def _hash_file(path, checkpoint):
    sha256, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    with path.open('rb') as source:
        while True:
            checkpoint()
            chunk = source.read(_CHUNK)
            if not chunk:
                break
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest()


def download_resumable(url, destination, *, max_bytes, progress, checkpoint,
                       headers_factory=None, allowed_auth_host=None, expected_size=None,
                       expected_md5=None, timeout_seconds=900, max_attempts=3):
    """Download bounded chunks, preserving verified partials across interruptions.

    ``checkpoint`` checks cancellation/storage before each write and hash read.
    Progress dictionaries contain received_bytes, total_bytes, attempt and resumed.
    Sidecars contain a URL hash and validators, never the URL or credentials.
    """
    with _CheckpointMonitor(checkpoint) as monitor:
        try:
            return _download_resumable(url, destination, max_bytes=max_bytes,
                progress=lambda value: monitor.progress(progress, value), checkpoint=monitor.check,
                headers_factory=headers_factory, allowed_auth_host=allowed_auth_host,
                expected_size=expected_size, expected_md5=expected_md5,
                timeout_seconds=timeout_seconds, max_attempts=max_attempts, monitor=monitor)
        except BaseException:
            monitor.raise_error()
            raise


def _download_resumable(url, destination, *, max_bytes, progress, checkpoint,
                       headers_factory, allowed_auth_host, expected_size, expected_md5,
                       timeout_seconds, max_attempts, monitor):
    if max_bytes <= 0 or timeout_seconds <= 0 or max_attempts < 1:
        raise ValueError('Import limits must be positive.')
    if expected_size is not None and (not isinstance(expected_size, int) or expected_size <= 0 or expected_size > max_bytes):
        raise IngestionError('The source video exceeds the import size limit or has an invalid size.')
    if expected_md5 is not None and not re.fullmatch(r'[a-fA-F0-9]{32}', expected_md5):
        raise IngestionError('The source video has an invalid integrity checksum.')
    _, initial_host = ingestion._parse_url(url)
    destination = Path(destination)
    if destination.exists():
        raise IngestionError('The import destination already exists. Start a new import.')
    partial, sidecar = Path(str(destination) + '.part'), Path(str(destination) + '.part.json')
    fingerprint = hashlib.sha256(url.encode()).hexdigest()
    state = {}
    try:
        state = json.loads(sidecar.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    # Invalid/unvalidated partials cannot safely be appended to a changed asset.
    if (not isinstance(state, dict) or state.get('url_hash') != fingerprint
            or not isinstance(state.get('validator'), dict)
            or state['validator'].get('header') not in ('ETag', 'Last-Modified')
            or not isinstance(state['validator'].get('value'), str)
            or re.search(r'[\r\n]', state['validator']['value'])):
        state = {}
    if not state:
        partial.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    final_metadata = None
    refreshed_auth = False
    for attempt in range(1, max_attempts + 1):
        checkpoint()
        ingestion._remaining(deadline)
        offset = partial.stat().st_size if partial.exists() and state.get('validator') else 0
        if offset > max_bytes:
            raise IngestionError('The partial video exceeds the import size limit.')
        request_headers = {}
        if offset:
            request_headers = {'Range': f'bytes={offset}-', 'If-Range': state['validator']['value']}
        connection = watchdog = None
        try:
            connection, response, watchdog, current, host = _open(url, 'GET', deadline,
                request_headers, headers_factory, allowed_auth_host, monitor=monitor)
            checkpoint()
            if (response.status == 401 and not refreshed_auth and headers_factory is not None
                    and allowed_auth_host == 'www.googleapis.com'):
                try:
                    inspect.signature(headers_factory).bind(force_refresh=True)
                except (TypeError, ValueError):
                    pass
                else:
                    refreshed_auth = True
                    watchdog.cancel()
                    connection.close()
                    checkpoint()
                    connection, response, watchdog, current, host = _open(url, 'GET', deadline,
                        request_headers, headers_factory, allowed_auth_host, force_refresh=True, monitor=monitor)
                    checkpoint()
            if response.status in _RETRY_STATUS:
                raise _RetryError(f'The media host returned temporary HTTP {response.status}.')
            if response.status == 416 and offset:
                match = re.fullmatch(r'bytes \*/([0-9]+)', response.getheader('Content-Range', ''))
                if (match and int(match[1]) == offset and state.get('total') == offset
                        and _validator(response) == state.get('validator')):
                    final_metadata = _metadata(response, current, initial_host, host)
                    break
                partial.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
                state = {}
                raise _RetryError('The media changed; restarting the import.')
            if response.status not in (200, 206):
                raise IngestionError(f'The media host returned HTTP {response.status}. Reconnect or use an accessible authorized file.')
            metadata = _metadata(response, current, initial_host, host)
            if not metadata['downloadable'] and metadata['content_type']:
                raise IngestionError('This link returns a web page or playlist. Use an authorized video file.')
            if response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise IngestionError('Compressed media responses are unsupported.')
            validator = _validator(response)
            declared = metadata['size_bytes']
            if response.status == 206:
                match = re.fullmatch(r'bytes ([0-9]+)-([0-9]+)/([0-9]+)', response.getheader('Content-Range', ''))
                if not offset or not match:
                    raise IngestionError('The media host returned an invalid partial response.')
                start, end, total = map(int, match.groups())
                if (start != offset or not start <= end < total or (declared is not None and declared != end - start + 1)):
                    raise IngestionError('The media host returned a mismatched byte range.')
                if validator != state.get('validator') or (state.get('total') is not None and state['total'] != total):
                    partial.unlink(missing_ok=True)
                    sidecar.unlink(missing_ok=True)
                    state = {}
                    raise _RetryError('The media changed; restarting the import.')
                segment_end = end + 1
            else:
                offset, total, segment_end = 0, declared, declared
            if (total is not None and total > max_bytes) or (expected_size is not None and total is not None and total != expected_size):
                raise IngestionError('The media size does not match the authorized source or exceeds the import limit.')
            state = {'url_hash': fingerprint, 'validator': validator, 'total': total}
            _save_state(sidecar, state)
            received = offset
            progress({'received_bytes': received, 'total_bytes': total or expected_size,
                      'attempt': attempt, 'resumed': bool(offset)})
            with partial.open('ab' if offset else 'wb') as output:
                while not response.isclosed():
                    checkpoint()
                    remaining = ingestion._remaining(deadline)
                    transport = connection.sock or getattr(connection, '_transport_socket', None)
                    if transport is not None:
                        transport.settimeout(min(30, remaining))
                    chunk = response.read1(_CHUNK)
                    ingestion._remaining(deadline)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes or (expected_size is not None and received > expected_size):
                        raise IngestionError('The linked video exceeds the authorized import size limit.')
                    if segment_end is not None and received > segment_end:
                        raise IngestionError('The media host sent more bytes than declared.')
                    if not offset and received == len(chunk):
                        prefix = chunk[:1024].lstrip().lower()
                        if prefix.startswith((b'<!doctype html', b'<html', b'<?xml', b'<mpd', b'#extm3u')):
                            raise IngestionError('This link returns a web page or playlist, not a video file.')
                    checkpoint()
                    progress({'received_bytes': received, 'total_bytes': total or expected_size,
                              'attempt': attempt, 'resumed': bool(offset)})
                    output.write(chunk)
            if (not received or (segment_end is not None and received != segment_end)
                    or (total is not None and received != total)
                    or (expected_size is not None and received != expected_size)):
                raise _RetryError('The video download was incomplete. Resuming the available bytes.')
            final_metadata = metadata
            break
        except (_RetryError, OSError, http.client.HTTPException) as exc:
            monitor.raise_error()
            last_error = exc
            # A failed write must never turn into an unlimited retry loop.
            if isinstance(exc, OSError) and getattr(exc, 'errno', None) in (13, 28):
                raise IngestionError('There is not enough workspace storage or permission to import this video.') from None
            if attempt == max_attempts:
                break
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if connection is not None:
                connection.close()
        # Short cancellation checkpoints also cover retry backoff.
        until = time.monotonic() + min(2 ** (attempt - 1), 4)
        while time.monotonic() < until:
            checkpoint()
            time.sleep(min(.2, max(0, until - time.monotonic()), ingestion._remaining(deadline)))
    if final_metadata is None:
        raise IngestionError('Video import interrupted after retries. Retry to resume the saved partial file.') from last_error
    checkpoint()
    size = partial.stat().st_size
    if not size or size > max_bytes or (expected_size is not None and size != expected_size):
        raise IngestionError('The downloaded video size does not match the authorized source.')
    sha256, md5 = _hash_file(partial, checkpoint)
    if expected_md5 is not None and md5.lower() != expected_md5.lower():
        # A corrupt complete file must not keep passing the 416 completion path.
        partial.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise IngestionError('The downloaded video failed its source integrity check. Import it again.')
    if destination.exists():
        raise IngestionError('The import destination already exists. Start a new import.')
    partial.replace(destination)
    sidecar.unlink(missing_ok=True)
    provenance = {'source_kind': 'authorized_https_import', 'source_host': initial_host,
                  'final_host': final_metadata['final_host'], 'rights_confirmed': True,
                  'imported_at': datetime.now(timezone.utc).isoformat(), 'sha256': sha256, 'size_bytes': size}
    return {'filename': final_metadata['filename'], 'sha256': sha256, 'size_bytes': size, 'provenance': provenance}
