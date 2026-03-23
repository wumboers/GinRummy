"""Tkinter client UI for the LAN Gin Rummy application."""

from __future__ import annotations

import queue
import socket
import threading
import tkinter as tk
from collections import Counter
from tkinter import messagebox
from typing import Any

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
        "status_message": "Connecting...",
        "server_error": None,
        "last_new_card": None,
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
            return f"{your_name}: draw from Stock or Discard."
        return f"Waiting for {turn_name} to draw."
    if state["stage"] == "discard":
        if state["turn"] == you:
            if state["pending_knock"]:
                return "Knock is armed. Discard one card with deadwood 10 or less."
            return f"{your_name}: choose a card to discard, or arm Knock first."
        return f"Waiting for {turn_name} to discard."
    return "Connected."


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


def suit_color(card: str) -> str:
    """Return a foreground color for a card.

    Args:
        card: Card code.

    Returns:
        Tk color string.
    """
    return "#c62828" if card[0] in ("H", "D") else "#111111"


def draw_card(
    canvas: tk.Canvas,
    x: int,
    y: int,
    card: str,
    selected: bool,
    hidden: bool = False,
    fill: str | None = None,
    outline: str | None = None,
    border_width: int | None = None,
) -> None:
    """Draw one card rectangle.

    Args:
        canvas: Tk canvas.
        x: Left coordinate.
        y: Top coordinate.
        card: Card code.
        selected: Whether the card is selected.
        hidden: Whether to draw card back styling.
        fill: Optional explicit fill color override.
        outline: Optional explicit outline color override.
        border_width: Optional explicit border width override.
    """
    outline_color = "#ffd54f" if selected else "#1b5e20"
    width = 3 if selected else 2
    fill_color = "#f7f7f7" if not hidden else "#1e88e5"
    if fill is not None:
        fill_color = fill
    if outline is not None:
        outline_color = outline
    if border_width is not None:
        width = border_width
    canvas.create_rectangle(x, y, x + CARD_WIDTH, y + CARD_HEIGHT, fill=fill_color, outline=outline_color, width=width)
    if hidden:
        canvas.create_text(x + CARD_WIDTH / 2, y + CARD_HEIGHT / 2, text="GIN", fill="white", font=("Segoe UI", 12, "bold"))
        return
    canvas.create_text(x + CARD_WIDTH / 2, y + 16, text=card_label(card), fill=suit_color(card), font=("Segoe UI", 12, "bold"))


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


def get_hand_analysis(cards: list[str]) -> dict[str, Any]:
    """Return meld and deadwood analysis for a visible hand.

    Args:
        cards: Visible local hand.

    Returns:
        Dictionary with evaluation, meld card set, and deadwood value.
    """
    evaluation = evaluate_hand(cards)
    meld_cards = {card for meld in evaluation["melds"] for card in meld}
    return {
        "evaluation": evaluation,
        "meld_cards": meld_cards,
        "deadwood_value": evaluation["deadwood_value"],
    }


def detect_new_card(previous_cards: list[str] | None, current_cards: list[str]) -> str | None:
    """Infer the newly drawn card by multiset comparison.

    Args:
        previous_cards: Prior visible hand.
        current_cards: Current visible hand.

    Returns:
        Card code for the newly added card when detectable, else None.
    """
    if not previous_cards:
        return None
    previous_counter = Counter(previous_cards)
    current_counter = Counter(current_cards)
    for card, count in current_counter.items():
        if count > previous_counter.get(card, 0):
            return card
    return None


