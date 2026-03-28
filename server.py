"""Host-authoritative LAN server for the Gin Rummy application."""

from __future__ import annotations

import hashlib
import hmac
import json
import queue
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine import (
    append_log,
    card_value,
    continue_after_round,
    discard_card,
    draw_from_discard,
    draw_from_stock,
    evaluate_hand,
    make_initial_state,
    make_public_state,
    rename_player,
    set_pending_knock,
    set_sort_mode,
)
from net import create_server_socket, guess_local_ip, recv_messages, send_message


DEFAULT_PORT = 43851
AUTH_TIMEOUT_SECONDS = 10.0
RECONNECT_GRACE_SECONDS = 60.0
AUTH_FAILURE_WINDOW_SECONDS = 60.0
MAX_AUTH_FAILURES = 5
DEFAULT_TURN_TIMEOUT_SECONDS = 0


def _default_audit_log_path(port: int) -> str:
    """Return a timestamped local audit log path for one host session."""
    logs_dir = Path(__file__).resolve().parent / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(logs_dir / f"ginrummy_audit_{stamp}_{port}.jsonl")


def _append_audit_line(context: dict[str, Any], event: str, **payload: Any) -> None:
    """Append one JSON audit event to the local host log file."""
    path = context.get("audit_log_path")
    if not path:
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **payload,
    }
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def _audit_round_setup(context: dict[str, Any], reason: str) -> None:
    """Record hidden round setup details for later fairness analysis."""
    state = context["state"]
    round_state = state["round"]
    _append_audit_line(
        context,
        "round_setup",
        reason=reason,
        round_index=state["round_index"],
        dealer=state["dealer"],
        player_names=[player["name"] for player in state["players"]],
        hands=[list(hand) for hand in round_state["hands"]],
        discard=list(round_state["discard"]),
        stock_count=len(round_state["stock"]),
        stock_order=list(round_state["stock"]),
    )


def _audit_state_snapshot(context: dict[str, Any], label: str) -> None:
    """Record a compact hidden-state snapshot after a meaningful action."""
    state = context["state"]
    round_state = state["round"]
    _append_audit_line(
        context,
        "state_snapshot",
        label=label,
        round_index=state["round_index"],
        scores=list(state["scores"]),
        match_wins=list(state.get("match_wins", [0, 0])),
        turn=round_state["turn"],
        stage=round_state["stage"],
        pending_knock=bool(round_state["pending_knock"]),
        stock_count=len(round_state["stock"]),
        discard_top=round_state["discard"][-1] if round_state["discard"] else None,
        hands=[list(hand) for hand in round_state["hands"]],
        last_drawn=list(round_state["last_drawn"]),
        round_over=bool(round_state["round_over"]),
        summary=round_state["summary"],
    )


def _hash_password(password: str) -> str:
    """Return a stable hash for a shared game password."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _sanitize_chat_message(message: str) -> str:
    """Return a safe plain-text chat message without control characters."""
    collapsed = " ".join(str(message).replace("\r", " ").replace("\n", " ").split())
    sanitized = "".join(ch for ch in collapsed if ch.isprintable())
    return sanitized[:300]


def make_server_context(
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    seed: int | None = None,
    password: str = "",
    turn_timeout_seconds: int = DEFAULT_TURN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Create a mutable server context.

    Args:
        host: Bind address.
        port: TCP port.
        seed: Optional RNG seed.
        password: Optional shared password required to join.

    Returns:
        Server context dictionary.
    """
    state = make_initial_state(seed=seed)
    context = {
        "host": host,
        "port": port,
        "state": state,
        "server_socket": None,
        "connections": {},
        "player_connections": {},
        "reserved_slots": {},
        "auth_failures": {},
        "next_connection_id": 1,
        "event_queue": queue.Queue(),
        "running": False,
        "threads": [],
        "lock": threading.Lock(),
        "local_ip": guess_local_ip(),
        "password_hash": _hash_password(password) if password else None,
        "turn_timeout_seconds": max(0, int(turn_timeout_seconds)),
        "turn_deadline_monotonic": None,
        "audit_log_path": _default_audit_log_path(port),
    }
    state["log_sink"] = lambda message: _append_audit_line(
        context,
        "event_log",
        round_index=context["state"]["round_index"],
        message=message,
    )
    return context


