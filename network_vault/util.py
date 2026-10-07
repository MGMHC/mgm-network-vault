"""Shared helpers: secrets, event logging, paths."""
import json
import os
import re
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config
from .models import LogEntry, db
from .portable import apply_pending_restore

# NETWORK_VAULT_DATA is kept as an alias of NETWORK_VAULT_DATA_DIR for older setups
BASE_DIR = Path(os.environ["NETWORK_VAULT_DATA"]) if os.environ.get("NETWORK_VAULT_DATA") else config.data_dir()
BASE_DIR.mkdir(parents=True, exist_ok=True)
# a restore uploaded earlier is swapped in here - before the keys and database are opened
apply_pending_restore(BASE_DIR)
# restored.json is left by the swap (here or by "manage.py import") so the app can log it once
RESTORED = json.loads((BASE_DIR / "restored.json").read_text(encoding="utf-8"))     if (BASE_DIR / "restored.json").exists() else None
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _key_file(name, factory):
    p = BASE_DIR / name
    if not p.exists():
        p.write_bytes(factory())
    return p.read_bytes().strip()


FLASK_SECRET = _key_file("flask.key", lambda: secrets.token_hex(32).encode())
_fernet = Fernet(_key_file("secret.key", Fernet.generate_key))


def encrypt(text):
    return _fernet.encrypt((text or "").encode()).decode()


def decrypt(token):
    if not token:
        return ""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


def log_event(message, category="system", level="INFO", device=None, details=None, user=None, commit=True):
    if user is None:
        try:
            from flask import has_request_context, session
            user = session.get("user") if has_request_context() else "scheduler"
        except Exception:
            user = None
    db.session.add(LogEntry(
        level=level, category=category, message=message[:512], details=details, user=user,
        device_id=device.id if device else None, device_name=device.name if device else None,
    ))
    if commit:
        db.session.commit()


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "device"


def read_backup_file(rel):
    if not rel:
        return ""
    p = (BACKUP_DIR / rel).resolve()
    if BACKUP_DIR.resolve() not in p.parents or not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")
