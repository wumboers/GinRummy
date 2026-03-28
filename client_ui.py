"""Tkinter client UI for the LAN Gin Rummy application."""

from __future__ import annotations

import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox
from typing import Any

try:
    import winsound
except ImportError:  # pragma: no cover - winsound is Windows-only.
    winsound = None

from engine import evaluate_hand
from net import create_client_socket, recv_messages, send_message


CARD_WIDTH = 58
CARD_HEIGHT = 84
CARD_GAP = 8


def make_client_context(host: str, port: int, name: str, password: str = "") -> dict[str, Any]:
    """Create a client context dictionary.

    Args:
        host: Server host or IP address.
        port: Server port.
        name: Player display name.
        password: Optional shared game password.

    Returns:
        Mutable client context.
    """
    return {
        "host": host,
        "port": port,
        "name": name,
        "password": password,
        "socket": None,
        "reader_queue": queue.Queue(),
        "running": False,
        "state": None,
        "player_index": None,
        "selected_card": None,
        "manual_hand_order": None,
        "status_message": "Connecting...",
        "server_error": None,
        "last_state_sync_monotonic": None,
        "was_actionable": False,
        "last_round_sound_key": None,
    }


def connect_client(context: dict[str, Any]) -> None:
    """Open the TCP connection and start the reader thread.

    Args:
        context: Client context.
    """
    sock = create_client_socket(context["host"], context["port"])
    context["socket"] = sock
    context["running"] = True
    send_message(sock, {"type": "hello", "name": context["name"], "password": context.get("password", "")})
    reader = threading.Thread(target=reader_loop, args=(context,), daemon=True)
    reader.start()


def close_client(context: dict[str, Any]) -> None:
    """Close the network socket and stop the reader loop.

    Args:
        context: Client context.
    """
    context["running"] = False
    sock = context.get("socket")
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


def reader_loop(context: dict[str, Any]) -> None:
    """Receive server messages and enqueue them for the tkinter thread.

    Args:
        context: Client context.
    """
    buffer = b""
    try:
        while context["running"]:
            messages, buffer = recv_messages(context["socket"], buffer)
            for message in messages:
                context["reader_queue"].put(message)
    except ValueError:
        context["reader_queue"].put({"type": "fatal", "message": "Received malformed or oversized server message."})
    except (ConnectionError, OSError):
        context["reader_queue"].put({"type": "fatal", "message": "Disconnected from server."})
    finally:
        context["running"] = False


def send_action(context: dict[str, Any], action: str, **payload: Any) -> None:
    """Send an action message to the server.

    Args:
        context: Client context.
        action: Action name.
        **payload: Extra JSON fields.
    """
    if context.get("socket") is None:
        return
    send_message(context["socket"], {"type": "action", "action": action, **payload})


def send_chat(context: dict[str, Any], entry: tk.Entry) -> None:
    """Send one chat message from the input field."""
    message = entry.get().strip()
    if not message:
        return
    send_action(context, "chat", message=message)
    entry.delete(0, "end")


def compute_status_text(state: dict[str, Any]) -> str:
    """Build the main status line for the local player.

    Args:
        state: Redacted player state.

    Returns:
        UI status string.
    """
    you = state["you"]
    your_name = state["players"][you]["name"]
    opponent_name = state["players"][1 - you]["name"]

    if state["round_over"]:
        if state["game_over"]:
            winner = state["players"][state["winner"]]["name"]
            return f"Match over. {winner} won. Click 'New Match'."
        return "Round over. Click 'Continue Round' for the next deal."

    turn_name = state["players"][state["turn"]]["name"]
    if state["stage"] == "offer_first_upcard":
        if state["offered_to"] == you:
            return f"Opening discard is offered to you. Click Discard pile to take it or Stock pile to decline."
        return f"Opening discard is being offered to {opponent_name}."
    if state["stage"] == "draw":
        if state["turn"] == you:
            if state.get("must_draw_from_stock"):
                return f"{your_name}: both players passed. You must draw from Stock."
            return f"{your_name}: draw from Stock or Discard."
        return f"Waiting for {turn_name} to draw."
    if state["stage"] == "discard":
        if state["turn"] == you:
            if state["pending_knock"]:
                return "Knock is armed. Discard one card with deadwood 10 or less."
            return f"{your_name}: choose a card to discard, or arm Knock first."
        return f"Waiting for {turn_name} to discard."
    return "Connected."


