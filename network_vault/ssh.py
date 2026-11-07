"""SSH to network devices: Netmiko drivers for the platforms Netmiko lacks, connect with retry, run commands."""
import re
import socket
import time
from contextlib import contextmanager

from netmiko import ConnectHandler

from .models import get_int, log_event
from .util import decrypt

import paramiko
from netmiko.cisco.cisco_s300 import CiscoS300SSH
from netmiko.channel import SSHChannel
from netmiko.exceptions import NetmikoAuthenticationException
from netmiko.ssh_dispatcher import CLASS_MAPPER, platforms


class CiscoC1300SSH(CiscoS300SSH):
    """Cisco C1300 / CBS / SG switch driver with fallback for in-band SSH login.

    On Cisco C1300 and Small Business switches, standard SSH password authentication
    ('ip ssh password-auth') is disabled by factory default. In that case, standard
    Paramiko auth_password fails with 'Bad authentication type; allowed types: []'.
    This driver attempts standard SSH auth first; if that fails, it falls back to
    transport auth_none followed by interactive in-band terminal login ('User Name:', 'Password:').
    """

    def establish_connection(self, width: int = 511, height: int = 1000) -> None:
        try:
            return super().establish_connection(width=width, height=height)
        except NetmikoAuthenticationException:
            return self._establish_inband_connection(width=width, height=height)

    def _establish_inband_connection(self, width: int = 511, height: int = 1000) -> None:
        import time
        self.remote_conn_pre = self._build_ssh_client()
        sock = (self.host, self.port)
        t = paramiko.Transport(sock)
        if self.disabled_algorithms:
            t.disabled_algorithms = self.disabled_algorithms
        t.start_client(timeout=self.conn_timeout)
        try:
            t.auth_none(self.username)
        except Exception:
            pass

        if not t.is_authenticated():
            t.close()
            raise NetmikoAuthenticationException(
                f"Failed to authenticate to {self.host}:{self.port} (neither password nor in-band allowed)"
            )

        # Attach transport to SSHClient wrapper so Netmiko / Paramiko cleanup tears it down cleanly
        if self.remote_conn_pre:
            self.remote_conn_pre._transport = t

        self.remote_conn = t.open_session()
        self.remote_conn.get_pty(term="vt100", width=width, height=height)
        self.remote_conn.invoke_shell()
        self.remote_conn.settimeout(self.blocking_timeout)

        buf = ""
        start = time.time()
        timeout = max(10, self.auth_timeout)
        while time.time() - start < timeout:
            if self.remote_conn.recv_ready():
                chunk = self.remote_conn.recv(1024).decode(self.encoding, errors="ignore")
                buf += chunk
                if any(prompt in buf for prompt in ["User Name:", "User Name :", "login as:"]):
                    break
            time.sleep(0.1)

        if any(prompt in buf for prompt in ["User Name:", "User Name :", "login as:"]):
            self.remote_conn.send((self.username + "\r").encode(self.encoding))
            buf = ""
            start = time.time()
            while time.time() - start < timeout:
                if self.remote_conn.recv_ready():
                    chunk = self.remote_conn.recv(1024).decode(self.encoding, errors="ignore")
                    buf += chunk
                    if "Password:" in buf or "Password :" in buf:
                        break
                time.sleep(0.1)

        if "Password:" in buf or "Password :" in buf:
            self.remote_conn.send((self.password + "\r").encode(self.encoding))
            buf = ""
            start = time.time()
            while time.time() - start < timeout:
                if self.remote_conn.recv_ready():
                    chunk = self.remote_conn.recv(1024).decode(self.encoding, errors="ignore")
                    buf += chunk
                    if any(p in buf for p in [">", "#"]):
                        break
                time.sleep(0.1)

        self.channel = SSHChannel(conn=self.remote_conn, encoding=self.encoding)
        return None

    def disconnect(self) -> None:
        """Gracefully exit and ensure Paramiko transport is terminated to free VTY lines."""
        try:
            super().disconnect()
        finally:
            if self.remote_conn_pre and hasattr(self.remote_conn_pre, "_transport") and self.remote_conn_pre._transport:
                try:
                    self.remote_conn_pre._transport.close()
                except Exception:
                    pass


