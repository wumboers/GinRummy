"""Automated local AI client for solo play against the LAN host."""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

from engine import evaluate_hand
from net import create_client_socket, recv_messages, send_message


class AIClient:
    """Stateful Gin Rummy AI client that connects to the host like a normal player.

    Args:
        host: Server host or IP address.
        port: Server TCP port.
        name: Display name shown in the UI.
    """

    def __init__(self, host: str, port: int, name: str = "Computer") -> None:
        self.host = host
        self.port = port
        self.name = name
        self.sock: socket.socket | None = None
        self.running = False
        self.player_index: int | None = None
        self.last_action_key: tuple[Any, ...] | None = None
        self.last_drawn_card: str | None = None
        self.last_seen_hand: list[str] = []

    def connect(self) -> None:
        """Connect to the host and announce the AI player name."""
        self.sock = create_client_socket(self.host, self.port)
        self.running = True
        send_message(self.sock, {"type": "hello", "name": self.name})

    def close(self) -> None:
        """Close the AI client socket."""
        self.running = False
        if self.sock is None:
            return
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None

    def send_action(self, action: str, **payload: Any) -> None:
        """Send one action message to the server.

        Args:
            action: Gin Rummy action name.
            **payload: Additional JSON payload for the action.
        """
        if self.sock is None:
            return
        send_message(self.sock, {"type": "action", "action": action, **payload})

    def run(self) -> None:
        """Run the blocking receive/respond loop until disconnected."""
        self.connect()
        buffer = b""
        try:
            while self.running and self.sock is not None:
                messages, buffer = recv_messages(self.sock, buffer)
                for message in messages:
                    self.handle_message(message)
        except (ConnectionError, OSError):
            pass
        finally:
            self.close()

    def handle_message(self, message: dict[str, Any]) -> None:
        """Process one message from the server.

        Args:
            message: Decoded server JSON message.
        """
        message_type = message.get("type")
        if message_type == "assign":
            self.player_index = int(message.get("player", 1))
            return
        if message_type != "state":
            return

        state = message.get("state")
        if not isinstance(state, dict):
            return

        current_hand = list(state.get("your_hand", []))
        if len(current_hand) > len(self.last_seen_hand):
            added = [card for card in current_hand if card not in self.last_seen_hand or current_hand.count(card) > self.last_seen_hand.count(card)]
            self.last_drawn_card = added[-1] if added else current_hand[-1]
        elif len(current_hand) < len(self.last_seen_hand):
            self.last_drawn_card = None
        self.last_seen_hand = current_hand

        if state.get("round_over") or state.get("game_over"):
            self.last_action_key = None
            return
        if state.get("turn") != state.get("you"):
            self.last_action_key = None
            return

        action_key = self.make_action_key(state)
        if action_key == self.last_action_key:
            return

        action = self.choose_action(state)
        self.last_action_key = action_key
        if action is None:
            return

        time.sleep(0.2)
        self.send_action(action["action"], **action.get("payload", {}))

    def make_action_key(self, state: dict[str, Any]) -> tuple[Any, ...]:
        """Build a dedupe key for the current decision point.

        Args:
            state: Public state snapshot for the AI player.

        Returns:
            Hashable tuple describing the current decision point.
        """
        return (
            state.get("round_index"),
            state.get("turn"),
            state.get("stage"),
            tuple(state.get("your_hand", [])),
            state.get("discard_top"),
            bool(state.get("pending_knock")),
            tuple(state.get("first_upcard_declines", [])),
            state.get("offered_to"),
        )

    def choose_action(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Choose one legal AI action from the current public state.

        Args:
            state: Public state snapshot for the AI player.

        Returns:
            Action dictionary or ``None`` when no action should be sent yet.
        """
        stage = str(state.get("stage", ""))
        hand = list(state.get("your_hand", []))
        discard_top = state.get("discard_top")

        if stage == "offer_first_upcard":
            if state.get("offered_to") != state.get("you"):
                return None
            if discard_top and self.should_take_discard(hand, str(discard_top), opening_offer=True):
                return {"action": "draw_discard", "payload": {}}
            return {"action": "draw_stock", "payload": {}}

        if stage == "draw":
            if discard_top and self.should_take_discard(hand, str(discard_top), opening_offer=False):
                return {"action": "draw_discard", "payload": {}}
            return {"action": "draw_stock", "payload": {}}

        if stage == "discard":
            best = self.choose_discard(hand)
            if best is None:
                return None
            if best["deadwood"] <= 10 and not state.get("pending_knock"):
                return {"action": "set_knock", "payload": {"value": True}}
            return {"action": "discard", "payload": {"card": best["card"]}}

        return None

    def should_take_discard(self, hand: list[str], discard_card: str, opening_offer: bool) -> bool:
        """Decide whether the top discard is worth taking.

        Args:
            hand: Current hand.
            discard_card: Top discard card available.
            opening_offer: Whether this is the opening upcard offer.

        Returns:
            ``True`` when the discard improves the hand enough to justify taking it.
        """
        current_eval = evaluate_hand(hand)
        best_after_draw = self.choose_discard(hand + [discard_card], prefer_not=discard_card)
        if best_after_draw is None:
            return False

        deadwood_gain = current_eval["deadwood_value"] - int(best_after_draw["deadwood"])
        meld_gain = int(best_after_draw["melded_cards"]) - sum(len(meld) for meld in current_eval["melds"])

        if deadwood_gain >= 3:
            return True
        if deadwood_gain >= 1 and meld_gain > 0:
            return True
        if opening_offer and meld_gain > 0:
            return True
        return False

    def choose_discard(self, hand: list[str], prefer_not: str | None = None) -> dict[str, Any] | None:
        """Choose the best discard from a full hand.

        Args:
            hand: Current full hand, normally 11 cards before discarding.
            prefer_not: Optional card to avoid discarding when ties occur.

        Returns:
            Dictionary with the chosen card and resulting hand metrics.
        """
        if not hand:
            return None

        best: dict[str, Any] | None = None
        for card in list(dict.fromkeys(hand)):
            remaining = list(hand)
            remaining.remove(card)
            evaluation = evaluate_hand(remaining)
            candidate = {
                "card": card,
                "deadwood": int(evaluation["deadwood_value"]),
                "melded_cards": int(sum(len(meld) for meld in evaluation["melds"])),
                "discard_value": self.card_deadwood_value(card),
                "is_preferred": card != prefer_not,
            }
            if self.is_better_discard(candidate, best):
                best = candidate
        return best

    @staticmethod
    def card_deadwood_value(card: str) -> int:
        """Return the standard Gin Rummy deadwood value for one card.

        Args:
            card: Card code.

        Returns:
            Deadwood value.
        """
        rank = card[1:]
        if rank == "A":
            return 1
        if rank in {"J", "Q", "K"}:
            return 10
        return int(rank)

    @staticmethod
    def is_better_discard(candidate: dict[str, Any], incumbent: dict[str, Any] | None) -> bool:
        """Compare two discard candidates.

        Args:
            candidate: New discard candidate.
            incumbent: Current best candidate.

        Returns:
            ``True`` when the candidate should replace the incumbent.
        """
        if incumbent is None:
            return True
        candidate_key = (
            -candidate["deadwood"],
            candidate["melded_cards"],
            candidate["discard_value"],
            1 if candidate["is_preferred"] else 0,
            candidate["card"],
        )
        incumbent_key = (
            -incumbent["deadwood"],
            incumbent["melded_cards"],
            incumbent["discard_value"],
            1 if incumbent["is_preferred"] else 0,
            incumbent["card"],
        )
        return candidate_key > incumbent_key


def run_ai_client(host: str, port: int, name: str = "Computer") -> None:
    """Connect a local AI player to the running host server.

    Args:
        host: Server host or IP address.
        port: TCP port.
        name: Display name shown for the AI.
    """
    ai = AIClient(host=host, port=port, name=name)
    ai.run()


def start_ai_thread(host: str, port: int, name: str = "Computer") -> threading.Thread:
    """Launch the AI client in a daemon thread.

    Args:
        host: Server host or IP address.
        port: TCP port.
        name: Display name shown for the AI.

    Returns:
        Started daemon thread object.
    """
    thread = threading.Thread(target=run_ai_client, args=(host, port, name), daemon=True)
    thread.start()
    return thread
