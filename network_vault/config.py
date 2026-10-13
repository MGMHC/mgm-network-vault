"""Server configuration: config.toml next to run.py, overridable by environment variables.

Precedence: environment variable  >  config.toml  >  built-in default.
Everything else (devices, schedules, retention, GitHub...) lives in the database and is edited in the UI.
"""
import os
import tomllib
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path(os.environ.get("NETWORK_VAULT_CONFIG", APP_ROOT / "config.toml"))

DEFAULTS = {
    "host": "0.0.0.0",          # listen address (0.0.0.0 = all network cards)
    "port": 8080,
    "data_dir": "data",         # database, encryption keys, backups (relative to the app folder)
    "threads": 12,              # web server worker threads
    "behind_proxy": False,      # true when published through IIS / nginx / a load balancer
    "secure_cookies": False,    # true when users reach the app over HTTPS
    # Syslog listener settings
    "syslog_enabled": True,     # Enable syslog listener for event-driven backups
    "syslog_port": 514,         # UDP syslog port (standard 514)
    "syslog_debounce_sec": 45,  # Wait N seconds after last commit log before backing up
    "syslog_log_dir": "D:/NetworkVault/syslog", # Path to save raw syslog daily files
}
ENV = {k: f"NETWORK_VAULT_{k.upper()}" for k in DEFAULTS}

_file = {}
if CONFIG_FILE.exists():
    with open(CONFIG_FILE, "rb") as f:
        _toml = tomllib.load(f)
        _file = _toml.get("server", {})
        # Merge syslog section if present
        if "syslog" in _toml:
            for sk, sv in _toml["syslog"].items():
                _file[f"syslog_{sk}"] = sv


def get(key):
    default = DEFAULTS[key]
    raw = os.environ.get(ENV[key]) or _file.get(key, default)  # empty env var = not set
    if isinstance(default, bool):
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    return str(raw)


def data_dir():
    p = Path(get("data_dir"))
    return p if p.is_absolute() else APP_ROOT / p