def start_server(context: dict[str, Any]) -> None:
    """Start the listening socket and background networking threads."""
    server_socket = create_server_socket(context["host"], context["port"])
    context["server_socket"] = server_socket
    context["running"] = True
    _reset_turn_deadline(context)
    _append_audit_line(
        context,
        "session_started",
        host=context["host"],
        port=context["port"],
        local_ip=context.get("local_ip"),
        turn_timeout_seconds=context.get("turn_timeout_seconds", 0),
    )
    _audit_round_setup(context, reason="session_started")

    accept_thread = threading.Thread(target=accept_loop, args=(context,), daemon=True)
    accept_thread.start()
    context["threads"].append(accept_thread)


def stop_server(context: dict[str, Any]) -> None:
    """Stop the server and close sockets."""
    context["running"] = False
    _append_audit_line(context, "session_stopped")
    try:
        if context["server_socket"] is not None:
            context["server_socket"].close()
    except OSError:
        pass

    for connection in list(context["connections"].values()):
        sock = connection.get("socket")
        if sock is None:
            continue
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


def accept_loop(context: dict[str, Any]) -> None:
    """Accept inbound sockets and start per-client readers."""
    while context["running"]:
        try:
            sock, addr = context["server_socket"].accept()
        except OSError:
            break

        peer_ip = str(addr[0]) if addr else ""
        with context["lock"]:
            connection_id = context["next_connection_id"]
            context["next_connection_id"] += 1
            context["connections"][connection_id] = {
                "socket": sock,
                "buffer": b"",
                "connected_at": time.monotonic(),
                "peer_ip": peer_ip,
                "player_index": None,
            }

        reader = threading.Thread(target=client_reader_loop, args=(context, connection_id), daemon=True)
        reader.start()
        context["threads"].append(reader)


def client_reader_loop(context: dict[str, Any], connection_id: int) -> None:
    """Read messages from one client and push them into the server queue."""
    while context["running"]:
        connection = context["connections"].get(connection_id)
        if connection is None:
            return

        sock = connection["socket"]
        buffer = connection["buffer"]
        try:
            messages, buffer = recv_messages(sock, buffer)
            connection["buffer"] = buffer
            for message in messages:
                context["event_queue"].put((connection_id, message))
        except ValueError as exc:
            context["event_queue"].put((connection_id, {"type": "protocol_error", "message": str(exc)}))
            return
        except (ConnectionError, OSError):
            context["event_queue"].put((connection_id, {"type": "disconnect"}))
            return


def _send_to_connection(context: dict[str, Any], connection_id: int, payload: dict[str, Any]) -> None:
    """Send one message to an active connection."""
    connection = context["connections"].get(connection_id)
    if connection is None:
        return
    sock = connection.get("socket")
    if sock is None:
        return
    send_message(sock, payload)


def _make_player_payload(context: dict[str, Any], player_index: int) -> dict[str, Any]:
    """Build one public-state payload with timeout metadata."""
    payload = make_public_state(context["state"], player_index)
    deadline = context.get("turn_deadline_monotonic")
    remaining = None
    if deadline is not None:
        remaining = max(0.0, deadline - time.monotonic())
    payload["turn_timeout_seconds"] = context.get("turn_timeout_seconds", 0)
    payload["turn_time_remaining"] = remaining
    return payload


def _reset_turn_deadline(context: dict[str, Any]) -> None:
    """Reset the server-side deadline for the current turn, if enabled."""
    timeout_seconds = int(context.get("turn_timeout_seconds", 0))
    round_state = context["state"]["round"]
    if timeout_seconds <= 0 or round_state["round_over"] or context["state"]["game_over"]:
        context["turn_deadline_monotonic"] = None
        return
    context["turn_deadline_monotonic"] = time.monotonic() + timeout_seconds


