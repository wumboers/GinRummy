"""Host-authoritative LAN server for the Gin Rummy application."""

from __future__ import annotations

import queue
import socket
import threading
from typing import Any

from engine import (
    continue_after_round,
    discard_card,
    draw_from_discard,
    draw_from_stock,
    make_initial_state,
    make_public_state,
    rename_player,
    set_pending_knock,
    set_sort_mode,
)
from net import create_server_socket, guess_local_ip, recv_messages, send_message


DEFAULT_PORT = 43851


def make_server_context(host: str = "0.0.0.0", port: int = DEFAULT_PORT, seed: int | None = None) -> dict[str, Any]:
    """Create a mutable server context.

    Args:
        host: Bind address.
        port: TCP port.
        seed: Optional RNG seed.

    Returns:
        Server context dictionary.
    """
    return {
        "host": host,
        "port": port,
        "state": make_initial_state(seed=seed),
        "server_socket": None,
        "client_sockets": {},
        "client_buffers": {},
        "event_queue": queue.Queue(),
        "running": False,
        "threads": [],
        "lock": threading.Lock(),
        "local_ip": guess_local_ip(),
    }


def start_server(context: dict[str, Any]) -> None:
    """Start the listening socket and background networking threads.

    Args:
        context: Server context.
    """
    server_socket = create_server_socket(context["host"], context["port"])
    context["server_socket"] = server_socket
    context["running"] = True

    accept_thread = threading.Thread(target=accept_loop, args=(context,), daemon=True)
    accept_thread.start()
    context["threads"].append(accept_thread)


def stop_server(context: dict[str, Any]) -> None:
    """Stop the server and close sockets.

    Args:
        context: Server context.
    """
    context["running"] = False
    try:
        if context["server_socket"] is not None:
            context["server_socket"].close()
    except OSError:
        pass

    for sock in list(context["client_sockets"].values()):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


def accept_loop(context: dict[str, Any]) -> None:
    """Accept exactly two player sockets and start per-client readers.

    Args:
        context: Server context.
    """
    while context["running"]:
        try:
            sock, _ = context["server_socket"].accept()
        except OSError:
            break

        with context["lock"]:
            occupied = set(context["client_sockets"].keys())
            if 0 not in occupied:
                player_index = 0
            elif 1 not in occupied:
                player_index = 1
            else:
                send_message(sock, {"type": "fatal", "message": "This match already has two players connected."})
                sock.close()
                continue
            context["client_sockets"][player_index] = sock
            context["client_buffers"][player_index] = b""

        send_message(sock, {"type": "assign", "player": player_index})
        send_message(sock, {"type": "state", "state": make_public_state(context["state"], player_index)})

        reader = threading.Thread(target=client_reader_loop, args=(context, player_index), daemon=True)
        reader.start()
        context["threads"].append(reader)


def client_reader_loop(context: dict[str, Any], player_index: int) -> None:
    """Read messages from one client and push them into the server queue.

    Args:
        context: Server context.
        player_index: Bound player index.
    """
    sock = context["client_sockets"][player_index]
    buffer = context["client_buffers"][player_index]
    try:
        while context["running"]:
            messages, buffer = recv_messages(sock, buffer)
            for message in messages:
                context["event_queue"].put((player_index, message))
    except (ConnectionError, OSError):
        context["event_queue"].put((player_index, {"type": "disconnect"}))
    finally:
        context["client_buffers"][player_index] = buffer


def broadcast_state(context: dict[str, Any]) -> None:
    """Send the latest redacted state to all connected players.

    Args:
        context: Server context.
    """
    for player_index, sock in list(context["client_sockets"].items()):
        try:
            send_message(sock, {"type": "state", "state": make_public_state(context["state"], player_index)})
        except OSError:
            context["event_queue"].put((player_index, {"type": "disconnect"}))


def send_error(context: dict[str, Any], player_index: int, message: str) -> None:
    """Send a recoverable error message to one player.

    Args:
        context: Server context.
        player_index: Target player index.
        message: Error text.
    """
    sock = context["client_sockets"].get(player_index)
    if sock is None:
        return
    try:
        send_message(sock, {"type": "error", "message": message})
    except OSError:
        pass


def process_one_event(context: dict[str, Any], timeout: float = 0.05) -> bool:
    """Process at most one queued client event.

    Args:
        context: Server context.
        timeout: Queue wait timeout in seconds.

    Returns:
        True when an event was processed, otherwise False.
    """
    try:
        player_index, message = context["event_queue"].get(timeout=timeout)
    except queue.Empty:
        return False

    handle_client_message(context, player_index, message)
    return True


def handle_client_message(context: dict[str, Any], player_index: int, message: dict[str, Any]) -> None:
    """Apply one client command to the authoritative game state.

    Args:
        context: Server context.
        player_index: Player who sent the message.
        message: Decoded JSON message.
    """
    message_type = message.get("type")

    if message_type == "disconnect":
        sock = context["client_sockets"].pop(player_index, None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        context["state"]["log"].append(f"{context['state']['players'][player_index]['name']} disconnected.")
        broadcast_state(context)
        return

    if message_type == "hello":
        rename_player(context["state"], player_index, str(message.get("name", "")).strip())
        broadcast_state(context)
        return

    if message_type != "action":
        send_error(context, player_index, "Unknown message type.")
        return

    action = message.get("action")
    try:
        if action == "draw_stock":
            draw_from_stock(context["state"], player_index)
        elif action == "draw_discard":
            draw_from_discard(context["state"], player_index)
        elif action == "discard":
            discard_card(context["state"], player_index, str(message.get("card", "")))
        elif action == "set_knock":
            set_pending_knock(context["state"], player_index, bool(message.get("value", False)))
        elif action == "sort":
            set_sort_mode(context["state"], player_index, str(message.get("mode", "rank")))
        elif action == "continue":
            continue_after_round(context["state"])
        else:
            raise ValueError("Unknown action.")
    except ValueError as exc:
        send_error(context, player_index, str(exc))
        broadcast_state(context)
        return

    broadcast_state(context)
