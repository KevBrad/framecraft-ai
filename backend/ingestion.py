"""Bounded imports of authorized HTTPS media files, never network FFmpeg inputs.

Connections bypass proxies, pin a validated DNS address and retain the original
TLS hostname for certificate validation. Every redirect repeats validation.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
from pathlib import Path
import queue
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urljoin, urlsplit


class IngestionError(RuntimeError):
    pass


_YOUTUBE_HOSTS = ('youtube.com', 'youtu.be', 'youtube-nocookie.com', 'googlevideo.com')
_EXTENSIONS = ('.mp4', '.mov', '.webm', '.mkv', '.m4v')
_REDIRECTS = (301, 302, 303, 307, 308)
_CHUNK = 256 * 1024


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IngestionError('Video import timed out. Try a smaller file or a faster direct download link.')
    return remaining


def _parse_url(url: str):
    if not isinstance(url, str) or len(url) > 8192 or re.search(r'[\x00-\x20\x7f\\]', url):
        raise IngestionError('Use a valid direct HTTPS video-file link.')
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443) or parts.fragment):
            raise ValueError()
        host = parts.hostname.rstrip('.').encode('idna').decode('ascii').lower()
        if '%' in host or not re.fullmatch(r'[a-z0-9.:-]+', host):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise IngestionError('Use an HTTPS link on port 443 without login credentials or a fragment.') from None
    if any(host == item or host.endswith('.' + item) for item in _YOUTUBE_HOSTS):
        raise IngestionError('YouTube links provide a reference preview only. Import your original video file or a direct HTTPS file link. For your own uploads, use YouTube Studio or Google Takeout.')
    return parts, host


def _is_public(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or address.is_multicast or address.is_unspecified:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            return False
        # Do not let translation prefixes disguise private IPv4 endpoints.
        if address in ipaddress.ip_network('64:ff9b::/96') or address in ipaddress.ip_network('64:ff9b:1::/48'):
            return False
    # Azure's host virtual service address is globally numbered but internal.
    return str(address) != '168.63.129.16'


def _resolve_public(host: str, deadline: float) -> list[tuple]:
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP))
        except Exception:
            result.put(None)

    # System DNS itself has no portable Python timeout. A daemon thread bounds
    # the caller's wait and never receives a destination/file handle.
    threading.Thread(target=resolve, daemon=True, name='framecraft-import-dns').start()
    try:
        records = result.get(timeout=min(10, _remaining(deadline)))
    except queue.Empty:
        raise IngestionError('The video host could not be resolved in time.') from None
    if not records:
        raise IngestionError('The video host could not be resolved.')
    if any(record[0] not in (socket.AF_INET, socket.AF_INET6) or not _is_public(record[4][0]) for record in records):
        raise IngestionError('Video links must resolve only to public internet addresses. Local, private and metadata addresses are blocked.')
    return records


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, records: list[tuple], deadline: float):
        super().__init__(host, port=443, timeout=min(30, _remaining(deadline)), context=ssl.create_default_context())
        self._records, self._deadline = records, deadline

    def connect(self):
        for family, socktype, proto, _, address in self._records:
            raw = socket.socket(family, socktype, proto)
            try:
                raw.settimeout(min(30, _remaining(self._deadline)))
                # A literal, already-resolved sockaddr prevents DNS rebinding.
                raw.connect(address)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                # getresponse() can detach self.sock for Connection: close
                # while its response file still holds the descriptor open.
                self._transport_socket = self.sock
                return
            except Exception:
                raw.close()
        raise IngestionError('Could not securely connect to the video host. Check the link and TLS certificate.')


def _connection(host: str, records: list[tuple], deadline: float):
    return _PinnedHTTPSConnection(host, records, deadline)


def _check_budget(callback, size):
    if callback is not None:
        try:
            callback(size)
        except Exception:
            raise IngestionError('There is not enough workspace storage to import this video.') from None


def _abort_connection(connection):
    # An absolute deadline also stops slow-drip headers/chunk framing, where a
    # per-socket inactivity timeout alone is insufficient.
    try:
        transport = connection.sock or getattr(connection, '_transport_socket', None)
        if transport is not None:
            transport.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    connection.close()


def download_media(url: str, destination: Path, *, max_bytes: int,
                   check_budget: Callable[[int], None] | None = None,
                   report: Callable[[str], None] | None = None,
                   timeout_seconds: float = 900) -> dict:
    """Download to a new file and return safe metadata (no source URL secrets).

    `check_budget(cumulative_bytes)` runs before each write. The caller must
    confirm rights, probe the local file, and queue processing only after success.
    Failed imports remove only the new file this invocation created.
    """
    if max_bytes <= 0 or timeout_seconds <= 0:
        raise ValueError('Import byte and time limits must be positive.')
    destination = Path(destination)
    deadline = time.monotonic() + timeout_seconds
    current, created = url, False
    initial_parts, initial_host = _parse_url(url)
    del initial_parts
    try:
        _check_budget(check_budget, 0)
        for redirect_count in range(5):
            parts, host = _parse_url(current)
            records = _resolve_public(host, deadline)
            if report:
                report('Connecting to authorized video file')
            connection = _connection(host, records, deadline)
            watchdog = threading.Timer(_remaining(deadline), _abort_connection, args=(connection,))
            watchdog.daemon = True
            watchdog.start()
            try:
                target = parts.path or '/'
                if parts.query:
                    target += '?' + parts.query
                connection.request('GET', target, headers={'Accept': 'video/*, application/octet-stream',
                    'Accept-Encoding': 'identity', 'User-Agent': 'Framecraft/1.0', 'Connection': 'close'})
                response = connection.getresponse()
                if response.status in _REDIRECTS:
                    location = response.getheader('Location')
                    if not location or redirect_count == 4:
                        raise IngestionError('The video link redirects too many times or has no download destination.')
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise IngestionError(f'The video host returned HTTP {response.status}. Use a working direct download link.')
                content_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
                if content_type and not (content_type.startswith('video/') or content_type in ('application/octet-stream', 'binary/octet-stream')):
                    raise IngestionError('This link returns a web page or playlist. Use a direct MP4, MOV, WebM, MKV or M4V file link.')
                if response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                    raise IngestionError('The video host returned an unsupported compressed response. Use a direct video-file link.')
                declared = response.getheader('Content-Length')
                if declared is not None:
                    if not re.fullmatch(r'[0-9]+', declared.strip()):
                        raise IngestionError('The video host returned an invalid file size.')
                    declared = int(declared)
                    if declared > max_bytes:
                        raise IngestionError(f'The linked video exceeds the {max_bytes // 1024**2} MB import limit.')
                digest, size, reported_mb = hashlib.sha256(), 0, -1
                with destination.open('xb') as output:
                    created = True
                    while True:
                        remaining = _remaining(deadline)
                        # HTTPResponse.read1() closes its file as soon as the
                        # declared Content-Length is consumed. For a detached
                        # Connection: close transport this also closes the last
                        # socket descriptor; do not configure that socket again
                        # merely to discover EOF on the following iteration.
                        if response.isclosed():
                            break
                        transport = connection.sock or getattr(connection, '_transport_socket', None)
                        if transport is not None:
                            transport.settimeout(min(30, remaining))
                        chunk = response.read1(_CHUNK)
                        _remaining(deadline)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            raise IngestionError(f'The linked video exceeds the {max_bytes // 1024**2} MB import limit.')
                        if size == len(chunk):
                            prefix = chunk[:1024].lstrip().lower()
                            if prefix.startswith((b'<!doctype html', b'<html', b'<?xml', b'<mpd', b'#extm3u')):
                                raise IngestionError('This link returns a web page or playlist. Use a direct video-file link.')
                        _check_budget(check_budget, size)
                        output.write(chunk)
                        digest.update(chunk)
                        if report and size // 1024**2 != reported_mb:
                            reported_mb = size // 1024**2
                            report(f'Importing video: {size // 1024**2} MB received')
                if not size or (declared is not None and size != declared):
                    raise IngestionError('The video download was empty or incomplete. Retry with a working direct file link.')
                suffix = Path(parts.path).suffix.lower()
                if suffix not in _EXTENSIONS:
                    suffix = {'video/webm': '.webm', 'video/x-matroska': '.mkv', 'video/quicktime': '.mov'}.get(content_type, '.mp4')
                provenance = {'source_kind': 'authorized_https_import', 'source_host': initial_host,
                    'final_host': host, 'rights_confirmed': True, 'imported_at': datetime.now(timezone.utc).isoformat(),
                    'sha256': digest.hexdigest(), 'size_bytes': size}
                return {'filename': 'Imported video' + suffix, 'sha256': digest.hexdigest(), 'size_bytes': size,
                        'provenance': provenance}
            finally:
                watchdog.cancel()
                connection.close()
        raise IngestionError('The video link redirects too many times.')
    except BaseException as exc:
        if created:
            destination.unlink(missing_ok=True)
        if isinstance(exc, (IngestionError, KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(exc, FileExistsError):
            raise IngestionError('The import destination already exists. Start a new import.') from None
        raise IngestionError('The video import failed. Check the direct link, connection and available storage.') from None