def _discard_candidate_key(card: str, evaluation: dict[str, Any]) -> tuple[int, int, int, str]:
    """Rank timeout discard candidates by resulting hand quality."""
    return (
        int(evaluation["deadwood_value"]),
        len(evaluation["deadwood"]),
        -card_value(card),
        card,
    )


def _choose_timeout_discard(state: dict[str, Any], player_index: int) -> str:
    """Choose a legal discard when a player's turn times out."""
    hand = list(state["round"]["hands"][player_index])
    pending_knock = bool(state["round"]["pending_knock"])
    best_card: str | None = None
    best_key: tuple[int, int, int, str] | None = None
    best_knock_card: str | None = None
    best_knock_key: tuple[int, int, int, str] | None = None

    for card in list(dict.fromkeys(hand)):
        remaining = list(hand)
        remaining.remove(card)
        evaluation = evaluate_hand(remaining)
        candidate_key = _discard_candidate_key(card, evaluation)
        if best_key is None or candidate_key < best_key:
            best_key = candidate_key
            best_card = card
        if pending_knock and evaluation["deadwood_value"] <= 10:
            if best_knock_key is None or candidate_key < best_knock_key:
                best_knock_key = candidate_key
                best_knock_card = card

    if pending_knock and best_knock_card is not None:
        return best_knock_card
    if best_card is None:
        raise ValueError("No legal discard available for timeout handling.")
    return best_card


def _apply_turn_timeout(context: dict[str, Any]) -> None:
    """Apply an automatic action when the active player runs out of time."""
    state = context["state"]
    round_state = state["round"]
    if round_state["round_over"]:
        _reset_turn_deadline(context)
        return

    player_index = round_state["turn"]
    player_name = state["players"][player_index]["name"]
    stage = round_state["stage"]

    if stage == "offer_first_upcard":
        append_log(state, f"{player_name} ran out of time and automatically declined the opening discard.")
        draw_from_stock(state, player_index)
    elif stage == "draw":
        append_log(state, f"{player_name} ran out of time and automatically drew from stock.")
        draw_from_stock(state, player_index)
    elif stage == "discard":
        discard = _choose_timeout_discard(state, player_index)
        if round_state["pending_knock"]:
            remaining = list(round_state["hands"][player_index])
            remaining.remove(discard)
            if evaluate_hand(remaining)["deadwood_value"] > 10:
                round_state["pending_knock"] = False
                append_log(state, f"{player_name}'s pending knock was canceled after timing out.")
        append_log(state, f"{player_name} ran out of time and automatically discarded {discard}.")
        discard_card(state, player_index, discard)

    _audit_state_snapshot(context, label=f"timeout_{stage}")
    _reset_turn_deadline(context)


def _send_fatal_and_close(context: dict[str, Any], connection_id: int, message: str) -> None:
    """Send a fatal error to a connection and close it."""
    _append_audit_line(context, "fatal", connection_id=connection_id, message=message)
    try:
        _send_to_connection(context, connection_id, {"type": "fatal", "message": message})
    except OSError:
        pass
    _close_connection(context, connection_id, reserve_slot=False, announce_disconnect=False)


def _close_connection(
    context: dict[str, Any],
    connection_id: int,
    reserve_slot: bool,
    announce_disconnect: bool,
) -> None:
    """Close one connection and optionally reserve its player slot for reconnect."""
    connection = context["connections"].pop(connection_id, None)
    if connection is None:
        return

    player_index = connection.get("player_index")
    if player_index is not None and context["player_connections"].get(player_index) == connection_id:
        context["player_connections"].pop(player_index, None)
        if reserve_slot:
            context["reserved_slots"][player_index] = {
                "expires_at": time.monotonic() + RECONNECT_GRACE_SECONDS,
                "name": context["state"]["players"][player_index]["name"],
            }
        elif player_index in context["reserved_slots"]:
            context["reserved_slots"].pop(player_index, None)

    sock = connection.get("socket")
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    if announce_disconnect and player_index is not None:
        context["state"]["log"].append(f"{context['state']['players'][player_index]['name']} disconnected.")
        _append_audit_line(
            context,
            "disconnect",
            connection_id=connection_id,
            player_index=player_index,
            reserve_slot=reserve_slot,
        )


