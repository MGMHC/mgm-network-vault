"""Streaming scans that run one SSH session per switch in parallel: progress events and the NDJSON stream."""
import json
import threading
from contextlib import contextmanager
from datetime import datetime

from . import ssh
from .models import get_int


def scan_workers(total):
    """Parallel SSH sessions for a fleet scan of `total` switches (at least 2 unless fewer switches). App context."""
    return max(1, min(total, max(2, get_int("max_workers"))))


def timestamp():
    return datetime.now().strftime("%H:%M:%S")


def ndjson(q):
    """Yield the events a scan puts on queue q as NDJSON lines, until it puts None."""
    while True:
        item = q.get()
        if item is None:
            return
        yield json.dumps(item) + "\n"


def fleet_progress(q, total, timestamp):
    """Progress for a parallel per-switch scan. Each switch reports a fraction (0..1) as it goes through its
    steps (connect, each command, parse), so the bar moves during the SSH session and not only when a whole
    switch finishes. The bar never goes back, and it holds at 99% until the scan's final event."""
    fracs = {}
    lock = threading.Lock()
    state = {"pct": 0}

    def step(key, device, text, frac, **extra):
        with lock:
            fracs[key] = max(fracs.get(key, 0.0), min(1.0, frac))
            done = sum(1 for f in fracs.values() if f >= 1.0)
            state["pct"] = max(state["pct"], min(99, int(sum(fracs.values()) / max(1, total) * 100)))
            q.put({"type": "progress", "step": f"[{device}] {text}", "percent": state["pct"], "done": done,
                   "total": total, "device": device, "timestamp": timestamp(), **extra})

    return step


class DeviceSteps:
    """Reports one switch's steps to an on_step(text, frac) callback: connecting, then each command, then
    parsing. `commands` is how many commands this platform runs, so each one moves the bar the same amount."""

    def __init__(self, on_step, conn=None):
        self.say = on_step or (lambda text, frac: None)
        self.conn = conn
        self.n = 1
        self.ran = 0

    @contextmanager
    def session(self, device):
        """Open (and always close) the SSH session the steps run on."""
        self.say(f"Connecting to {device.host} over SSH…", 0.05)
        with ssh.session(device) as conn:
            self.conn = conn
            self.say("Connected", 0.35)
            yield self

    def expect(self, commands):
        self.n = max(1, commands)

    def run(self, cmd):
        self.say(f"Running '{cmd}'…", 0.35 + 0.55 * min(self.ran, self.n) / self.n)
        out = ssh.run(self.conn, cmd)
        self.ran += 1
        return out

    def first(self, *cmds, optional=False):
        """Output of the first command the device accepts; "" when optional and none is."""
        for i, cmd in enumerate(cmds):
            try:
                return self.run(cmd)
            except Exception:  # noqa: BLE001 - older firmware names the command differently
                if i == len(cmds) - 1 and not optional:
                    raise
        return ""

    def parsing(self):
        self.say("Parsing output…", 0.92)
