"""Socket and JSON protocol helpers for the LAN Gin Rummy application."""

from __future__ import annotations

import json
import socket
from typing import Any


BUFFER_SIZE = 65536
MAX_MESSAGE_BYTES = 16384


def send_message(sock: socket.socket, payload: dict[str, Any]) -> None:
    """Send one newline-delimited JSON message.

    Args:
        sock: Connected TCP socket.
        payload: JSON-serializable dictionary to send.
    """
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
    sock.sendall(data)


def recv_messages(sock: socket.socket, buffer: bytes) -> tuple[list[dict[str, Any]], bytes]:
    """Receive zero or more complete newline-delimited JSON messages.

    Args:
        sock: Connected TCP socket.
        buffer: Any previously buffered incomplete bytes.

    Returns:
        Tuple containing a list of decoded messages and the remaining partial buffer.
    """
    chunk = sock.recv(BUFFER_SIZE)
    if not chunk:
        raise ConnectionError("Socket closed by peer.")

    data = buffer + chunk
    if len(data) > MAX_MESSAGE_BYTES and b"\n" not in data:
        raise ValueError("Message too large.")
    parts = data.split(b"\n")
    messages: list[dict[str, Any]] = []
    for raw in parts[:-1]:
        if raw.strip():
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError("Message too large.")
            messages.append(json.loads(raw.decode("utf-8")))
    if len(parts[-1]) > MAX_MESSAGE_BYTES:
        raise ValueError("Message too large.")
    return messages, parts[-1]


def create_server_socket(host: str, port: int) -> socket.socket:
    """Create, bind, and listen on a TCP server socket.

    Args:
        host: Bind address, usually `0.0.0.0`.
        port: TCP port to listen on.

    Returns:
        Listening socket.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(2)
    return server


def create_client_socket(host: str, port: int, timeout: float = 10.0) -> socket.socket:
    """Create and connect a TCP client socket.

    Args:
        host: Remote server host or IP address.
        port: Remote TCP port.
        timeout: Connection timeout in seconds.

    Returns:
        Connected socket.
    """
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(timeout)
    client.connect((host, port))
    client.settimeout(None)
    return client


def guess_local_ip() -> str:
    """Best-effort LAN IP detection for display in the host UI.

    Returns:
        Detected local IP address, or `127.0.0.1` as a fallback.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
