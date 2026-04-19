"""Entry point: `python -m server.run [--host HOST] [--port PORT]`."""

from __future__ import annotations

import argparse
import os
import socket
import sys

from .app import create_app
from .repl import start_repl


def _lan_ip() -> str:
    """Best-effort: discover the machine's LAN IP to print for convenience."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()

    app, socketio, table = create_app()

    lan = _lan_ip()
    print(f"Poker server starting on http://{lan}:{args.port}")
    print("Other devices on your hotspot/LAN should open that URL in a browser.\n")

    def shutdown():
        os._exit(0)

    start_repl(table, on_exit=shutdown)

    socketio.run(app, host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