def _cleanup_expired_state(context: dict[str, Any]) -> None:
    """Drop expired auth-failure entries, pending sockets, and reconnect holds."""
    now = time.monotonic()

    for ip_address, failures in list(context["auth_failures"].items()):
        recent = [stamp for stamp in failures if now - stamp <= AUTH_FAILURE_WINDOW_SECONDS]
        if recent:
            context["auth_failures"][ip_address] = recent
        else:
            context["auth_failures"].pop(ip_address, None)

    for player_index, reservation in list(context["reserved_slots"].items()):
        if reservation["expires_at"] <= now:
            context["reserved_slots"].pop(player_index, None)

    for connection_id, connection in list(context["connections"].items()):
        if connection.get("player_index") is None and now - connection["connected_at"] > AUTH_TIMEOUT_SECONDS:
            _send_fatal_and_close(context, connection_id, "Authentication timed out.")

    deadline = context.get("turn_deadline_monotonic")
    if deadline is not None and now >= deadline:
        _apply_turn_timeout(context)
        broadcast_state(context)


def _player_index_for_connection(context: dict[str, Any], connection_id: int) -> int | None:
    """Return the authenticated player index bound to a connection."""
    connection = context["connections"].get(connection_id)
    if connection is None:
        return None
    return connection.get("player_index")


def _record_auth_failure(context: dict[str, Any], ip_address: str) -> None:
    """Record one failed authentication attempt for rate limiting."""
    failures = context["auth_failures"].setdefault(ip_address, [])
    failures.append(time.monotonic())


def _is_rate_limited(context: dict[str, Any], ip_address: str) -> bool:
    """Return whether an IP address exceeded recent auth failures."""
    failures = context["auth_failures"].get(ip_address, [])
    return len(failures) >= MAX_AUTH_FAILURES


def _assign_player_slot(context: dict[str, Any], requested_name: str) -> int | None:
    """Choose a player slot for a newly authenticated connection."""
    now = time.monotonic()
    for player_index, reservation in sorted(context["reserved_slots"].items()):
        if reservation["expires_at"] > now and reservation["name"] == requested_name and player_index not in context["player_connections"]:
            context["reserved_slots"].pop(player_index, None)
            return player_index

    for player_index in (0, 1):
        reservation = context["reserved_slots"].get(player_index)
        if player_index in context["player_connections"]:
            continue
        if reservation is not None and reservation["expires_at"] > now:
            continue
        context["reserved_slots"].pop(player_index, None)
        return player_index

    return None


def broadcast_state(context: dict[str, Any]) -> None:
    """Send the latest redacted state to all connected authenticated players."""
    for player_index, connection_id in list(context["player_connections"].items()):
        try:
            _send_to_connection(context, connection_id, {"type": "state", "state": _make_player_payload(context, player_index)})
        except OSError:
            context["event_queue"].put((connection_id, {"type": "disconnect"}))


def send_error(context: dict[str, Any], connection_id: int, message: str) -> None:
    """Send a recoverable error message to one connection."""
    try:
        _send_to_connection(context, connection_id, {"type": "error", "message": message})
    except OSError:
        pass


def process_one_event(context: dict[str, Any], timeout: float = 0.05) -> bool:
    """Process at most one queued client event."""
    _cleanup_expired_state(context)
    try:
        connection_id, message = context["event_queue"].get(timeout=timeout)
    except queue.Empty:
        return False

    handle_client_message(context, connection_id, message)
    return True


