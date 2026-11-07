"""Shared helpers: data folders, secrets, backup file access."""
import os
import re
import secrets

from cryptography.fernet import Fernet, InvalidToken

from . import config
from .portable import FLASK_KEY_FILE, SECRET_KEY_FILE, apply_pending_restore

NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console window for git/ping

BASE_DIR = config.data_dir()
BASE_DIR.mkdir(parents=True, exist_ok=True)
# a restore uploaded earlier is swapped in here - before the keys and database are opened
apply_pending_restore(BASE_DIR)
BACKUP_DIR = config.backup_dir()
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _key_file(name, factory):
    p = BASE_DIR / name
    if not p.exists():
        p.write_bytes(factory())
    return p.read_bytes().strip()


FLASK_SECRET = _key_file(FLASK_KEY_FILE, lambda: secrets.token_hex(32).encode())
_fernet = Fernet(_key_file(SECRET_KEY_FILE, Fernet.generate_key))


def encrypt(text):
    return _fernet.encrypt((text or "").encode()).decode()


def decrypt(token):
    if not token:
        return ""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "device"


def read_backup_file(rel):
    if not rel:
        return ""
    p = (BACKUP_DIR / rel).resolve()
    if BACKUP_DIR.resolve() not in p.parents or not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")
