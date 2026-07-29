"""Local storage helpers for the user's Google Gemini API key."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import stat
import tempfile
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path


TERMS_VERSION = "2026-07-23-v1"
ENCRYPTED_API_KEY_HEADER = "AI_ADVENTURE_DPAPI_V1:"


class _DataBlob(ctypes.Structure):
    """Windows DATA_BLOB used by the DPAPI functions."""

    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def read_api_key(path: Path | str) -> str:
    """Reads and trims a locally stored API key, returning blank when absent."""

    key_path = Path(path).expanduser()

    try:
        raw_value = key_path.read_bytes()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""

    if raw_value.startswith(ENCRYPTED_API_KEY_HEADER.encode("ascii")):
        encoded_value = raw_value[len(ENCRYPTED_API_KEY_HEADER) :].strip()

        try:
            protected_value = base64.b64decode(encoded_value, validate=True)
            return _unprotect_for_current_user(protected_value).decode("utf-8").strip()
        except (ValueError, UnicodeDecodeError, OSError):
            return ""

    # Read older plaintext files so existing installations continue working,
    # then upgrade them immediately on Windows when possible.
    legacy_value = raw_value.decode("utf-8", errors="ignore").strip()
    if legacy_value and os.name == "nt":
        try:
            write_api_key(key_path, legacy_value)
        except (OSError, ValueError):
            pass

    return legacy_value


def write_api_key(path: Path | str, api_key: str) -> Path:
    """Atomically writes a non-empty API key to a local app-data file.

    Windows DPAPI encrypts the key for the current Windows user. No application
    decryption key is hard-coded or stored beside the file. A best-effort
    owner-only mode is applied; the UI still warns that local storage is not a
    complete security boundary.
    """

    key_path = Path(path).expanduser().resolve()
    clean_key = str(api_key or "").strip()

    if not clean_key:
        raise ValueError("An API key is required.")

    key_path.parent.mkdir(parents=True, exist_ok=True)
    stored_value = _stored_api_key_bytes(clean_key)
    _atomic_write_bytes(key_path, stored_value)
    _set_owner_only_permissions(key_path)

    return key_path


def record_terms_acceptance(path: Path | str, terms_text: str) -> Path:
    """Writes a local receipt for the exact terms the user accepted.

    The receipt contains no API key. It records UTC time, a terms version, and
    a hash of the displayed text so a later audit can distinguish revisions.
    """

    receipt_path = Path(path).expanduser().resolve()
    normalized_terms = str(terms_text or "")

    if not normalized_terms.strip():
        raise ValueError("The terms text cannot be empty.")

    accepted_at = datetime.now(timezone.utc).replace(microsecond=0)
    acceptance = {
        "document": "AI Adventure Local API-Key Terms of Use and Arbitration Notice",
        "terms_version": TERMS_VERSION,
        "accepted_at_utc": accepted_at.isoformat().replace("+00:00", "Z"),
        "terms_sha256": hashlib.sha256(
            normalized_terms.encode("utf-8")
        ).hexdigest(),
        "api_key_included": False,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    prior_acceptances: list[dict[str, object]] = []

    try:
        prior_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        prior_receipt = {}

    if isinstance(prior_receipt, dict):
        raw_acceptances = prior_receipt.get("acceptances", [])
        if isinstance(raw_acceptances, list):
            prior_acceptances = [
                entry for entry in raw_acceptances if isinstance(entry, dict)
            ]

    receipt = {
        **acceptance,
        "acceptances": [*prior_acceptances, acceptance],
    }
    _atomic_write_text(
        receipt_path,
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
    )
    _set_owner_only_permissions(receipt_path)
    return receipt_path


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically replaces a local text file."""

    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Atomically replaces a local binary file."""

    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        _set_owner_only_permissions(temporary_path)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _stored_api_key_bytes(api_key: str) -> bytes:
    """Returns the on-disk representation of the API key."""

    protected_value = _protect_for_current_user(api_key.encode("utf-8"))
    encoded_value = base64.b64encode(protected_value).decode("ascii")
    return f"{ENCRYPTED_API_KEY_HEADER}{encoded_value}\n".encode("ascii")


def _protect_for_current_user(value: bytes) -> bytes:
    """Encrypts bytes using Windows DPAPI scoped to the current user."""

    if os.name != "nt":
        # The shipped application targets Windows. Keep source-level tests and
        # development tooling portable, while making the fallback explicit.
        return value

    return _call_dpapi("CryptProtectData", value)


def _unprotect_for_current_user(value: bytes) -> bytes:
    """Decrypts bytes using Windows DPAPI scoped to the current user."""

    if os.name != "nt":
        return value

    return _call_dpapi("CryptUnprotectData", value)


def _call_dpapi(function_name: str, value: bytes) -> bytes:
    """Calls one Windows DPAPI transform and frees its output buffer."""

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = getattr(crypt32, function_name)
    function.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    function.restype = wintypes.BOOL

    input_buffer = ctypes.create_string_buffer(value)
    input_blob = _DataBlob(
        len(value),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    output_blob = _DataBlob()

    if not function(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x00000001,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _set_owner_only_permissions(path: Path) -> None:
    """Applies the strongest portable owner-only mode available to Python."""

    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows ACLs are managed separately from POSIX mode bits. The
        # application still keeps the file under the user's app-data folder.
        pass
