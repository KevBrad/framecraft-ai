"""Google-authorized video metadata and OAuth; never scrape Drive web players.

Transport destinations are compile-time HTTPS endpoints. Bearer tokens remain
server-side. The ingestion worker streams media_url() with access_headers().
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .config import DATA, MAX_UPLOAD
from .secrets import SecretError, delete_secret, load_secret, save_secret

SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
_AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
_TOKEN = 'https://oauth2.googleapis.com/token'
_REVOKE = 'https://oauth2.googleapis.com/revoke'
_FILES = 'https://www.googleapis.com/drive/v3/files'
_TOKEN_NAME = 'google_drive_tokens'
_STATES_NAME = 'google_drive_states'
_LOCK = threading.RLock()
_FIELDS = 'id,name,mimeType,size,md5Checksum,capabilities(canDownload),videoMediaMetadata,version,modifiedTime'


class DriveError(RuntimeError):
    pass


@contextmanager
def _credential_lock():
    """Serialize refresh/state updates across both API and worker processes.

    OS byte/file locks release automatically after crashes; the lock file is
    intentionally persistent and contains no credentials.
    """
    with _LOCK:
        directory = DATA / 'secrets'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (directory / 'google-drive.lock').open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + 45
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise DriveError('Another Google Drive connection update is still running. Try again shortly.') from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _client_config() -> dict:
    client_id = os.getenv('GOOGLE_OAUTH_CLIENT_ID', '')
    client_secret = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET', '')
    # A predictable location lets setup instructions name a concrete destination.
    # Never manufacture credentials: only Google can issue a valid client ID.
    location = os.getenv('GOOGLE_OAUTH_CLIENT_FILE', '')
    if not location and not client_id:
        location = str(DATA / 'connections' / 'google-oauth-client.json')
    if location:
        try:
            path = Path(location)
            if path.stat().st_size > 65536:
                raise ValueError()
            document = json.loads(path.read_text(encoding='utf-8-sig'))
            # Desktop application clients support the loopback flow. Web/mobile
            # client JSON is not silently treated as an installed application.
            installed = document['installed']
            client_id = installed['client_id']
            client_secret = installed.get('client_secret', '')
        except (OSError, ValueError, KeyError, TypeError):
            raise DriveError('Google Drive setup needs your Desktop OAuth client JSON at data/connections/google-oauth-client.json (or the GOOGLE_OAUTH_CLIENT_FILE location).') from None
    if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9._-]{10,256}\.apps\.googleusercontent\.com', client_id):
        raise DriveError('Google Drive is not configured. Add your Desktop OAuth client JSON with GOOGLE_OAUTH_CLIENT_FILE, then restart Framecraft.')
    if not isinstance(client_secret, str) or len(client_secret) > 1024 or re.search(r'[\x00-\x20\x7f]', client_secret):
        raise DriveError('The Google OAuth client secret configuration is invalid.')
    redirect = os.getenv('FRAMECRAFT_OAUTH_REDIRECT_URI', 'http://127.0.0.1:8000/api/connections/google-drive/callback')
    try:
        parts = urlsplit(redirect)
        if (parts.scheme != 'http' or parts.hostname != '127.0.0.1' or parts.username is not None
                or parts.password is not None or not parts.port or parts.query or parts.fragment
                or parts.path != '/api/connections/google-drive/callback'):
            raise ValueError()
    except ValueError:
        raise DriveError('Google OAuth redirect must use http://127.0.0.1:<port>/api/connections/google-drive/callback.') from None
    return {'client_id': client_id, 'client_secret': client_secret, 'redirect_uri': redirect}


def _request(method: str, url: str, *, data=None, params=None, headers=None) -> tuple[int, dict]:
    # Callers cannot turn this utility into an arbitrary authenticated proxy.
    if url not in (_TOKEN, _REVOKE, _FILES) and not re.fullmatch(re.escape(_FILES) + r'/[A-Za-z0-9_-]{1,200}', url):
        raise DriveError('Unsupported Google API destination.')
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False) as client:
            with client.stream(method, url, data=data, params=params, headers=headers) as response:
                body = bytearray()
                for part in response.iter_bytes(64 * 1024):
                    body.extend(part)
                    if len(body) > 2 * 1024 * 1024:
                        raise DriveError('Google returned an unexpectedly large metadata response.')
                if 300 <= response.status_code < 400:
                    raise DriveError('Google API redirected unexpectedly; no credentials were forwarded.')
                try:
                    result = json.loads(body) if body else {}
                except (ValueError, UnicodeError):
                    raise DriveError('Google returned an invalid API response.') from None
                if not isinstance(result, dict):
                    raise DriveError('Google returned an invalid API response.')
                return response.status_code, result
    except httpx.HTTPError:
        raise DriveError('Google Drive could not be reached securely. Check the connection and try again.') from None


def _read_secret(name: str):
    try:
        return load_secret(name)
    except SecretError as error:
        raise DriveError(str(error)) from None


def _save_secret(name: str, value):
    try:
        save_secret(name, value)
    except SecretError as error:
        raise DriveError(str(error)) from None


def begin_auth() -> str:
    config = _client_config()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
    with _credential_lock():
        states = _read_secret(_STATES_NAME) or {}
        states = {key: value for key, value in states.items() if value.get('expires', 0) > time.time()}
        # Bound abandoned login attempts without disrupting the newest flow.
        while len(states) >= 10:
            del states[next(iter(states))]
        states[state] = {'verifier': verifier, 'expires': time.time() + 600, 'client_id': config['client_id'], 'redirect_uri': config['redirect_uri']}
        _save_secret(_STATES_NAME, states)
    return _AUTH + '?' + urlencode({'client_id': config['client_id'], 'redirect_uri': config['redirect_uri'], 'response_type': 'code', 'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent', 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})


def _accept_tokens(payload: dict, config: dict, previous: dict | None = None) -> dict:
    if str(payload.get('token_type', 'Bearer')).lower() != 'bearer':
        raise DriveError('Google returned an unsupported access-token type.')
    token = payload.get('access_token')
    if not isinstance(token, str) or not token or len(token) > 16384 or re.search(r'[\x00-\x20\x7f]', token):
        raise DriveError('Google did not return a usable access token. Reconnect Google Drive.')
    scope = payload.get('scope')
    if scope is not None and (not isinstance(scope, str) or SCOPE not in scope.split()):
        raise DriveError('Read-only Google Drive permission was not granted. Reconnect and approve this permission.')
    try:
        expires = int(payload.get('expires_in', 3600))
        if not 0 < expires <= 86400:
            raise ValueError()
    except (TypeError, ValueError):
        raise DriveError('Google returned an invalid token lifetime.') from None
    refresh = payload.get('refresh_token') or (previous or {}).get('refresh_token')
    if refresh is not None and (not isinstance(refresh, str) or len(refresh) > 16384 or re.search(r'[\x00-\x20\x7f]', refresh)):
        raise DriveError('Google returned an invalid refresh token.')
    credentials = {'access_token': token, 'refresh_token': refresh, 'expires_at': time.time() + expires, 'client_id': config['client_id']}
    _save_secret(_TOKEN_NAME, credentials)
    return credentials


def complete_auth(code: str, state: str) -> dict:
    if not isinstance(state, str) or not 16 <= len(state) <= 200:
        raise DriveError('Google sign-in state is missing or invalid. Start Connect Google Drive again.')
    with _credential_lock():
        states = _read_secret(_STATES_NAME) or {}
        pending = states.pop(state, None)
        _save_secret(_STATES_NAME, states)  # Single-use even if exchange fails.
        config = _client_config()
        if (not pending or pending.get('expires', 0) <= time.time() or pending.get('client_id') != config['client_id']
                or pending.get('redirect_uri') != config['redirect_uri']):
            raise DriveError('Google sign-in expired or does not match this request. Start Connect Google Drive again.')
        if not isinstance(code, str) or not code or len(code) > 4096 or re.search(r'[\x00-\x20\x7f]', code):
            raise DriveError('Google sign-in did not return an authorization code.')
        form = {**config, 'code': code, 'code_verifier': pending['verifier'], 'grant_type': 'authorization_code'}
        status, result = _request('POST', _TOKEN, data=form)
        if status != 200:
            raise DriveError('Google sign-in could not be completed. Start Connect Google Drive again.')
        _accept_tokens(result, config)
    return connection_status()


def connection_status() -> dict:
    try:
        config = _client_config()
    except DriveError as error:
        return {'configured': False, 'connected': False, 'message': str(error)}
    try:
        credentials = _read_secret(_TOKEN_NAME)
        connected = bool(credentials and credentials.get('client_id') == config['client_id'] and (credentials.get('refresh_token') or credentials.get('expires_at', 0) > time.time()))
    except DriveError as error:
        return {'configured': True, 'connected': False, 'message': str(error)}
    return {'configured': True, 'connected': connected, 'message': 'Google Drive connected with read-only access. Choose a video you are authorized to use.' if connected else 'Connect Google Drive to browse and import videos with your permission.'}


def access_headers(force_refresh: bool = False) -> dict:
    with _credential_lock():
        config = _client_config()
        credentials = _read_secret(_TOKEN_NAME)
        if not credentials or credentials.get('client_id') != config['client_id']:
            raise DriveError('Connect Google Drive before importing this video.')
        if force_refresh or credentials.get('expires_at', 0) <= time.time() + 60:
            if not credentials.get('refresh_token'):
                delete_secret(_TOKEN_NAME)
                raise DriveError('Google Drive authorization expired. Reconnect Google Drive.')
            form = {'client_id': config['client_id'], 'client_secret': config['client_secret'], 'grant_type': 'refresh_token', 'refresh_token': credentials['refresh_token']}
            status, result = _request('POST', _TOKEN, data=form)
            if status != 200:
                if status in (400, 401):
                    delete_secret(_TOKEN_NAME)
                    raise DriveError('Google Drive permission expired or was revoked. Reconnect Google Drive.')
                raise DriveError('Google Drive could not refresh its connection. Try again shortly.')
            credentials = _accept_tokens(result, config, credentials)
        return {'Authorization': 'Bearer ' + credentials['access_token']}


def request_json(url: str, params: dict | None = None) -> dict:
    status, result = _request('GET', url, params=params, headers=access_headers())
    if status == 401:
        status, result = _request('GET', url, params=params, headers=access_headers(force_refresh=True))
    if status == 401:
        delete_secret(_TOKEN_NAME)
        raise DriveError('Google Drive permission was revoked. Reconnect Google Drive.')
    if status in (403, 404):
        raise DriveError('This Google Drive video is unavailable or its owner does not permit downloading it.')
    if status != 200:
        raise DriveError('Google Drive could not load this video. Try again shortly.')
    return result


def disconnect() -> dict:
    with _credential_lock():
        try:
            credentials = _read_secret(_TOKEN_NAME)
        except DriveError:
            credentials = None
        delete_secret(_TOKEN_NAME)
        delete_secret(_STATES_NAME)
        revoked = True
        if credentials:
            try:
                status, _ = _request('POST', _REVOKE, data={'token': credentials.get('refresh_token') or credentials.get('access_token')})
                revoked = status in (200, 400)
            except DriveError:
                revoked = False
    result = connection_status()
    result['message'] = 'Google Drive disconnected.' if revoked else 'Local Google Drive credentials removed. Google could not be reached to revoke access; you can also remove Framecraft in your Google Account permissions.'
    return result


def validate_file_id(file_id: str) -> str:
    if not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', file_id):
        raise DriveError('Use a valid Google Drive video-file link or choose a video from Drive.')
    return file_id


def parse_drive_url(url: str) -> str | None:
    if not isinstance(url, str) or len(url) > 8192 or re.search(r'[\x00-\x20\x7f\\]', url):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.hostname not in ('drive.google.com', 'docs.google.com') or parts.port not in (None, 443) or parts.username is not None or parts.password is not None or parts.fragment:
            return None
        match = re.fullmatch(r'/file/d/([A-Za-z0-9_-]{1,200})(?:/(?:view|preview|edit))?/?', parts.path)
        if match:
            return match.group(1)
        if parts.hostname == 'drive.google.com' and parts.path in ('/open', '/uc'):
            values = parse_qs(parts.query).get('id', [])
            if len(values) == 1:
                return validate_file_id(values[0])
    except (ValueError, DriveError):
        return None
    return None


def validate_video(metadata: dict, max_bytes: int = MAX_UPLOAD) -> dict:
    validate_file_id(metadata.get('id'))
    if not str(metadata.get('mimeType', '')).startswith('video/'):
        raise DriveError('Choose an original video file stored in Google Drive. Folders, shortcuts and native Google documents are not video imports.')
    if metadata.get('capabilities', {}).get('canDownload') is not True:
        raise DriveError('The owner has not allowed this Google Drive video to be downloaded.')
    try:
        size = int(metadata.get('size', 0))
        if size <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        raise DriveError('Google Drive did not provide a valid video-file size.') from None
    if size > max_bytes:
        raise DriveError('This Google Drive video exceeds the configured import size limit.')
    return metadata


def list_videos(query: str = '', page_token: str = '') -> dict:
    if not isinstance(query, str) or len(query) > 200 or re.search(r'[\x00-\x1f\x7f]', query):
        raise DriveError('Use a video-name search of at most 200 characters.')
    if not isinstance(page_token, str) or len(page_token) > 4096 or re.search(r'[\x00-\x20\x7f]', page_token):
        raise DriveError('Google Drive page token is invalid. Start the search again.')
    clause = "trashed = false and mimeType contains 'video/'"
    if query:
        escaped = query.replace('\\', '\\\\').replace("'", "\\'")
        clause += " and name contains '" + escaped + "'"
    params = {'q': clause, 'pageSize': '50', 'fields': 'nextPageToken,incompleteSearch,files(' + _FIELDS + ')', 'orderBy': 'modifiedTime desc', 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true', 'spaces': 'drive'}
    if page_token:
        params['pageToken'] = page_token
    result = request_json(_FILES, params)
    files = []
    for metadata in result.get('files', []):
        try:
            validate_video(metadata)
            files.append(metadata)
        except (DriveError, AttributeError, TypeError):
            continue
    return {'files': files, 'next_page_token': result.get('nextPageToken', ''), 'incomplete_search': bool(result.get('incompleteSearch'))}


def file_metadata(file_id: str) -> dict:
    return validate_video(request_json(_FILES + '/' + validate_file_id(file_id), {'fields': _FIELDS, 'supportsAllDrives': 'true'}))


def media_url(file_id: str) -> str:
    return _FILES + '/' + validate_file_id(file_id) + '?alt=media&supportsAllDrives=true'
