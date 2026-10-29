"""Google transport tests are mocked; these do not claim live Google consent."""
import base64
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest

from backend import drive
from backend import secrets as storage


@pytest.fixture
def connected(monkeypatch, tmp_path):
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_ID', '123456789012-example.apps.googleusercontent.com')
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_FILE', raising=False)
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_SECRET', raising=False)
    monkeypatch.delenv('FRAMECRAFT_OAUTH_REDIRECT_URI', raising=False)
    monkeypatch.setattr(drive, 'DATA', tmp_path)
    store = {}
    monkeypatch.setattr(drive, 'load_secret', lambda name: store.get(name))
    monkeypatch.setattr(drive, 'save_secret', lambda name, value: store.__setitem__(name, value))
    monkeypatch.setattr(drive, 'delete_secret', lambda name: store.pop(name, None))
    store[drive._TOKEN_NAME] = {'access_token': 'old-token', 'refresh_token': 'refresh-token', 'expires_at': time.time() + 3600, 'client_id': os.environ['GOOGLE_OAUTH_CLIENT_ID']}
    return store


def test_missing_configuration_is_explicit(monkeypatch, tmp_path):
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_FILE', raising=False)
    monkeypatch.setattr(drive, 'DATA', tmp_path)
    assert drive.connection_status()['configured'] is False
    assert 'data/connections/google-oauth-client.json' in drive.connection_status()['message']
    with pytest.raises(drive.DriveError):
        drive.begin_auth()


def test_default_client_json_is_detected(monkeypatch, tmp_path):
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_FILE', raising=False)
    monkeypatch.setattr(drive, 'DATA', tmp_path)
    (tmp_path / 'connections').mkdir()
    (tmp_path / 'connections' / 'google-oauth-client.json').write_text(json.dumps({'installed': {'client_id': '123456789012-example.apps.googleusercontent.com'}}))
    assert drive._client_config()['client_id'].startswith('123456')


def test_pkce_and_single_use_state(connected, monkeypatch):
    values = parse_qs(urlsplit(drive.begin_auth()).query)
    state = values['state'][0]
    pending = connected[drive._STATES_NAME][state]
    expected = base64.urlsafe_b64encode(hashlib.sha256(pending['verifier'].encode()).digest()).rstrip(b'=').decode()
    assert values['code_challenge'] == [expected]
    assert values['code_challenge_method'] == ['S256']
    assert values['scope'] == [drive.SCOPE]
    calls = []
    def transport(method, url, **kwargs):
        calls.append(kwargs['data'])
        return 200, {'access_token': 'new-token', 'refresh_token': 'new-refresh', 'expires_in': 3600, 'scope': drive.SCOPE}
    monkeypatch.setattr(drive, '_request', transport)
    result = drive.complete_auth('auth-code', state)
    assert result['connected'] is True
    assert calls[0]['code_verifier'] == pending['verifier']
    assert 'token' not in json.dumps(result)
    with pytest.raises(drive.DriveError, match='expired|match'):
        drive.complete_auth('auth-code', state)


def test_expired_or_forged_state_never_calls_google(connected, monkeypatch):
    state = parse_qs(urlsplit(drive.begin_auth()).query)['state'][0]
    connected[drive._STATES_NAME][state]['expires'] = time.time() - 1
    monkeypatch.setattr(drive, '_request', lambda *a, **k: pytest.fail('Expired state reached Google'))
    for candidate in (state, 'x' * 40):
        with pytest.raises(drive.DriveError):
            drive.complete_auth('auth-code', candidate)


@pytest.mark.parametrize('redirect', ['https://evil.test/api/connections/google-drive/callback', 'http://localhost:8000/api/connections/google-drive/callback', 'http://127.0.0.1:8000/evil', 'http://127.0.0.1:8000/api/connections/google-drive/callback?evil=yes'])
def test_redirect_is_loopback_only(connected, monkeypatch, redirect):
    monkeypatch.setenv('FRAMECRAFT_OAUTH_REDIRECT_URI', redirect)
    with pytest.raises(drive.DriveError, match='redirect'):
        drive.begin_auth()


