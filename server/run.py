"""Entry point: `python -m server.run [--host HOST] [--port PORT]`."""

from __future__ import annotations

import argparse
import os
import socket

from .app import create_app
from .repl import start_repl


def _lan_ip() -> str:
    """Best-effort local LAN IP, used only to print a connect URL for the host."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()

    app, socketio, table = create_app()

    print(f"Poker server starting on http://{_lan_ip()}:{args.port}")
    print("Other devices on your hotspot/LAN should open that URL in a browser.\n")

    start_repl(table, on_exit=lambda: os._exit(0))
    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()
