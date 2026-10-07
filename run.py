"""Start MGM Network Vault: web UI + background scheduler.

    python run.py

Server settings (port, data folder, proxy...) are read from config.toml - see config.example.toml.
Exit code 3 means "restart requested" (after a restore); start.bat and the Windows service loop on it.
"""
import logging
import socket
import sys

from waitress import serve

from network_vault import config, create_app
from network_vault.scheduler import init_scheduler
from network_vault.util import BASE_DIR
from network_vault.version import __version__

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

app = create_app()

if __name__ == "__main__":
    host, port = config.get("host"), config.get("port")
    # Windows lets a second server bind the same port, which would also run every schedule twice.
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        logging.error("Port %s is already in use - MGM Network Vault (or another program) is already "
                      "running. Not starting a second copy.", port)
        sys.exit(1)
    except OSError:
        pass
    init_scheduler(app)
    # 0.0.0.0 means "all network cards" - it is not an address a browser can open, so print real ones
    if host in ("0.0.0.0", ""):
        try:
            ips = sorted({i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
                         - {"127.0.0.1"})
        except OSError:
            ips = []
        urls = [f"http://localhost:{port}"] + [f"http://{ip}:{port}" for ip in ips if not ip.startswith("169.254.")]
    else:
        urls = [f"http://{host}:{port}"]
    logging.info("Config: %s | data: %s", config.CONFIG_FILE if config.CONFIG_FILE.exists() else "defaults", BASE_DIR)
    logging.info("MGM Network Vault %s is running. Open in a browser:\n    %s", __version__, "\n    ".join(urls))
    serve(app, host=host, port=port, threads=config.get("threads"))  # proxy headers: see behind_proxy
