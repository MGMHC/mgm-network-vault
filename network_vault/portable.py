"""Export / import of the complete installation, and the application package for deployment.

Export file (*.nvault): a ZIP of the data folder, encrypted with a passphrase
(scrypt key derivation + AES-256-GCM in 1 MiB chunks). It contains everything needed to move
the vault to another server:

    manifest.json               what was exported, from where, counts
    data/network_vault.db       devices, backup index, schedules, logs, users, settings (incl. GitHub)
    data/secret.key             key that decrypts the stored switch / GitHub credentials
    data/flask.key              session signing key
    data/backups/...            every stored configuration file
    config.toml                 server config of the source machine (reference only, never auto-applied)

The local GitHub working copy (data/git-repo) is not exported - it is re-downloaded from GitHub.

Restoring is two-step so the database is never replaced while it is in use: the archive is verified
and unpacked to data/restore-pending/, and swapped in on the next start (the previous data is kept
in data/pre-restore-<timestamp>/).

This module must not import .util (util reads the key files; the swap has to happen before that).
"""
import io
import json
import os
import shutil
import socket
import sqlite3
import struct
import zipfile
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = 1
MAGIC = b"NVAULTX1"
CHUNK = 1024 * 1024
APP_NAME = "MGM Network Vault"
DATA_FILES = ("network_vault.db", "secret.key", "flask.key")
PENDING = "restore-pending"


class ExportError(Exception):
    pass


# --------------------------------------------------------------------------- crypto

def _key(passphrase, salt):
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())


def _encrypt_file(src, dst, passphrase):
    salt = os.urandom(16)
    aes = AESGCM(_key(passphrase, salt))
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        fo.write(MAGIC + salt)
        idx = 0
        block = fi.read(CHUNK)
        while True:
            nxt = fi.read(CHUNK)
            last = not nxt
            nonce = os.urandom(12)
            ct = aes.encrypt(nonce, block, struct.pack(">Q?", idx, last))
            fo.write(struct.pack(">?I", last, len(ct)) + nonce + ct)
            if last:
                break
            block, idx = nxt, idx + 1


def _decrypt_file(src, dst, passphrase):
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        head = fi.read(len(MAGIC) + 16)
        if not head.startswith(MAGIC):
            raise ExportError("This is not an MGM Network Vault export file (.nvault).")
        aes = AESGCM(_key(passphrase, head[len(MAGIC):]))
        idx = 0
        while True:
            hdr = fi.read(5)
            if len(hdr) < 5:
                raise ExportError("The export file is incomplete (truncated during copy?).")
            last, n = struct.unpack(">?I", hdr)
            nonce, ct = fi.read(12), fi.read(n)
            try:
                fo.write(aes.decrypt(nonce, ct, struct.pack(">Q?", idx, last)))
            except InvalidTag:
                raise ExportError("Wrong passphrase, or the export file is damaged.") from None
            if last:
                break
            idx += 1


# --------------------------------------------------------------------------- export

def _counts(db_path):
    con = sqlite3.connect(db_path)
    try:
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                for t in ("device", "backup", "schedule", "credential", "user", "log_entry")}
    finally:
        con.close()


def export_archive(base_dir, out_path, passphrase, config_file=None):
    """Write an encrypted export of the data folder to out_path. Returns the manifest."""
    if len(passphrase or "") < 10:
        raise ExportError("Use a passphrase of at least 10 characters.")
    base_dir = Path(base_dir)
    tmp = base_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    db_copy, zip_path = tmp / f"export-{stamp}.db", tmp / f"export-{stamp}.zip"
    try:
        # consistent snapshot even while backups are running
        src = sqlite3.connect(base_dir / "network_vault.db")
        dst = sqlite3.connect(db_copy)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()

        backup_files = [p for p in (base_dir / "backups").rglob("*") if p.is_file()]
        manifest = {
            "format": FORMAT, "app": APP_NAME,
            "created": datetime.now().isoformat(timespec="seconds"),
            "source_host": socket.gethostname(), "source_data_dir": str(base_dir),
            "counts": _counts(db_copy), "backup_files": len(backup_files),
        }
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.write(db_copy, "data/network_vault.db")
            for name in ("secret.key", "flask.key"):
                z.write(base_dir / name, f"data/{name}")
            for p in backup_files:
                z.write(p, "data/" + p.relative_to(base_dir).as_posix())
            if config_file and Path(config_file).exists():
                z.write(config_file, "config.toml")
        _encrypt_file(zip_path, out_path, passphrase)
        return manifest
    finally:
        for p in (db_copy, zip_path):
            p.unlink(missing_ok=True)