def order_hand_for_display(cards: list[str], context: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Return the locally rendered hand order and its analysis.

    Args:
        cards: Current visible hand.
        context: Client context carrying transient UI state.

    Returns:
        Tuple of ordered cards and hand analysis.
    """
    analysis = get_hand_analysis(cards)
    ordered = list(cards)
    new_card = context.get("last_new_card")
    if new_card and len(ordered) >= 11 and new_card in ordered:
        ordered.remove(new_card)
        ordered.append(new_card)
    return ordered, analysis


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
        return pile in {"stock", "discard"}
    if state["stage"] == "offer_first_upcard":
        if state["offered_to"] != you:
            return False
        return pile in {"stock", "discard"}
    return False


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
    info_var = tk.StringVar(value=host_banner or "")

    top_frame = tk.Frame(root, bg="#0b5d2a")
    top_frame.pack(fill="x", padx=8, pady=8)

    tk.Label(top_frame, textvariable=info_var, fg="white", bg="#0b5d2a", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    tk.Label(top_frame, textvariable=status_var, fg="#fff3cd", bg="#0b5d2a", font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))

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
    tk.Button(controls, text="Knock", width=18, command=lambda: send_action(context, "set_knock", value=True)).pack(pady=3)
    tk.Button(controls, text="Cancel Knock", width=18, command=lambda: send_action(context, "set_knock", value=False)).pack(pady=3)
    tk.Button(controls, text="Continue Round", width=18, command=lambda: send_action(context, "continue")).pack(pady=3)
    tk.Button(controls, text="New Match", width=18, command=lambda: send_action(context, "continue")).pack(pady=3)

    tk.Label(right, text="Event Log", fg="white", bg="#0b5d2a", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    log_box = tk.Text(right, width=34, height=35, state="disabled", bg="#073b1c", fg="white", wrap="word")
    log_box.pack(fill="both", expand=True)

    model = {
        "canvas": canvas,
        "status_var": status_var,
        "info_var": info_var,
        "log_box": log_box,
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

        for card, box in model["your_boxes"]:
            if point_in_box(x, y, box):
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
                    previous_state = context.get("state")
                    new_state = message["state"]
                    previous_hand = previous_state.get("your_hand") if previous_state else None
                    current_hand = new_state.get("your_hand", [])
                    stage = new_state.get("stage")
                    if len(current_hand) > len(previous_hand or []):
                        context["last_new_card"] = detect_new_card(previous_hand, current_hand)
                    elif stage != "discard" or len(current_hand) <= 10:
                        context["last_new_card"] = None
                    context["state"] = new_state
                    context["status_message"] = compute_status_text(context["state"])
                    status_var.set(context["status_message"])
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
    if stock_actionable:
        canvas.create_rectangle(stock_box[0] - 6, stock_box[1] - 6, stock_box[2] + 6, stock_box[3] + 6, outline="#fde047", width=3)
    canvas.create_text(stock_box[0] + CARD_WIDTH / 2, stock_box[1] - 14, text=f"Stock ({state['stock_count']})", fill="white", font=("Segoe UI", 12, "bold"))
    draw_card(canvas, stock_box[0], stock_box[1], "XX", selected=False, hidden=True)

    if discard_actionable:
        canvas.create_rectangle(discard_box[0] - 6, discard_box[1] - 6, discard_box[2] + 6, discard_box[3] + 6, outline="#fde047", width=3)
    canvas.create_text(discard_box[0] + CARD_WIDTH / 2, discard_box[1] - 14, text="Discard", fill="white", font=("Segoe UI", 12, "bold"))
    if state["discard_top"]:
        draw_card(canvas, discard_box[0], discard_box[1], state["discard_top"], selected=False, hidden=False)
    else:
        canvas.create_rectangle(discard_box[0], discard_box[1], discard_box[2], discard_box[3], outline="white", width=2)

    displayed_hand, analysis = order_hand_for_display(state["your_hand"], context)
    deadwood_value = analysis["deadwood_value"]
    deadwood_color = "#bbf7d0" if deadwood_value <= 10 else "#fff3cd"
    canvas.create_text(120, 476, text="Legend: green=MELD   gold=NEW", fill="#d1fae5", font=("Segoe UI", 10, "bold"), anchor="w")
    canvas.create_text(120, 500, text=f"{your_name}", fill="white", font=("Segoe UI", 14, "bold"), anchor="w")
    canvas.create_text(250, 500, text=f"Deadwood: {deadwood_value}", fill=deadwood_color, font=("Segoe UI", 13, "bold"), anchor="w")
    your_boxes = hand_hitboxes(displayed_hand, 120, 525)
    model["your_boxes"] = your_boxes
    for card, box in your_boxes:
        x1, y1, _, _ = box
        is_selected = context.get("selected_card") == card
        is_meld = card in analysis["meld_cards"]
        is_new = context.get("last_new_card") == card and len(displayed_hand) >= 11
        fill = "#dcfce7" if is_meld else None
        outline = None
        width = None
        if is_new:
            fill = "#fef3c7"
            outline = "#f59e0b"
            width = 3
        elif is_meld:
            outline = "#16a34a"
            width = 3
        draw_card(canvas, x1, y1, card, selected=is_selected, hidden=False, fill=fill, outline=outline, border_width=width)
        if is_meld:
            canvas.create_text(x1 + CARD_WIDTH / 2, y1 + CARD_HEIGHT - 10, text="MELD", fill="#166534", font=("Segoe UI", 8, "bold"))
        elif is_new:
            canvas.create_text(x1 + CARD_WIDTH / 2, y1 + CARD_HEIGHT - 10, text="NEW", fill="#92400e", font=("Segoe UI", 8, "bold"))

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

    canvas.create_rectangle(70, 150, 810, 540, fill="#102a43", outline="#ffd166", width=3)
    winner_name = state["players"][summary["round_winner"]]["name"]
    reason_map = {"gin": "went gin", "knock": "won by knock", "undercut": "won by undercut"}
    canvas.create_text(440, 188, text=f"{winner_name} {reason_map.get(summary['reason'], 'won')} for {summary['point_delta']} points", fill="white", font=("Segoe UI", 16, "bold"))

    you = state["you"]
    opponent = 1 - you
    your_name = state["players"][you]["name"]
    opponent_name = state["players"][opponent]["name"]

    y_cards = revealed_hands[you]
    o_cards = revealed_hands[opponent]

    canvas.create_text(110, 220, text=f"{opponent_name} revealed hand", fill="white", font=("Segoe UI", 12, "bold"), anchor="w")
    for index, card in enumerate(o_cards):
        draw_card(canvas, 110 + index * (CARD_WIDTH - 10), 236, card, selected=False)

    canvas.create_text(110, 344, text=f"{your_name} revealed hand", fill="white", font=("Segoe UI", 12, "bold"), anchor="w")
    for index, card in enumerate(y_cards):
        draw_card(canvas, 110 + index * (CARD_WIDTH - 10), 360, card, selected=False)

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