def handle_client_message(context: dict[str, Any], connection_id: int, message: dict[str, Any]) -> None:
    """Apply one client command to the authoritative game state."""
    connection = context["connections"].get(connection_id)
    if connection is None:
        return

    message_type = message.get("type")

    if message_type == "protocol_error":
        _send_fatal_and_close(context, connection_id, "Malformed or oversized message.")
        broadcast_state(context)
        return

    if message_type == "disconnect":
        reserve_slot = connection.get("player_index") is not None
        _close_connection(context, connection_id, reserve_slot=reserve_slot, announce_disconnect=reserve_slot)
        broadcast_state(context)
        return

    if message_type == "hello":
        requested_name = str(message.get("name", "")).strip()
        peer_ip = connection.get("peer_ip", "")
        if _is_rate_limited(context, peer_ip):
            _send_fatal_and_close(context, connection_id, "Too many failed attempts. Please wait and try again.")
            return

        expected_hash = context.get("password_hash")
        provided_hash = _hash_password(str(message.get("password", "")))
        if expected_hash is not None and not hmac.compare_digest(provided_hash, expected_hash):
            _record_auth_failure(context, peer_ip)
            _send_fatal_and_close(context, connection_id, "Invalid password.")
            return

        player_index = _assign_player_slot(context, requested_name)
        if player_index is None:
            _send_fatal_and_close(context, connection_id, "Match unavailable. A player may be reconnecting.")
            return

        connection["player_index"] = player_index
        context["player_connections"][player_index] = connection_id
        rename_player(context["state"], player_index, requested_name)
        _append_audit_line(
            context,
            "player_joined",
            connection_id=connection_id,
            player_index=player_index,
            requested_name=requested_name,
            peer_ip=peer_ip,
        )
        _send_to_connection(context, connection_id, {"type": "assign", "player": player_index})
        _send_to_connection(context, connection_id, {"type": "state", "state": _make_player_payload(context, player_index)})
        broadcast_state(context)
        return

    if message_type != "action":
        send_error(context, connection_id, "Unknown message type.")
        return

    player_index = _player_index_for_connection(context, connection_id)
    if player_index is None:
        send_error(context, connection_id, "Authenticate before sending actions.")
        return

    action = message.get("action")
    player_name = context["state"]["players"][player_index]["name"]
    _append_audit_line(
        context,
        "action_received",
        connection_id=connection_id,
        player_index=player_index,
        player_name=player_name,
        action=action,
        payload={key: value for key, value in message.items() if key not in {"type", "action"}},
        round_index=context["state"]["round_index"],
        stage=context["state"]["round"]["stage"],
    )
    previous_round_index = context["state"]["round_index"]
    previous_round_over = bool(context["state"]["round"]["round_over"])
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
        elif action == "chat":
            clean_message = _sanitize_chat_message(str(message.get("message", "")))
            if not clean_message:
                raise ValueError("Chat message cannot be empty.")
            chat_log = context["state"].setdefault("chat_log", [])
            speaker = context["state"]["players"][player_index]["name"]
            chat_log.append(f"{speaker}: {clean_message}")
            context["state"]["chat_log"] = chat_log[-60:]
        elif action == "continue":
            continue_after_round(context["state"])
        else:
            raise ValueError("Unknown action.")
    except ValueError as exc:
        send_error(context, connection_id, str(exc))
        _append_audit_line(
            context,
            "action_rejected",
            connection_id=connection_id,
            player_index=player_index,
            action=action,
            error=str(exc),
        )
        broadcast_state(context)
        return

    _audit_state_snapshot(context, label=f"after_{action}")
    if context["state"]["round_index"] != previous_round_index:
        _audit_round_setup(context, reason=f"after_{action}")
    elif not previous_round_over and context["state"]["round"]["round_over"]:
        _append_audit_line(
            context,
            "round_completed",
            round_index=context["state"]["round_index"],
            summary=context["state"]["round"]["summary"],
            revealed_hands=context["state"]["round"]["revealed_hands"],
        )

    if action in {"draw_stock", "draw_discard", "discard", "continue"}:
        _reset_turn_deadline(context)
    broadcast_state(context)