class SonicWallSonicOSSSH(CiscoS300SSH):
    """Driver for SonicWall firewalls running SonicOS Enhanced (NSa, TZ, SuperMassive).

    Handles SonicOS interactive terminal negotiation, in-band secondary password prompts,
    paging disable ('no cli pager session' on SonicOS 7/8, 'no cli pager' on SonicOS 6),
    and command execution over SSH.
    """

    def establish_connection(self, width: int = 511, height: int = 1000) -> None:
        super().establish_connection(width=width, height=height)
        # Check if SonicOS presents an interactive in-band Password: prompt after SSH banner
        import time
        time.sleep(0.5)
        buf = ""
        start = time.time()
        while time.time() - start < 4:
            if self.remote_conn.recv_ready():
                buf += self.remote_conn.recv(1024).decode(self.encoding, errors="ignore")
                if "password:" in buf.lower():
                    break
            time.sleep(0.1)

        if "password:" in buf.lower():
            self.remote_conn.send((self.password + "\n").encode(self.encoding))
            time.sleep(0.5)
            start = time.time()
            while time.time() - start < 6:
                if self.remote_conn.recv_ready():
                    buf += self.remote_conn.recv(1024).decode(self.encoding, errors="ignore")
                    if any(p in buf for p in [">", "#"]):
                        break
                time.sleep(0.1)

    def session_preparation(self) -> None:
        self._test_channel_read(pattern=r"[>#]")
        self.set_base_prompt()
        self.disable_paging()

    def disable_paging(self, command: str = "no cli pager session", delay_factor: float = 1.0) -> str:
        output = ""
        for cmd in ("no cli pager session", "no cli pager"):
            try:
                output = self.send_command_timing(cmd)
                if "% Error" not in output:
                    break
            except Exception:
                pass
        return output


CLASS_MAPPER["cisco_s300"] = CiscoC1300SSH
CLASS_MAPPER["sonicwall_sonicos"] = SonicWallSonicOSSSH
if "sonicwall_sonicos" not in platforms:
    platforms.append("sonicwall_sonicos")

VC_PROMPT_LINE = re.compile(r"^\{(master|backup|linecard)(:\d+)?\}\s*$", re.I)


def connect(device, retries=2, backoff_base=3):

    cred = device.credential
    if not cred:
        raise RuntimeError("No credential profile assigned to this device")
    t = get_int("ssh_timeout")
    params = dict(
        device_type=device.profile.netmiko,
        host=device.host, port=device.port or 22,
        username=cred.username, password=decrypt(cred.password_enc),
        secret=decrypt(cred.secret_enc),
        conn_timeout=t, auth_timeout=t, banner_timeout=t, timeout=t,
        fast_cli=False, global_delay_factor=1,
    )
    if device.legacy_ssh:
        # Old Junos (EX2200/EX3300 on 12.x) and older Cisco: force classic ssh-rsa signatures
        params["disabled_algorithms"] = {"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}

    attempt = 0
    while True:
        try:
            conn = ConnectHandler(**params)
            if device.profile.family == "cisco" and params["secret"]:
                try:
                    if not conn.check_enable_mode():
                        conn.enable()
                except Exception as e:  # noqa: BLE001 - read-only commands may still work unprivileged
                    log_event(f"Could not enter enable mode on {device.name}: {e}", "backup", "WARN", device)
            return conn
        except Exception as e:
            msg = str(e).lower()
            # Retry transient SSH handshake / banner / connection drops (e.g. switch VTY exhaustion or CPU spike)
            is_transient = any(needle in msg for needle in (
                "error reading ssh protocol banner",
                "eof",
                "connection reset",
                "connection closed",
                "banner_timeout",
                "tcp connection to device failed",
            ))
            if is_transient and attempt < retries:
                attempt += 1
                delay = backoff_base * attempt
                log_event(
                    f"Transient SSH handshake issue on {device.name} ({e}); retrying in {delay}s (attempt {attempt}/{retries})",
                    "backup", "WARN", device, commit=True
                )
                time.sleep(delay)
                continue

            # If banner failed, check if switch sent a clear text disconnect message (e.g. VTY line limit reached)
            if "error reading ssh protocol banner" in msg:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(2.0)
                    s.connect((device.host, device.port or 22))
                    raw_banner = s.recv(512).decode("ascii", errors="ignore").strip()
                    s.close()
                    if "maximum number of simultaneous connections" in raw_banner.lower():
                        raise RuntimeError(
                            f"Switch VTY line exhaustion on {device.name} ({device.host}): "
                            f"Server reached maximum number of simultaneous connections. "
                            f"Clear idle SSH sessions on switch via console/web or reduce exec-timeout."
                        ) from e
                    elif raw_banner and not raw_banner.startswith("SSH-"):
                        raise RuntimeError(
                            f"SSH protocol banner rejected by {device.name} ({device.host}): {raw_banner}"
                        ) from e
                except RuntimeError:
                    raise
                except Exception:
                    pass

            raise


@contextmanager
def session(device, retries=2):
    """An SSH session to device that is always closed afterwards."""
    conn = connect(device, retries=retries)
    try:
        yield conn
    finally:
        try:
            conn.disconnect()
        except Exception:  # noqa: BLE001 - the session is gone either way
            pass


def run(conn, cmd):
    out = conn.send_command(cmd, read_timeout=get_int("read_timeout"), strip_prompt=True, strip_command=True)
    return clean(out)


def clean(text):
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and (not lines[-1].strip() or VC_PROMPT_LINE.match(lines[-1].strip())):
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(l.rstrip() for l in lines) + "\n"
