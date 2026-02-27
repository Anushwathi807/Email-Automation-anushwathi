from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from typing import Any, Optional

TOKENS_DIR = os.getenv("TOKENS_DIR", "tokens")
INDEX_PATH = os.path.join(TOKENS_DIR, "index.json")


def ensure_tokens_dir() -> None:
    os.makedirs(TOKENS_DIR, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_index() -> dict[str, str]:
    """
    Index maps: email -> filename (relative to TOKENS_DIR).
    Example: {"ops1@example.com": "token_48921.json"}.
    """
    ensure_tokens_dir()
    if not os.path.exists(INDEX_PATH):
        return {}
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_index(index: dict[str, str]) -> None:
    ensure_tokens_dir()
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _token_file_path(filename: str) -> str:
    return os.path.join(TOKENS_DIR, filename)


def _read_json_file(path: str) -> Optional[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json_file(path: str, data: dict[str, Any]) -> None:
    ensure_tokens_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _ensure_index_built() -> dict[str, str]:
    """
    Ensure index exists. If missing, try to build it by scanning tokens/*.json
    and reading `account_email` inside each token file.
    """
    def alloc_new_name() -> str:
        ensure_tokens_dir()
        for _ in range(50):
            num = random.randint(10000, 99999)
            name = f"token_{num}.json"
            if not os.path.exists(_token_file_path(name)):
                return name
        raise RuntimeError("Failed to allocate token filename (too many collisions)")

    def looks_like_token_name(name: str) -> bool:
        if not (name.startswith("token_") and name.endswith(".json")):
            return False
        middle = name[len("token_") : -len(".json")]
        return middle.isdigit() and len(middle) in (4, 5)

    index = _load_index()
    if index:
        # Migrate non-standard filenames to token_<digits>.json
        changed = False
        for email, filename in list(index.items()):
            if looks_like_token_name(filename):
                continue
            old_path = _token_file_path(filename)
            if not os.path.exists(old_path):
                index.pop(email, None)
                changed = True
                continue
            new_name = alloc_new_name()
            os.replace(old_path, _token_file_path(new_name))
            index[email] = new_name
            changed = True
        if changed:
            _save_index(index)
        return index

    ensure_tokens_dir()
    built: dict[str, str] = {}
    try:
        for name in os.listdir(TOKENS_DIR):
            if not name.endswith(".json") or name == "index.json":
                continue
            data = _read_json_file(_token_file_path(name))
            if not isinstance(data, dict):
                continue
            email = data.get("account_email")
            if isinstance(email, str) and email.strip():
                email = email.strip()
                filename = name
                if not looks_like_token_name(filename):
                    new_name = alloc_new_name()
                    os.replace(_token_file_path(filename), _token_file_path(new_name))
                    filename = new_name
                built[email] = filename
    except Exception:
        built = {}

    _save_index(built)
    return built


def list_emails() -> list[str]:
    index = _ensure_index_built()
    return sorted(index.keys())


def read_token_json_by_email(email: str) -> Optional[dict[str, Any]]:
    if not email:
        return None
    index = _ensure_index_built()
    filename = index.get(email)
    if not filename:
        return None
    return _read_json_file(_token_file_path(filename))


def _new_token_filename() -> str:
    # Random 5-digit ID, collision-checked.
    ensure_tokens_dir()
    for _ in range(50):
        num = random.randint(10000, 99999)
        name = f"token_{num}.json"
        if not os.path.exists(_token_file_path(name)):
            return name
    raise RuntimeError("Failed to allocate token filename (too many collisions)")


def upsert_token(email: str, token_json: dict[str, Any], label: str = "") -> str:
    """
    Create or update a token entry for a Gmail inbox (identified by email).
    Returns the internal token filename.
    """
    if not email:
        raise ValueError("email is required")
    if not isinstance(token_json, dict):
        raise ValueError("token_json must be an object")

    index = _ensure_index_built()
    filename = index.get(email) or _new_token_filename()

    data = dict(token_json)
    data["account_email"] = email
    if label:
        data["account_label"] = label
    data["updated_at"] = _now_iso()
    data.setdefault("status", "")

    _write_json_file(_token_file_path(filename), data)
    index[email] = filename
    _save_index(index)
    return filename


def delete_by_email(email: str) -> bool:
    if not email:
        return False
    index = _ensure_index_built()
    filename = index.get(email)
    if not filename:
        return False
    path = _token_file_path(filename)
    try:
        if os.path.exists(path):
            os.remove(path)
    finally:
        index.pop(email, None)
        _save_index(index)
    return True


def get_refresh_token_by_email(email: str) -> Optional[str]:
    data = read_token_json_by_email(email)
    if not isinstance(data, dict):
        return None
    token = data.get("refresh_token")
    return token if isinstance(token, str) and token.strip() else None


def mark_account_invalid(email: str, reason: str) -> None:
    index = _ensure_index_built()
    filename = index.get(email)
    if not filename:
        return
    data = _read_json_file(_token_file_path(filename)) or {}
    data["status"] = "invalid"
    data["invalid_reason"] = reason
    data["updated_at"] = _now_iso()
    _write_json_file(_token_file_path(filename), data)
