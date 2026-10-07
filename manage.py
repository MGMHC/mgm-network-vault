"""MGM Network Vault - command line tools.

    python manage.py export  <file.nvault>        encrypted export of everything (safe while the app runs)
    python manage.py import  <file.nvault>        restore an export (stop the app first)
    python manage.py package <file.zip>           application package for installing on another server
    python manage.py reset-password <user>        set a new password for a web user (e.g. forgotten admin)
    python manage.py info                         show config file, data folder and counts

The passphrase is asked for interactively, or read from NETWORK_VAULT_PASSPHRASE (for scripted exports).
"""
import argparse
import getpass
import os
import socket
import sys
from pathlib import Path

from network_vault import config, portable


def _passphrase(confirm):
    p = os.environ.get("NETWORK_VAULT_PASSPHRASE")
    if p:
        return p
    p = getpass.getpass("Passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != p:
        sys.exit("Passphrases do not match.")
    return p


def _app_running():
    try:
        socket.create_connection(("127.0.0.1", config.get("port")), timeout=2).close()
        return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description="MGM Network Vault tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export").add_argument("file")
    sub.add_parser("import").add_argument("file")
    sub.add_parser("package").add_argument("file")
    sub.add_parser("reset-password").add_argument("user")
    sub.add_parser("info")
    from network_vault.version import __version__
    ap.add_argument("--version", action="version", version=f"MGM Network Vault {__version__}")
    a = ap.parse_args()

    from network_vault.util import BASE_DIR  # noqa: E402 - after argparse so --help works without data

    if a.cmd == "export":
        m = portable.export_archive(BASE_DIR, a.file, _passphrase(confirm=True), config.CONFIG_FILE)
        print(f"Exported {m['counts']['device']} devices, {m['counts']['backup']} backups, "
              f"{m['backup_files']} files -> {a.file}")
    elif a.cmd == "import":
        if _app_running():
            sys.exit("The app is running on this machine. Stop it (or the service) first, "
                     "or upload the export on the Settings > Export & migrate page instead.")
        m = portable.stage_restore(BASE_DIR, a.file, _passphrase(confirm=False))
        done = portable.apply_pending_restore(BASE_DIR)
        print(f"Restored export of {m['source_host']} ({m['created']}): {m['counts']['device']} devices, "
              f"{m['counts']['backup']} backups.\nPrevious data kept in {done['previous_data_kept_in']}\n"
              "Start the app again. Server settings from the old machine are in "
              f"{BASE_DIR / 'config.toml.from-export'} (not applied automatically).")
    elif a.cmd == "package":
        Path(a.file).write_bytes(portable.app_package(config.APP_ROOT))
        print(f"Application package written to {a.file}")
    elif a.cmd == "reset-password":
        from network_vault import create_app
        from network_vault.models import User, db
        app = create_app()
        with app.app_context():
            u = User.query.filter_by(username=a.user).first()
            if not u:
                sys.exit(f"No user named {a.user}")
            pw = getpass.getpass("New password (min 8 chars): ")
            if len(pw) < 8 or getpass.getpass("Repeat: ") != pw:
                sys.exit("Password too short or does not match.")
            u.set_password(pw)
            u.must_change = False
            db.session.commit()
            print(f"Password for {a.user} changed.")
    elif a.cmd == "info":
        from network_vault.version import __version__
        print(f"Version:     {__version__}")
        print(f"Config file: {config.CONFIG_FILE} ({'found' if config.CONFIG_FILE.exists() else 'not found - defaults'})")
        print(f"Data folder: {BASE_DIR}")
        print(f"Listening:   {config.get('host')}:{config.get('port')}  (running: {_app_running()})")
        db = BASE_DIR / "network_vault.db"
        if db.exists():
            print("Counts:     ", portable._counts(db))


if __name__ == "__main__":
    try:
        main()
    except portable.ExportError as e:
        sys.exit(f"Error: {e}")
    except FileNotFoundError as e:
        sys.exit(f"Error: file not found: {e.filename}")