def test_refresh_once_after_401(connected, monkeypatch):
    calls = []
    def transport(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == 'POST':
            assert kwargs['data']['grant_type'] == 'refresh_token'
            return 200, {'access_token': 'fresh-token', 'expires_in': 3600}
        if kwargs['headers']['Authorization'] == 'Bearer old-token':
            return 401, {}
        return 200, {'files': []}
    monkeypatch.setattr(drive, '_request', transport)
    assert drive.list_videos()['files'] == []
    assert len(calls) == 3
    assert connected[drive._TOKEN_NAME]['refresh_token'] == 'refresh-token'


def test_failed_401_retry_disconnects(connected, monkeypatch):
    calls = []
    def transport(method, url, **kwargs):
        calls.append(method)
        return (200, {'access_token': 'still-invalid'}) if method == 'POST' else (401, {})
    monkeypatch.setattr(drive, '_request', transport)
    with pytest.raises(drive.DriveError, match='revoked'):
        drive.list_videos()
    assert calls == ['GET', 'POST', 'GET']
    assert drive._TOKEN_NAME not in connected


def test_expired_token_refresh_and_revocation(connected, monkeypatch):
    connected[drive._TOKEN_NAME]['expires_at'] = 1
    monkeypatch.setattr(drive, '_request', lambda *a, **k: (400, {'error': 'invalid_grant'}))
    with pytest.raises(drive.DriveError, match='revoked'):
        drive.access_headers()
    assert drive._TOKEN_NAME not in connected


def test_concurrent_expired_token_requests_refresh_only_once(connected, monkeypatch):
    connected[drive._TOKEN_NAME]['expires_at'] = 1
    calls = []
    def transport(method, url, **kwargs):
        calls.append(method)
        time.sleep(0.01)
        return 200, {'access_token': 'refreshed-once', 'expires_in': 3600}
    monkeypatch.setattr(drive, '_request', transport)
    with ThreadPoolExecutor(max_workers=6) as pool:
        headers = list(pool.map(lambda _: drive.access_headers(), range(6)))
    assert calls == ['POST']
    assert all(item['Authorization'] == 'Bearer refreshed-once' for item in headers)


def video(**changes):
    return {'id': 'creator_video_123', 'name': 'Original.mp4', 'mimeType': 'video/mp4', 'size': '1024', 'capabilities': {'canDownload': True}, **changes}


@pytest.mark.parametrize('changes', [{'capabilities': {'canDownload': False}}, {'mimeType': 'application/vnd.google-apps.shortcut'}, {'size': '0'}, {'size': str(10**14)}, {'id': '../secret'}])
def test_denied_invalid_and_oversize_media_rejected(changes):
    with pytest.raises(drive.DriveError):
        drive.validate_video(video(**changes), max_bytes=2048)


def test_picker_filters_and_escapes_search(connected, monkeypatch):
    def transport(method, url, **kwargs):
        assert kwargs['params']['supportsAllDrives'] == 'true'
        assert kwargs['params']['includeItemsFromAllDrives'] == 'true'
        assert "name contains 'O\\'Neil'" in kwargs['params']['q']
        return 200, {'files': [video(), video(capabilities={'canDownload': False}), video(mimeType='application/vnd.google-apps.document')], 'nextPageToken': 'next'}
    monkeypatch.setattr(drive, '_request', transport)
    result = drive.list_videos("O'Neil")
    assert len(result['files']) == 1
    assert result['next_page_token'] == 'next'


def test_file_permission_error_is_actionable(connected, monkeypatch):
    monkeypatch.setattr(drive, '_request', lambda *a, **k: (403, {'error': {'message': 'private upstream detail'}}))
    with pytest.raises(drive.DriveError, match='owner does not permit') as error:
        drive.file_metadata('valid_id')
    assert 'private upstream detail' not in str(error.value)


def test_disconnect_deletes_credentials_even_when_revocation_offline(connected, monkeypatch):
    def offline(*args, **kwargs):
        raise drive.DriveError('offline')
    monkeypatch.setattr(drive, '_request', offline)
    result = drive.disconnect()
    assert result['connected'] is False
    assert 'Local Google Drive credentials removed' in result['message']
    assert drive._TOKEN_NAME not in connected


@pytest.mark.parametrize('url,expected', [('https://drive.google.com/file/d/abc_123/view?usp=sharing', 'abc_123'), ('https://drive.google.com/open?id=abc_123', 'abc_123'), ('https://drive.google.com/uc?id=abc_123&export=download', 'abc_123'), ('https://drive.google.com.evil.test/file/d/abc/view', None), ('https://user@drive.google.com/file/d/abc/view', None), ('https://drive.google.com/file/d/../view', None), ('https://drive.google.com/open?id=one&id=two', None), ('http://drive.google.com/file/d/abc/view', None)])
def test_drive_link_parser(url, expected):
    assert drive.parse_drive_url(url) == expected


def test_api_destination_allowlist():
    with pytest.raises(drive.DriveError, match='destination'):
        drive._request('GET', 'https://evil.test/', headers={'Authorization': 'Bearer protected'})
    with pytest.raises(drive.DriveError):
        drive.media_url('id?alt=media&evil=yes')


def test_http_transport_blocks_redirects_and_disables_environment(monkeypatch):
    import httpx
    original_client = httpx.Client
    seen = []
    def factory(**kwargs):
        assert kwargs['trust_env'] is False
        assert kwargs['follow_redirects'] is False
        def response(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={'Location': 'https://evil.test/stolen'}, json={})
        return original_client(**kwargs, transport=httpx.MockTransport(response))
    monkeypatch.setattr(drive.httpx, 'Client', factory)
    with pytest.raises(drive.DriveError, match='redirected'):
        drive._request('GET', drive._FILES, headers={'Authorization': 'Bearer do-not-forward'})
    assert seen == [drive._FILES]


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI integration test')
def test_dpapi_roundtrip_tamper_and_no_plaintext(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    payload = {'token': 'sensitive-example-token', 'nested': ['url?signature=secret']}
    storage.save_secret('sample', payload)
    assert storage.load_secret('sample') == payload
    target = tmp_path / 'secrets' / 'sample.bin'
    raw = target.read_bytes()
    assert b'sensitive-example-token' not in raw
    target.write_bytes(raw[:-8] + b'corrupt!')
    with pytest.raises(storage.SecretError):
        storage.load_secret('sample')
    storage.delete_secret('sample')
    assert storage.load_secret('sample') is None


@pytest.mark.parametrize('name', ['../token', 'a/b', '', 'x' * 101])
def test_secret_path_traversal_is_blocked(name):
    with pytest.raises(storage.SecretError):
        storage.load_secret(name)
