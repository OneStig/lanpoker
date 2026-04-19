"""Logging setup: suppress Werkzeug/SocketIO chatter, pretty host events."""

import logging
import sys


class HostFormatter(logging.Formatter):
    """Compact, readable format for host events."""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        return f"[{ts}] {record.getMessage()}"


def setup_logging() -> logging.Logger:
    """Silence web framework noise, return the host-facing logger."""
    # Suppress Werkzeug request access logs entirely.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("engineio").setLevel(logging.ERROR)
    logging.getLogger("socketio").setLevel(logging.ERROR)
    logging.getLogger("geventwebsocket").setLevel(logging.ERROR)

    host = logging.getLogger("poker")
    host.setLevel(logging.INFO)
    host.propagate = False
    # Avoid duplicate handlers if called twice.
    if not host.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(HostFormatter())
        host.addHandler(h)
    return host