def compute_timer_text(context: dict[str, Any]) -> str:
    """Build the turn-timer line shown under the main status text."""
    state = context.get("state")
    if not state:
        return ""
    timeout_seconds = int(state.get("turn_timeout_seconds") or 0)
    remaining = state.get("turn_time_remaining")
    if timeout_seconds <= 0 or remaining is None or state.get("round_over"):
        return ""

    synced_at = context.get("last_state_sync_monotonic")
    if synced_at is not None:
        remaining = max(0.0, float(remaining) - (time.monotonic() - synced_at))

    active_name = state["players"][state["turn"]]["name"]
    return f"Turn timer: {remaining:.1f}s left for {active_name}"


def player_can_act(state: dict[str, Any]) -> bool:
    """Return whether the local player currently needs to act."""
    you = state["you"]
    if state["round_over"]:
        return False
    if state["stage"] == "offer_first_upcard":
        return state["offered_to"] == you
    if state["stage"] in {"draw", "discard"}:
        return state["turn"] == you
    return False


def play_turn_chime(root: tk.Tk) -> None:
    """Play a small local notification sound for the active player."""
    if winsound is not None:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        return
    root.bell()


def play_round_result_sound(root: tk.Tk, reason: str) -> None:
    """Play a distinct local sound for gin and standard knock results."""
    if winsound is not None:
        if reason == "gin":
            winsound.PlaySound("SystemExit", winsound.SND_ALIAS | winsound.SND_ASYNC)
            return
        if reason == "knock":
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            return
    root.bell()


def maybe_play_round_result_sound(root: tk.Tk, context: dict[str, Any], state: dict[str, Any]) -> None:
    """Play a round-end sound once per resolved round."""
    summary = state.get("summary")
    if not state.get("round_over") or not isinstance(summary, dict):
        return

    reason = str(summary.get("reason", ""))
    if reason not in {"gin", "knock"}:
        return

    sound_key = (state.get("round_index"), reason, summary.get("round_winner"))
    if context.get("last_round_sound_key") == sound_key:
        return

    context["last_round_sound_key"] = sound_key
    play_round_result_sound(root, reason)


def update_control_visibility(model: dict[str, Any], state: dict[str, Any] | None) -> None:
    """Enable control buttons only when they are valid."""
    knock_button = model["knock_button"]
    cancel_knock_button = model["cancel_knock_button"]
    continue_button = model["continue_button"]
    new_match_button = model["new_match_button"]
    if state is None:
        knock_button.configure(state="disabled")
        cancel_knock_button.configure(state="disabled")
        continue_button.configure(state="disabled")
        new_match_button.configure(state="disabled")
        return

    can_manage_knock = (
        not state["round_over"]
        and state["turn"] == state["you"]
        and state["stage"] == "discard"
    )
    knock_button.configure(state="normal" if can_manage_knock and not state["pending_knock"] else "disabled")
    cancel_knock_button.configure(state="normal" if can_manage_knock and state["pending_knock"] else "disabled")
    continue_button.configure(state="normal" if state["round_over"] and not state["game_over"] else "disabled")
    new_match_button.configure(state="normal" if state["game_over"] else "disabled")


def match_tally_text(state: dict[str, Any], you: int) -> str:
    """Return the running best-of-many match tally for the local view."""
    wins = state.get("match_wins", [0, 0])
    opponent = 1 - you
    return f"Matches: {state['players'][you]['name']} {wins[you]} - {wins[opponent]} {state['players'][opponent]['name']}"


def card_label(card: str) -> str:
    """Convert a card code to a display string.

    Args:
        card: Card code such as `H10` or `SQ`.

    Returns:
        Human-readable label.
    """
    suit = card[0]
    rank = card[1:]
    suit_map = {"C": "♣", "D": "♦", "H": "♥", "S": "♠"}
    return f"{rank}{suit_map[suit]}"


def card_rank_label(card: str) -> str:
    """Return the rank text for a card."""
    return card[1:]