# --------------------------------------------------------------------------- import

def stage_restore(base_dir, in_path, passphrase):
    """Decrypt + verify an export and unpack it to data/restore-pending. Returns the manifest."""
    base_dir = Path(base_dir)
    tmp = base_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    zip_path = tmp / f"import-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
    pending = base_dir / PENDING
    try:
        _decrypt_file(in_path, zip_path, passphrase)
        try:
            z = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile:
            raise ExportError("The export file is damaged.") from None
        with z:
            names = z.namelist()
            try:
                manifest = json.loads(z.read("manifest.json"))
            except KeyError:
                raise ExportError("The export has no manifest - not a Network Vault export.") from None
            if manifest.get("app") != APP_NAME or manifest.get("format", 99) > FORMAT:
                raise ExportError("This export was made by a different or newer version of the app.")
            missing = [f for f in DATA_FILES if f"data/{f}" not in names]
            if missing:
                raise ExportError(f"The export is incomplete (missing {', '.join(missing)}).")
            for n in names:  # no absolute paths / path traversal
                if n.startswith(("/", "\\")) or ".." in Path(n).parts or ":" in n:
                    raise ExportError(f"Unsafe path in export: {n}")
            if z.testzip() is not None:
                raise ExportError("The export file is damaged (checksum error).")
            if pending.exists():
                shutil.rmtree(pending)
            pending.mkdir()
            for n in names:
                if n.startswith("data/") and not n.endswith("/"):
                    target = pending / n
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(n) as fi, open(target, "wb") as fo:
                        shutil.copyfileobj(fi, fo)
            if "config.toml" in names:
                (pending / "config.toml.from-export").write_bytes(z.read("config.toml"))
        # sanity check the database before we accept it
        con = sqlite3.connect(pending / "data" / "network_vault.db")
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ExportError("The database inside the export is damaged.")
        finally:
            con.close()
        manifest["staged"] = datetime.now().isoformat(timespec="seconds")
        (pending / "READY.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
    except Exception:
        if pending.exists() and not (pending / "READY.json").exists():
            shutil.rmtree(pending, ignore_errors=True)
        raise
    finally:
        zip_path.unlink(missing_ok=True)


def pending_restore(base_dir):
    f = Path(base_dir) / PENDING / "READY.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def cancel_restore(base_dir):
    shutil.rmtree(Path(base_dir) / PENDING, ignore_errors=True)


def apply_pending_restore(base_dir):
    """Swap a staged restore into place. Called at start-up before anything opens the data files."""
    base_dir = Path(base_dir)
    pending = base_dir / PENDING
    ready = pending / "READY.json"
    if not ready.exists():
        return None
    manifest = json.loads(ready.read_text(encoding="utf-8"))
    keep = base_dir / f"pre-restore-{datetime.now():%Y%m%d-%H%M%S}"
    keep.mkdir()
    for item in base_dir.iterdir():
        if item.name == PENDING or item.name.startswith("pre-restore-") or item == keep:
            continue
        shutil.move(str(item), str(keep / item.name))
    for item in (pending / "data").iterdir():
        shutil.move(str(item), str(base_dir / item.name))
    if (pending / "config.toml.from-export").exists():
        shutil.move(str(pending / "config.toml.from-export"), str(base_dir / "config.toml.from-export"))
    manifest["previous_data_kept_in"] = str(keep)
    (base_dir / "restored.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    shutil.rmtree(pending, ignore_errors=True)
    return manifest


# --------------------------------------------------------------------------- application package

PACKAGE_EXCLUDE_DIRS = {".venv", "data", "logs", "__pycache__", ".git", "node_modules"}
PACKAGE_EXCLUDE_FILES = {"config.toml"}  # server-specific; config.example.toml is included


def app_package(app_root):
    """ZIP (bytes) of the application code + deployment kit, without data or the virtualenv."""
    app_root = Path(app_root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(app_root.rglob("*")):
            rel = p.relative_to(app_root)
            if not p.is_file() or PACKAGE_EXCLUDE_DIRS & set(rel.parts) or rel.name in PACKAGE_EXCLUDE_FILES \
                    or p.suffix in (".pyc", ".log", ".nvault"):
                continue
            z.write(p, "network-vault/" + rel.as_posix())
    return buf.getvalue()
