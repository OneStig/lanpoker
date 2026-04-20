"""Flask + SocketIO server. Handlers are thin; all logic is in Table/engine."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, request, send_from_directory
from flask_socketio import SocketIO, emit

from .logging_setup import setup_logging
from .table import Table

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> tuple[Flask, SocketIO, Table]:
    host_log = setup_logging()

    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
    app.config["SECRET_KEY"] = "poker-lan"  # LAN only; no auth surface worth protecting

    socketio = SocketIO(
        app,
        async_mode="threading",
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
    )

    def broadcast(payload: dict) -> None:
        socketio.emit("table", payload)

    def private(username: str, payload: dict) -> None:
        sid = table.username_to_sid.get(username)
        if sid:
            socketio.emit("private", payload, to=sid)

    table = Table(on_broadcast=broadcast, on_private=private, on_log=host_log.info)

    def broadcast_state() -> None:
        broadcast({"type": "state", "state": table.public_state()})

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @socketio.on("connect")
    def on_connect():
        table.connect(request.sid)
        emit("table", {"type": "state", "state": table.public_state(), "new_hand": False})

    @socketio.on("disconnect")
    def on_disconnect():
        table.disconnect(request.sid)

    @socketio.on("hello")
    def on_hello(data):
        username = (data or {}).get("username", "")
        if username and table.attach_username(request.sid, username):
            emit("hello_result", {"ok": True, "username": username})
            emit("table", {"type": "state", "state": table.public_state(), "new_hand": False})
            hole = table.hole_for(username)
            if hole:
                emit("private", {"type": "hole", "cards": hole})
        else:
            emit("hello_result", {"ok": False})

    @socketio.on("join")
    def on_join(data):
        data = data or {}
        username = data.get("username", "")
        stack = int(data.get("stack", 0))
        ok, msg = table.request_join(request.sid, username, stack)
        emit("join_result", {"ok": ok, "message": msg, "username": username if ok else None})
        if ok:
            broadcast_state()

    @socketio.on("request_seat")
    def on_request_seat(data):
        username = (data or {}).get("username", "")
        ok, msg = table.request_seat(username)
        emit("action_result", {"ok": ok, "message": msg})
        if ok:
            broadcast_state()

    @socketio.on("action")
    def on_action(data):
        data = data or {}
        username = data.get("username", "")
        action = data.get("action", "")
        amount = int(data.get("amount", 0))
        ok, msg = table.player_action(username, action, amount)
        emit("action_result", {"ok": ok, "message": msg})

    @socketio.on("legal_actions")
    def on_legal_actions(data):
        username = (data or {}).get("username", "")
        emit("legal_actions", table.legal_actions_for(username))

    return app, socketio, table