def card_suit_symbol(card: str) -> str:
    """Return the suit symbol for a card."""
    return {"C": "♣", "D": "♦", "H": "♥", "S": "♠"}[card[0]]


def card_suit_hint(card: str) -> str:
    """Return a short suit hint to improve readability."""
    return {"C": "CLUB", "D": "DIAM", "H": "HEART", "S": "SPADE"}[card[0]]


def suit_color(card: str) -> str:
    """Return a foreground color for a card.

    Args:
        card: Card code.

    Returns:
        Tk color string.
    """
    return "#c62828" if card[0] in ("H", "D") else "#111111"


def meld_palette() -> list[dict[str, str]]:
    """Return the repeating color palette used for meld groups."""
    return [
        {"fill": "#dcfce7", "outline": "#16a34a", "label": "#166534"},
        {"fill": "#dbeafe", "outline": "#2563eb", "label": "#1d4ed8"},
        {"fill": "#fce7f3", "outline": "#db2777", "label": "#be185d"},
        {"fill": "#ede9fe", "outline": "#7c3aed", "label": "#6d28d9"},
    ]


def build_hand_visuals(cards: list[str], last_drawn: str | None, melds: list[list[str]] | None = None) -> dict[str, dict[str, Any]]:
    """Compute local rendering hints for the visible hand.

    Args:
        cards: Visible hand cards.
        last_drawn: Most recently drawn card, if any.
        melds: Optional explicit meld groups for this hand.

    Returns:
        Per-card visual flags used by the renderer.
    """
    evaluation = evaluate_hand(cards)
    active_melds = melds if melds is not None else evaluation["melds"]
    palette = meld_palette()
    card_positions = {card: index for index, card in enumerate(cards)}
    ordered_melds = sorted(
        [list(meld) for meld in active_melds],
        key=lambda meld: min(card_positions.get(card, 10**9) for card in meld),
    )
    meld_styles: dict[str, dict[str, str]] = {}
    for index, meld in enumerate(ordered_melds):
        style = palette[index % len(palette)]
        for card in meld:
            meld_styles[card] = style

    return {
        card: {
            "melded": card in meld_styles,
            "drawn": card == last_drawn,
            "deadwood": card in evaluation["deadwood"],
            "meld_style": meld_styles.get(card),
        }
        for card in cards
    }


def draw_card(
    canvas: tk.Canvas,
    x: int,
    y: int,
    card: str,
    selected: bool,
    hidden: bool = False,
    melded: bool = False,
    meld_style: dict[str, str] | None = None,
    drawn: bool = False,
    clickable: bool = False,
) -> None:
    """Draw one card rectangle.

    Args:
        canvas: Tk canvas.
        x: Left coordinate.
        y: Top coordinate.
        card: Card code.
        selected: Whether the card is selected.
        hidden: Whether to draw card back styling.
        melded: Whether the card belongs to a best meld group.
        meld_style: Optional color style for the meld group.
        drawn: Whether this is the most recently drawn card.
        clickable: Whether the card or pile is currently actionable.
    """
    if hidden:
        outline = "#d9c36a" if clickable else "#0d47a1"
        width = 3 if clickable else 2
        canvas.create_rectangle(x, y, x + CARD_WIDTH, y + CARD_HEIGHT, fill="#1e88e5", outline=outline, width=width)
        canvas.create_text(x + CARD_WIDTH / 2, y + CARD_HEIGHT / 2, text="GIN", fill="white", font=("Segoe UI", 12, "bold"))
        return

    fill = "#f7f7f7"
    if melded:
        fill = meld_style["fill"] if meld_style is not None else "#dcfce7"
    elif drawn:
        fill = "#fff8dc"

    if selected:
        outline = "#ffd54f"
        width = 4
    elif melded:
        outline = meld_style["outline"] if meld_style is not None else "#16a34a"
        width = 3
    elif drawn:
        outline = "#f59e0b"
        width = 3
    elif clickable:
        outline = "#d9c36a"
        width = 3
    else:
        outline = "#1b5e20"
        width = 2

    canvas.create_rectangle(x, y, x + CARD_WIDTH, y + CARD_HEIGHT, fill=fill, outline=outline, width=width)
    color = suit_color(card)
    canvas.create_text(x + CARD_WIDTH / 2, y + 13, text=card_rank_label(card), fill=color, font=("Segoe UI", 15, "bold"))
    canvas.create_text(x + CARD_WIDTH / 2, y + 37, text=card_suit_symbol(card), fill=color, font=("Segoe UI Symbol", 24, "bold"))
    canvas.create_text(x + CARD_WIDTH / 2, y + 57, text=card_suit_hint(card), fill=color, font=("Segoe UI", 7, "bold"))
    if melded:
        label_color = meld_style["label"] if meld_style is not None else "#166534"
        canvas.create_text(x + CARD_WIDTH / 2, y + CARD_HEIGHT - 12, text="MELD", fill=label_color, font=("Segoe UI", 8, "bold"))
    elif drawn:
        canvas.create_text(x + CARD_WIDTH / 2, y + CARD_HEIGHT - 12, text="NEW", fill="#b45309", font=("Segoe UI", 8, "bold"))


