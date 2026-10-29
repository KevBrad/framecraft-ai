"""Per-user encrypted local secrets; plaintext never reaches the public API.

Windows uses DPAPI bound to the current Windows user. Other platforms require
an explicitly supplied Fernet key and the optional cryptography package.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import threading
import uuid

from .config import DATA

_LOCK = threading.RLock()
_MAGIC = b'FRAMECRAFT-SECRET-1\n'


class SecretError(RuntimeError):
    pass


def _path(name: str) -> Path:
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', name):
        raise SecretError('Invalid local secret name.')
    directory = DATA / 'secrets'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory / (name + '.bin')


def _dpapi(value: bytes, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    raw = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_raw = ctypes.create_string_buffer(b'Framecraft local secrets v1')
    entropy = Blob(len(entropy_raw.raw) - 1, ctypes.cast(entropy_raw, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    if decrypt:
        operation = crypt32.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = None
    else:
        operation = crypt32.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = 'Framecraft'
    operation.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN: never trigger credential/UI prompts in workers.
    if not operation(ctypes.byref(source), description, ctypes.byref(entropy), None, None, 1, ctypes.byref(result)):
        raise SecretError('Windows could not unlock local credentials for this user. Reconnect the source.')
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _fernet():
    key = os.getenv('FRAMECRAFT_SECRET_KEY', '')
    if not key:
        raise SecretError('Encrypted credential storage requires Windows DPAPI, or FRAMECRAFT_SECRET_KEY and cryptography on other systems.')
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode('ascii'))
    except (ImportError, ValueError, UnicodeError):
        raise SecretError('Install cryptography and configure a valid Fernet FRAMECRAFT_SECRET_KEY.') from None


def save_secret(name: str, value) -> None:
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > 2 * 1024 * 1024:
        raise SecretError('Local secret is too large.')
    encrypted = b'DPAPI\n' + _dpapi(raw) if os.name == 'nt' else b'FERNET\n' + _fernet().encrypt(raw)
    with _LOCK:
        target = _path(name)
        temporary = target.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as handle:
                if os.name != 'nt':
                    os.chmod(temporary, 0o600)
                handle.write(_MAGIC + encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def load_secret(name: str):
    with _LOCK:
        target = _path(name)
        if not target.exists():
            return None
        if target.stat().st_size > 3 * 1024 * 1024:
            raise SecretError('Invalid local credential storage. Reconnect the source.')
        encrypted = target.read_bytes()
    if not encrypted.startswith(_MAGIC):
        raise SecretError('Unrecognized local credential storage. Reconnect the source.')
    body = encrypted[len(_MAGIC):]
    try:
        if body.startswith(b'DPAPI\n') and os.name == 'nt':
            raw = _dpapi(body[6:], decrypt=True)
        elif body.startswith(b'FERNET\n'):
            raw = _fernet().decrypt(body[7:])
        else:
            raise SecretError('Credentials belong to another operating system. Reconnect the source.')
        return json.loads(raw)
    except SecretError:
        raise
    except Exception:
        raise SecretError('Local credentials could not be decrypted. Reconnect the source.') from None


def delete_secret(name: str) -> None:
    with _LOCK:
        _path(name).unlink(missing_ok=True)
