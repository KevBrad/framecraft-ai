"""Provider boundary: identification never implies permission to fetch media.

An approved future adapter must explicitly resolve an authorized source and
provide a download plan. No YouTube playback extraction adapter is registered.
"""
from dataclasses import dataclass
import re
from typing import Callable, Protocol
from urllib.parse import parse_qs, urlsplit

from .config import MAX_UPLOAD
from .ingestion import IngestionError


def youtube_id(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError
        host = (parsed.hostname or '').lower()
        parts = parsed.path.strip('/').split('/')
        if host in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
            value = parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else (parts[1] if len(parts) == 2 and parts[0] in ('shorts', 'embed', 'live') else '')
        elif host == 'youtu.be':
            value = parsed.path.strip('/')
        else:
            raise ValueError
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
            raise ValueError
        return value
    except (ValueError, AttributeError):
        raise IngestionError('Use a valid HTTPS YouTube watch, Shorts or youtu.be link.') from None


@dataclass
class DownloadPlan:
    url: str
    headers_factory: Callable | None = None
    allowed_auth_host: str | None = None
    expected_size: int | None = None
    expected_md5: str | None = None
    filename: str | None = None
    identity: dict | None = None


class ProviderAdapter(Protocol):
    name: str
    def matches(self, url: str) -> bool: ...
    def resolve(self, url: str) -> dict: ...
    def plan(self, source_ref: dict, secret: dict) -> DownloadPlan: ...


class DirectMedia:
    name = 'direct'
    def matches(self, url):
        return True

    def resolve(self, url):
        from .import_transport import probe_metadata
        metadata = probe_metadata(url)
        if metadata.get('size_bytes') and metadata['size_bytes'] > MAX_UPLOAD:
            raise IngestionError('This video exceeds the configured import size limit.')
        if not metadata.get('downloadable'):
            raise IngestionError('This link is a web page or unsupported stream. Use an HTTPS video-file link.')
        return {'provider': self.name, 'readiness': 'ready', 'title': metadata.get('filename') or 'Imported video',
                'size_bytes': metadata.get('size_bytes'), 'metadata': metadata,
                'message': 'Ready to import. The downloaded file will be validated before processing.'}

    def plan(self, source_ref, secret):
        if not secret or not secret.get('url'):
            raise IngestionError('The source link is unavailable. Retry with a fresh authorized link.')
        return DownloadPlan(secret['url'])


class GoogleDrive:
    name = 'google_drive'
    def matches(self, url):
        return (urlsplit(url).hostname or '').lower() in ('drive.google.com', 'docs.google.com')

    def resolve(self, url):
        from . import drive
        file_id = drive.parse_drive_url(url)
        if not file_id:
            raise IngestionError('Use a Google Drive video-file link, not a folder or document.')
        status = drive.connection_status()
        if not status.get('connected'):
            return {'provider': self.name, 'file_id': file_id, 'readiness': 'requires_connection',
                    'message': status.get('message') or 'Connect Google Drive to validate and import this video.'}
        metadata = drive.file_metadata(file_id)
        drive.validate_video(metadata, MAX_UPLOAD)
        return {'provider': self.name, 'readiness': 'ready', 'file_id': file_id, 'title': metadata['name'],
                'size_bytes': int(metadata.get('size', 0)), 'metadata': metadata,
                'message': 'This account can download the video. Ready to import with your permission.'}

    def plan(self, source_ref, secret):
        from . import drive
        metadata = drive.file_metadata(source_ref['file_id'])
        drive.validate_video(metadata, MAX_UPLOAD)
        return DownloadPlan(drive.media_url(metadata['id']), drive.access_headers, 'www.googleapis.com',
                            int(metadata.get('size', 0)) or None, metadata.get('md5Checksum'), metadata['name'],
                            {'drive_file_id': metadata['id'], 'drive_version': metadata.get('version'),
                             'modified_time': metadata.get('modifiedTime')})


class YouTubeReference:
    name = 'youtube'
    def matches(self, url):
        host = (urlsplit(url).hostname or '').lower()
        return host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be')

    def resolve(self, url):
        import httpx
        video_id = youtube_id(url)
        result = {'provider': self.name, 'youtube_id': video_id, 'readiness': 'requires_file',
                  'title': None, 'metadata': {'video_id': video_id},
                  'message': 'YouTube reference only. Link a creator-stored original, upload an authorized file, or select it from Google Drive.'}
        try:
            with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
                with client.stream('GET', 'https://www.youtube.com/oembed', params={'url':f'https://www.youtube.com/watch?v={video_id}', 'format':'json'}) as response:
                    data = bytearray()
                    for block in response.iter_bytes(8192):
                        data.extend(block)
                        if len(data) > 65536:
                            break
                    if response.status_code == 200 and len(data) <= 65536:
                        import json
                        metadata = json.loads(data)
                        result['title'] = str(metadata.get('title', ''))[:200] or None
                        result['metadata'].update(title=result['title'], author_name=str(metadata.get('author_name',''))[:200])
        except (httpx.HTTPError, ValueError):
            pass
        return result

    def plan(self, source_ref, secret):
        raise IngestionError('YouTube metadata is not downloadable media. Link an authorized original source first.')


_ADAPTERS: list[ProviderAdapter] = [YouTubeReference(), GoogleDrive(), DirectMedia()]


def register_provider(adapter: ProviderAdapter, *, before: str='direct'):
    """Explicit code-level extension point for a licensed/approved integration.

    External requests cannot register adapters, arbitrary endpoints or credentials.
    """
    if not adapter.name or any(a.name == adapter.name for a in _ADAPTERS):
        raise ValueError('Provider names must be unique.')
    for index, existing in enumerate(_ADAPTERS):
        if existing.name==before:
            _ADAPTERS.insert(index,adapter)
            return
    raise ValueError('The requested provider priority does not exist.')


def adapter_for(url: str) -> ProviderAdapter:
    if not isinstance(url, str) or len(url) > 8192 or re.search(r'[\x00-\x20\x7f\\]', url):
        raise IngestionError('Use a valid HTTPS video URL.')
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ValueError
        for adapter in _ADAPTERS:
            if adapter.matches(url):
                return adapter
    except ValueError:
        pass
    raise IngestionError('Use a valid HTTPS video URL without embedded login credentials.')


def adapter_named(name):
    for adapter in _ADAPTERS:
        if adapter.name == name:
            return adapter
    raise IngestionError('This source provider is not available.')