def draw_compact_card(canvas: tk.Canvas, x: int, y: int, card: str, highlight: bool = False) -> None:
    """Draw a compact card chip for the layoff summary."""
    fill = "#fff7d6" if highlight else "#f7f7f7"
    outline = "#f59e0b" if highlight else "#93a4b7"
    canvas.create_rectangle(x, y, x + 34, y + 22, fill=fill, outline=outline, width=2 if highlight else 1)
    canvas.create_text(x + 17, y + 11, text=card_label(card), fill=suit_color(card), font=("Segoe UI Symbol", 9, "bold"))


def hand_hitboxes(cards: list[str], start_x: int, start_y: int) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Build hitboxes for a horizontal hand layout.

    Args:
        cards: Hand cards.
        start_x: Left start position.
        start_y: Top start position.

    Returns:
        List of `(card, (x1, y1, x2, y2))` entries.
    """
    boxes: list[tuple[str, tuple[int, int, int, int]]] = []
    for index, card in enumerate(cards):
        x1 = start_x + index * (CARD_WIDTH + CARD_GAP)
        y1 = start_y
        boxes.append((card, (x1, y1, x1 + CARD_WIDTH, y1 + CARD_HEIGHT)))
    return boxes


def point_in_box(x: int, y: int, box: tuple[int, int, int, int]) -> bool:
    """Check whether a point is inside a rectangle.

    Args:
        x: X coordinate.
        y: Y coordinate.
        box: Rectangle bounds.

    Returns:
        True when the point is inside the box.
    """
    x1, y1, x2, y2 = box
    return x1 <= x <= x2 and y1 <= y <= y2


def pile_is_actionable(state: dict[str, Any], pile: str) -> bool:
    """Return whether a pile is currently actionable for the local player.

    Args:
        state: Public state.
        pile: Either ``stock`` or ``discard``.

    Returns:
        True when the local player can click the pile.
    """
    you = state["you"]
    if state["round_over"] or state["turn"] != you:
        return False
    if state["stage"] == "draw":
        if state.get("must_draw_from_stock"):
            return pile == "stock"
        return pile in {"stock", "discard"}
    if state["stage"] == "offer_first_upcard":
        if state["offered_to"] != you:
            return False
        return pile in {"stock", "discard"}
    return False


def can_reorder_hand(state: dict[str, Any]) -> bool:
    """Return whether the local player may manually reorder their hand."""
    return (
        not state["round_over"]
        and len(state["your_hand"]) == 10
        and not (state["turn"] == state["you"] and state["stage"] == "discard")
    )


def sync_manual_hand_order(context: dict[str, Any], state: dict[str, Any]) -> None:
    """Keep local manual hand order aligned with the latest server state."""
    server_hand = list(state["your_hand"])
    manual_order = context.get("manual_hand_order")

    if not can_reorder_hand(state):
        context["manual_hand_order"] = None
        context["selected_card"] = None
        return

    if manual_order is None:
        context["manual_hand_order"] = list(server_hand)
        if context.get("selected_card") not in server_hand:
            context["selected_card"] = None
        return

    filtered = [card for card in manual_order if card in server_hand]
    for card in server_hand:
        if card not in filtered:
            filtered.append(card)
    context["manual_hand_order"] = filtered
    if context.get("selected_card") not in filtered:
        context["selected_card"] = None


def get_display_hand(state: dict[str, Any], context: dict[str, Any]) -> list[str]:
    """Return the hand order currently shown to the local player."""
    if can_reorder_hand(state) and context.get("manual_hand_order"):
        manual_order = [card for card in context["manual_hand_order"] if card in state["your_hand"]]
        if len(manual_order) == len(state["your_hand"]):
            return manual_order
    return list(state["your_hand"])


def launch_client_ui(context: dict[str, Any], host_banner: str | None = None, tick_callback=None) -> None:
    """Launch the tkinter GUI and block until the window closes.

    Args:
        context: Client context.
        host_banner: Optional host informational text.
        tick_callback: Optional function polled by the UI loop.
    """
    root = tk.Tk()
    root.title("Gin Rummy LAN")
    root.geometry("1080x760")
    root.configure(bg="#0b5d2a")

    status_var = tk.StringVar(value=context["status_message"])
    timer_var = tk.StringVar(value="")
    info_var = tk.StringVar(value=host_banner or "")

    top_frame = tk.Frame(root, bg="#0b5d2a")
    top_frame.pack(fill="x", padx=8, pady=8)

    tk.Label(top_frame, textvariable=info_var, fg="white", bg="#0b5d2a", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    tk.Label(top_frame, textvariable=status_var, fg="#fff3cd", bg="#0b5d2a", font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))
    tk.Label(top_frame, textvariable=timer_var, fg="#d1fae5", bg="#0b5d2a", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(2, 0))

    center = tk.Frame(root, bg="#0b5d2a")
    center.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    left = tk.Frame(center, bg="#0b5d2a")
    left.pack(side="left", fill="both", expand=True)

    right = tk.Frame(center, bg="#0b5d2a")
    right.pack(side="right", fill="y")

    canvas = tk.Canvas(left, width=840, height=660, bg="#15803d", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    controls = tk.Frame(right, bg="#0b5d2a")
    controls.pack(fill="x", pady=(0, 8))

    tk.Button(controls, text="Sort by Rank", width=18, command=lambda: send_action(context, "sort", mode="rank")).pack(pady=3)
    tk.Button(controls, text="Sort by Suit", width=18, command=lambda: send_action(context, "sort", mode="suit")).pack(pady=3)
    knock_button = tk.Button(controls, text="Knock", width=18, command=lambda: send_action(context, "set_knock", value=True))
    cancel_knock_button = tk.Button(controls, text="Cancel Knock", width=18, command=lambda: send_action(context, "set_knock", value=False))
    knock_button.pack(pady=3)
    cancel_knock_button.pack(pady=3)
    continue_button = tk.Button(controls, text="Continue Round", width=18, command=lambda: send_action(context, "continue"))
    new_match_button = tk.Button(controls, text="New Match", width=18, command=lambda: send_action(context, "continue"))
    continue_button.pack(pady=3)
    new_match_button.pack(pady=3)

    tk.Label(right, text="Event Log", fg="white", bg="#0b5d2a", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    log_box = tk.Text(right, width=34, height=17, state="disabled", bg="#073b1c", fg="white", wrap="word")
    log_box.pack(fill="x", expand=False)

    tk.Label(right, text="Chat", fg="white", bg="#0b5d2a", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(10, 0))
    chat_box = tk.Text(right, width=34, height=13, state="disabled", bg="#052b15", fg="white", wrap="word")
    chat_box.pack(fill="both", expand=True)

    chat_entry_row = tk.Frame(right, bg="#0b5d2a")
    chat_entry_row.pack(fill="x", pady=(6, 0))
    chat_entry = tk.Entry(chat_entry_row)
    chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
    tk.Button(chat_entry_row, text="Send", width=8, command=lambda: send_chat(context, chat_entry)).pack(side="right")
    chat_entry.bind("<Return>", lambda _event: send_chat(context, chat_entry))

    model = {
        "canvas": canvas,
        "status_var": status_var,
        "info_var": info_var,
        "log_box": log_box,
        "chat_box": chat_box,
        "knock_button": knock_button,
        "cancel_knock_button": cancel_knock_button,
        "continue_button": continue_button,
        "new_match_button": new_match_button,
        "your_boxes": [],
        "stock_box": (330, 270, 330 + CARD_WIDTH, 270 + CARD_HEIGHT),
        "discard_box": (430, 270, 430 + CARD_WIDTH, 270 + CARD_HEIGHT),
    }

    def on_close() -> None:
        """Clean up sockets and close the window."""
        close_client(context)
        root.destroy()

    def on_canvas_click(event) -> None:
        """Handle click routing for piles and hand cards."""
        state = context.get("state")
        if not state:
            return
        x = int(event.x)
        y = int(event.y)

        if point_in_box(x, y, model["stock_box"]):
            send_action(context, "draw_stock")
            return
        if point_in_box(x, y, model["discard_box"]):
            send_action(context, "draw_discard")
            return

        display_hand = get_display_hand(state, context)
        hand_visuals = build_hand_visuals(display_hand, state.get("your_last_drawn"))
        for card, box in model["your_boxes"]:
            if point_in_box(x, y, box):
                if can_reorder_hand(state):
                    visuals = hand_visuals.get(card, {})
                    if visuals.get("melded", False):
                        return
                    selected = context.get("selected_card")
                    if selected == card:
                        context["selected_card"] = None
                    elif selected is None:
                        context["selected_card"] = card
                    else:
                        order = list(context.get("manual_hand_order") or display_hand)
                        if selected not in order or card not in order:
                            context["selected_card"] = card if card in order else None
                            render_state(model, context)
                            return
                        first_index = order.index(selected)
                        second_index = order.index(card)
                        order[first_index], order[second_index] = order[second_index], order[first_index]
                        context["manual_hand_order"] = order
                        context["selected_card"] = None
                    render_state(model, context)
                    return
                if state["turn"] == state["you"] and state["stage"] == "discard":
                    context["selected_card"] = card
                    send_action(context, "discard", card=card)
                else:
                    context["selected_card"] = card
                    render_state(model, context)
                return

    def poll_messages() -> None:
        """Poll server messages and refresh the UI."""
        if tick_callback is not None:
            tick_callback()

        try:
            while True:
                message = context["reader_queue"].get_nowait()
                message_type = message.get("type")
                if message_type == "assign":
                    context["player_index"] = message.get("player")
                elif message_type == "state":
                    context["state"] = message["state"]
                    context["last_state_sync_monotonic"] = time.monotonic()
                    is_actionable = player_can_act(context["state"])
                    if is_actionable and not context.get("was_actionable", False):
                        play_turn_chime(root)
                    context["was_actionable"] = is_actionable
                    maybe_play_round_result_sound(root, context, context["state"])
                    sync_manual_hand_order(context, context["state"])
                    context["status_message"] = compute_status_text(context["state"])
                    status_var.set(context["status_message"])
                    timer_var.set(compute_timer_text(context))
                    update_control_visibility(model, context["state"])
                    render_state(model, context)
                elif message_type == "error":
                    context["server_error"] = message.get("message", "Unknown server error.")
                    status_var.set(context["server_error"])
                elif message_type == "fatal":
                    messagebox.showerror("Connection error", message.get("message", "Disconnected."))
                    on_close()
                    return
        except queue.Empty:
            pass
        update_control_visibility(model, context.get("state"))
        timer_var.set(compute_timer_text(context))
        root.after(50, poll_messages)

    root.protocol("WM_DELETE_WINDOW", on_close)
    canvas.bind("<Button-1>", on_canvas_click)
    poll_messages()
    root.mainloop()


def render_state(model: dict[str, Any], context: dict[str, Any]) -> None:
    """Render the entire visible client state.

    Args:
        model: UI widget model.
        context: Client context.
    """
    canvas: tk.Canvas = model["canvas"]
    log_box: tk.Text = model["log_box"]
    chat_box: tk.Text = model["chat_box"]
    state = context.get("state")
    canvas.delete("all")

    if state is None:
        canvas.create_text(420, 330, text="Connecting to server...", fill="white", font=("Segoe UI", 18, "bold"))
        return

    you = state["you"]
    opponent = 1 - you
    your_name = state["players"][you]["name"]
    opponent_name = state["players"][opponent]["name"]

    canvas.create_text(120, 24, text=f"Round {state['round_index']}", fill="white", font=("Segoe UI", 18, "bold"), anchor="w")
    canvas.create_text(120, 52, text=f"Dealer: {state['players'][state['dealer']]['name']}", fill="white", font=("Segoe UI", 12), anchor="w")
    canvas.create_text(120, 78, text=match_tally_text(state, you), fill="#d1fae5", font=("Segoe UI", 11, "bold"), anchor="w")
    canvas.create_text(500, 24, text=f"{your_name}: {state['scores'][you]}", fill="white", font=("Segoe UI", 16, "bold"))
    canvas.create_text(700, 24, text=f"{opponent_name}: {state['scores'][opponent]}", fill="white", font=("Segoe UI", 16, "bold"))

    canvas.create_text(120, 110, text=f"{opponent_name} ({state['opponent_count']} cards)", fill="white", font=("Segoe UI", 14, "bold"), anchor="w")
    for index in range(state["opponent_count"]):
        x = 120 + index * (CARD_WIDTH + CARD_GAP)
        draw_card(canvas, x, 130, "XX", selected=False, hidden=True)

    stock_box = model["stock_box"]
    discard_box = model["discard_box"]
    stock_actionable = pile_is_actionable(state, "stock")
    discard_actionable = pile_is_actionable(state, "discard")
    stock_label_color = "#f6e58d" if stock_actionable else "white"
    discard_label_color = "#f6e58d" if discard_actionable else "white"
    canvas.create_text(stock_box[0] + CARD_WIDTH / 2, stock_box[1] - 14, text=f"Stock ({state['stock_count']})", fill=stock_label_color, font=("Segoe UI", 12, "bold"))
    draw_card(canvas, stock_box[0], stock_box[1], "XX", selected=False, hidden=True, clickable=stock_actionable)

    canvas.create_text(discard_box[0] + CARD_WIDTH / 2, discard_box[1] - 14, text="Discard", fill=discard_label_color, font=("Segoe UI", 12, "bold"))
    if state["discard_top"]:
        draw_card(canvas, discard_box[0], discard_box[1], state["discard_top"], selected=False, hidden=False, clickable=discard_actionable)
    else:
        canvas.create_rectangle(discard_box[0], discard_box[1], discard_box[2], discard_box[3], outline="white", width=2)

    deadwood_color = "#bbf7d0" if state["your_deadwood"] <= 10 else "#fff3cd"
    display_hand = get_display_hand(state, context)
    hand_visuals = build_hand_visuals(display_hand, state.get("your_last_drawn"))
    canvas.create_text(120, 500, text=f"{your_name}", fill="white", font=("Segoe UI", 14, "bold"), anchor="w")
    canvas.create_text(250, 500, text=f"Deadwood: {state['your_deadwood']}", fill=deadwood_color, font=("Segoe UI", 13, "bold"), anchor="w")
    if can_reorder_hand(state):
        canvas.create_text(430, 500, text="Click two deadwood cards to swap positions", fill="#d1fae5", font=("Segoe UI", 10, "bold"), anchor="w")
    your_boxes = hand_hitboxes(display_hand, 120, 525)
    model["your_boxes"] = your_boxes
    for card, box in your_boxes:
        x1, y1, _, _ = box
        visuals = hand_visuals.get(card, {})
        draw_card(
            canvas,
            x1,
            y1,
            card,
            selected=context.get("selected_card") == card,
            hidden=False,
            melded=bool(visuals.get("melded", False)),
            meld_style=visuals.get("meld_style"),
            drawn=bool(visuals.get("drawn", False)),
            clickable=state["turn"] == you and state["stage"] == "discard",
        )

    if state["pending_knock"]:
        canvas.create_text(640, 500, text="Knock Armed", fill="#ffd54f", font=("Segoe UI", 14, "bold"))

    if state["round_over"] and state.get("summary"):
        render_round_summary(canvas, state)

    log_box.configure(state="normal")
    log_box.delete("1.0", "end")
    for line in state["log"]:
        log_box.insert("end", f"• {line}\n")
    log_box.configure(state="disabled")
    log_box.see("end")

    chat_box.configure(state="normal")
    chat_box.delete("1.0", "end")
    for line in state.get("chat_log", []):
        chat_box.insert("end", f"{line}\n")
    chat_box.configure(state="disabled")
    chat_box.see("end")


def render_round_summary(canvas: tk.Canvas, state: dict[str, Any]) -> None:
    """Render the round-end overlay and revealed hands.

    Args:
        canvas: Tk canvas.
        state: Redacted player state with summary present.
    """
    summary = state["summary"]
    revealed_hands = state["revealed_hands"]
    if summary is None or revealed_hands is None:
        return

    canvas.create_rectangle(70, 150, 810, 610, fill="#102a43", outline="#ffd166", width=3)
    winner_name = state["players"][summary["round_winner"]]["name"]
    reason_map = {"gin": "went gin", "knock": "won by knock", "undercut": "won by undercut"}
    canvas.create_text(440, 188, text=f"{winner_name} {reason_map.get(summary['reason'], 'won')} for {summary['point_delta']} points", fill="white", font=("Segoe UI", 16, "bold"))

    you = state["you"]
    opponent = 1 - you
    your_name = state["players"][you]["name"]
    opponent_name = state["players"][opponent]["name"]

    y_cards = revealed_hands[you]
    o_cards = revealed_hands[opponent]
    knocker = summary["knocker"]
    defender = summary["defender"]
    your_melds = summary["knocker_eval"]["melds"] if you == knocker else summary["defender_eval"]["melds"]
    opponent_melds = summary["knocker_eval"]["melds"] if opponent == knocker else summary["defender_eval"]["melds"]
    your_visuals = build_hand_visuals(y_cards, last_drawn=None, melds=your_melds)
    opponent_visuals = build_hand_visuals(o_cards, last_drawn=None, melds=opponent_melds)

    canvas.create_text(110, 220, text=f"{opponent_name} revealed hand", fill="white", font=("Segoe UI", 12, "bold"), anchor="w")
    for index, card in enumerate(o_cards):
        visuals = opponent_visuals.get(card, {})
        draw_card(
            canvas,
            110 + index * (CARD_WIDTH - 10),
            236,
            card,
            selected=False,
            melded=bool(visuals.get("melded", False)),
            meld_style=visuals.get("meld_style"),
        )

    canvas.create_text(110, 344, text=f"{your_name} revealed hand", fill="white", font=("Segoe UI", 12, "bold"), anchor="w")
    for index, card in enumerate(y_cards):
        visuals = your_visuals.get(card, {})
        draw_card(
            canvas,
            110 + index * (CARD_WIDTH - 10),
            360,
            card,
            selected=False,
            melded=bool(visuals.get("melded", False)),
            meld_style=visuals.get("meld_style"),
        )

    canvas.create_text(
        110,
        484,
        text=f"Knocker deadwood: {summary['knocker_deadwood']}   Defender deadwood after layoff: {summary['defender_deadwood']}",
        fill="#fff3cd",
        font=("Segoe UI", 10, "bold"),
        anchor="w",
    )
    canvas.create_text(
        110,
        508,
        text=f"Award: {summary['score_award']}",
        fill="#fff3cd",
        font=("Segoe UI", 10, "bold"),
        anchor="w",
    )

    layoff_sequence = summary.get("defender_after_layoff", {}).get("layoff_sequence", [])
    if layoff_sequence:
        canvas.create_text(110, 540, text="Layoff Sequence", fill="white", font=("Segoe UI", 12, "bold"), anchor="w")
        for index, step in enumerate(layoff_sequence[:4]):
            row_y = 556 + index * 30
            draw_compact_card(canvas, 110, row_y, step["card"], highlight=True)
            canvas.create_text(158, row_y + 11, text="->", fill="#ffd166", font=("Segoe UI", 12, "bold"), anchor="w")
            canvas.create_text(186, row_y + 11, text="onto", fill="#d1fae5", font=("Segoe UI", 9, "bold"), anchor="w")
            for meld_index, meld_card in enumerate(step.get("target_meld", [])[:5]):
                draw_compact_card(canvas, 228 + meld_index * 38, row_y, meld_card)
        if len(layoff_sequence) > 4:
            canvas.create_text(110, 586, text=f"...and {len(layoff_sequence) - 4} more layoff(s)", fill="#d1fae5", font=("Segoe UI", 9, "italic"), anchor="w")
