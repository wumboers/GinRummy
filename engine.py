"""Pure Gin Rummy rules engine for a two-player LAN game."""

from __future__ import annotations

import itertools
import random
from typing import Any


SUITS = ("C", "D", "H", "S")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
RANK_ORDER = {rank: index + 1 for index, rank in enumerate(RANKS)}
CARD_VALUES = {
    "A": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 10,
    "Q": 10,
    "K": 10,
}


def make_deck(rng: random.Random) -> list[str]:
    """Create and shuffle a standard 52-card deck.

    Args:
        rng: Random number generator.

    Returns:
        Shuffled deck represented as card strings like `H7` or `DQ`.
    """
    deck = [f"{suit}{rank}" for suit in SUITS for rank in RANKS]
    rng.shuffle(deck)
    return deck


def card_suit(card: str) -> str:
    """Return the suit portion of a card string.

    Args:
        card: Card string.

    Returns:
        Single-character suit code.
    """
    return card[0]


def card_rank(card: str) -> str:
    """Return the rank portion of a card string.

    Args:
        card: Card string.

    Returns:
        Rank text.
    """
    return card[1:]


def card_value(card: str) -> int:
    """Return the deadwood value for a card.

    Args:
        card: Card string.

    Returns:
        Deadwood point value.
    """
    return CARD_VALUES[card_rank(card)]


def _basic_sort_cards(cards: list[str], mode: str) -> list[str]:
    """Return a plain sorted copy of a card list.

    Args:
        cards: Cards to sort.
        mode: `rank` or `suit`.

    Returns:
        New sorted card list without meld grouping.
    """
    if mode == "suit":
        return sorted(cards, key=lambda c: (card_suit(c), RANK_ORDER[card_rank(c)]))
    return sorted(cards, key=lambda c: (RANK_ORDER[card_rank(c)], card_suit(c)))


def _order_meld_for_display(meld: list[str], mode: str) -> list[str]:
    """Return a single meld ordered for hand display.

    Args:
        meld: Meld cards.
        mode: `rank` or `suit`.

    Returns:
        Ordered meld list.
    """
    if len({card_rank(card) for card in meld}) == 1:
        return _basic_sort_cards(meld, mode)
    return sorted(meld, key=lambda c: RANK_ORDER[card_rank(c)])


def sort_cards(cards: list[str], mode: str) -> list[str]:
    """Return a sorted copy of a hand with melds grouped first.

    Args:
        cards: Cards to sort.
        mode: `rank` or `suit`.

    Returns:
        New sorted card list. Best meld groups stay together and only the
        remaining deadwood cards are sorted normally.
    """
    if len(cards) < 3:
        return _basic_sort_cards(cards, mode)

    evaluation = evaluate_hand(cards)
    melds = [list(meld) for meld in evaluation["melds"]]
    deadwood = _basic_sort_cards(list(evaluation["deadwood"]), mode)

    def meld_key(meld: list[str]) -> tuple:
        ordered = _order_meld_for_display(meld, mode)
        is_set = len({card_rank(card) for card in meld}) == 1
        if mode == "suit":
            if is_set:
                return (1, card_suit(ordered[0]), RANK_ORDER[card_rank(ordered[0])])
            return (0, card_suit(ordered[0]), RANK_ORDER[card_rank(ordered[0])])
        return (RANK_ORDER[card_rank(ordered[0])], 0 if is_set else 1, card_suit(ordered[0]))

    melds = sorted(melds, key=meld_key)

    grouped: list[str] = []
    for meld in melds:
        grouped.extend(_order_meld_for_display(meld, mode))
    grouped.extend(deadwood)
    return grouped


def make_initial_state(seed: int | None = None) -> dict[str, Any]:
    """Create a new match state.

    Args:
        seed: Optional deterministic RNG seed.

    Returns:
        Mutable match state dictionary.
    """
    rng = random.Random(seed)
    state: dict[str, Any] = {
        "scores": [0, 0],
        "dealer": 0,
        "round_index": 0,
        "target_score": 100,
        "game_over": False,
        "winner": None,
        "log": ["Match created."],
        "players": [
            {"name": "Player 1", "sort_mode": "rank"},
            {"name": "Player 2", "sort_mode": "rank"},
        ],
        "rng": rng,
    }
    start_new_round(state, dealer=state["dealer"])
    return state


def append_log(state: dict[str, Any], message: str) -> None:
    """Append a message to the rolling event log.

    Args:
        state: Match state.
        message: Message text.
    """
    state["log"].append(message)
    state["log"] = state["log"][-18:]


def start_new_round(state: dict[str, Any], dealer: int | None = None) -> None:
    """Deal a new round and reset round-level state.

    Args:
        state: Match state.
        dealer: Optional explicit dealer index.
    """
    if dealer is not None:
        state["dealer"] = dealer

    deck = make_deck(state["rng"])
    hands = [[], []]
    for _ in range(10):
        hands[0].append(deck.pop())
        hands[1].append(deck.pop())

    for player_index in (0, 1):
        mode = state["players"][player_index].get("sort_mode", "rank")
        hands[player_index] = sort_cards(hands[player_index], mode)

    upcard = deck.pop()
    non_dealer = 1 - state["dealer"]

    state["round_index"] += 1
    state["round"] = {
        "hands": hands,
        "stock": deck,
        "discard": [upcard],
        "turn": non_dealer,
        "stage": "offer_first_upcard",
        "offered_to": non_dealer,
        "first_upcard": upcard,
        "first_upcard_declines": [],
        "pending_knock": False,
        "winner": None,
        "round_over": False,
        "summary": None,
        "revealed_hands": None,
    }
    append_log(
        state,
        f"Round {state['round_index']} started. {state['players'][state['dealer']]['name']} deals. Upcard: {upcard}.",
    )


def get_current_turn(state: dict[str, Any]) -> int:
    """Return the current player index.

    Args:
        state: Match state.

    Returns:
        Player index whose turn it is.
    """
    return state["round"]["turn"]


def get_legal_melds(cards: list[str]) -> list[tuple[str, ...]]:
    """Enumerate all legal melds available in a hand.

    Args:
        cards: Hand cards.

    Returns:
        List of meld tuples.
    """
    melds: set[tuple[str, ...]] = set()

    rank_groups: dict[str, list[str]] = {}
    for card in cards:
        rank_groups.setdefault(card_rank(card), []).append(card)

    for same_rank_cards in rank_groups.values():
        if len(same_rank_cards) >= 3:
            for size in (3, 4):
                if len(same_rank_cards) >= size:
                    for combo in itertools.combinations(sorted(same_rank_cards), size):
                        melds.add(combo)

    suit_groups: dict[str, list[str]] = {}
    for card in cards:
        suit_groups.setdefault(card_suit(card), []).append(card)

    for suited_cards in suit_groups.values():
        suited_cards = sorted(suited_cards, key=lambda c: RANK_ORDER[card_rank(c)])
        ranks = [RANK_ORDER[card_rank(card)] for card in suited_cards]
        for start in range(len(suited_cards)):
            end = start
            while end + 1 < len(suited_cards) and ranks[end + 1] == ranks[end] + 1:
                end += 1
            if end - start + 1 >= 3:
                run_cards = suited_cards[start : end + 1]
                for size in range(3, len(run_cards) + 1):
                    for offset in range(0, len(run_cards) - size + 1):
                        melds.add(tuple(run_cards[offset : offset + size]))
    return sorted(melds)


def evaluate_hand(cards: list[str]) -> dict[str, Any]:
    """Find the best meld grouping and deadwood for a hand.

    Args:
        cards: Hand cards.

    Returns:
        Dictionary containing melds, deadwood cards, and deadwood value.
    """
    legal_melds = get_legal_melds(cards)
    best: dict[str, Any] = {
        "melds": [],
        "deadwood": _basic_sort_cards(list(cards), "rank"),
        "deadwood_value": sum(card_value(card) for card in cards),
    }

    def recurse(start_index: int, chosen: list[tuple[str, ...]], used_cards: set[str]) -> None:
        nonlocal best
        unused = [card for card in cards if card not in used_cards]
        deadwood_value = sum(card_value(card) for card in unused)
        candidate = {
            "melds": [list(meld) for meld in chosen],
            "deadwood": _basic_sort_cards(unused, "rank"),
            "deadwood_value": deadwood_value,
        }
        if candidate["deadwood_value"] < best["deadwood_value"]:
            best = candidate
        elif candidate["deadwood_value"] == best["deadwood_value"]:
            if len(candidate["deadwood"]) < len(best["deadwood"]):
                best = candidate
            elif len(candidate["deadwood"]) == len(best["deadwood"]):
                candidate_meld_lengths = sorted((len(meld) for meld in candidate["melds"]), reverse=True)
                best_meld_lengths = sorted((len(meld) for meld in best["melds"]), reverse=True)
                if candidate_meld_lengths > best_meld_lengths:
                    best = candidate

        for meld_index in range(start_index, len(legal_melds)):
            meld = legal_melds[meld_index]
            meld_set = set(meld)
            if used_cards & meld_set:
                continue
            recurse(meld_index + 1, chosen + [meld], used_cards | meld_set)

    recurse(0, [], set())
    return best


def can_layoff_card(card: str, meld: list[str]) -> bool:
    """Check whether a deadwood card can be laid off onto a meld.

    Args:
        card: Deadwood card.
        meld: Existing meld.

    Returns:
        True when the card can extend the meld.
    """
    if not meld:
        return False

    ranks = {card_rank(m) for m in meld}
    suits = {card_suit(m) for m in meld}
    if len(ranks) == 1:
        if len(meld) >= 4:
            return False
        return card_rank(card) in ranks and card_suit(card) not in suits

    meld_suit = card_suit(meld[0])
    if card_suit(card) != meld_suit:
        return False

    ordered = sorted(meld, key=lambda c: RANK_ORDER[card_rank(c)])
    low = RANK_ORDER[card_rank(ordered[0])]
    high = RANK_ORDER[card_rank(ordered[-1])]
    rank_num = RANK_ORDER[card_rank(card)]
    return rank_num == low - 1 or rank_num == high + 1


def apply_layoffs(knocker_eval: dict[str, Any], defender_eval: dict[str, Any]) -> dict[str, Any]:
    """Lay off defender deadwood onto knocker melds when allowed.

    Args:
        knocker_eval: Knocker hand evaluation.
        defender_eval: Defender hand evaluation.

    Returns:
        Updated evaluation for the defending hand.
    """
    melds = [list(meld) for meld in knocker_eval["melds"]]
    deadwood = list(defender_eval["deadwood"])
    changed = True
    while changed:
        changed = False
        for card in list(deadwood):
            for meld in melds:
                if can_layoff_card(card, meld):
                    meld.append(card)
                    if len({card_rank(x) for x in meld}) == 1:
                        meld[:] = sort_cards(meld, "suit")
                    else:
                        meld[:] = sorted(meld, key=lambda c: RANK_ORDER[card_rank(c)])
                    deadwood.remove(card)
                    changed = True
                    break
            if changed:
                break
    return {
        "melds": defender_eval["melds"],
        "deadwood": _basic_sort_cards(deadwood, "rank"),
        "deadwood_value": sum(card_value(card) for card in deadwood),
    }


def build_round_summary(state: dict[str, Any], knocker: int) -> dict[str, Any]:
    """Compute round-end scoring, layoff, and winner information.

    Args:
        state: Match state.
        knocker: Player index who knocked.

    Returns:
        Summary dictionary describing the round result.
    """
    hands = state["round"]["hands"]
    defender = 1 - knocker
    knocker_eval = evaluate_hand(hands[knocker])
    defender_eval = evaluate_hand(hands[defender])

    went_gin = knocker_eval["deadwood_value"] == 0
    defender_after_layoff = defender_eval
    if not went_gin:
        defender_after_layoff = apply_layoffs(knocker_eval, defender_eval)

    knocker_deadwood = knocker_eval["deadwood_value"]
    defender_deadwood = defender_after_layoff["deadwood_value"]
    diff = defender_deadwood - knocker_deadwood

    score_award = [0, 0]
    round_winner = knocker
    reason = "knock"

    if went_gin:
        round_points = diff + 20
        score_award[knocker] = round_points
        round_winner = knocker
        reason = "gin"
    elif diff > 0:
        round_points = diff
        score_award[knocker] = round_points
        round_winner = knocker
        reason = "knock"
    else:
        round_points = abs(diff) + 10
        score_award[defender] = round_points
        round_winner = defender
        reason = "undercut"

    summary = {
        "knocker": knocker,
        "defender": defender,
        "knocker_eval": knocker_eval,
        "defender_eval": defender_eval,
        "defender_after_layoff": defender_after_layoff,
        "score_award": score_award,
        "round_winner": round_winner,
        "reason": reason,
        "went_gin": went_gin,
        "knocker_deadwood": knocker_deadwood,
        "defender_deadwood": defender_deadwood,
        "point_delta": round_points,
    }
    return summary


def finish_round(state: dict[str, Any], knocker: int) -> None:
    """Close the current round and update match scores.

    Args:
        state: Match state.
        knocker: Player index who knocked.
    """
    summary = build_round_summary(state, knocker)
    state["round"]["summary"] = summary
    state["round"]["round_over"] = True
    state["round"]["winner"] = summary["round_winner"]
    state["round"]["revealed_hands"] = [
        sort_cards(list(state["round"]["hands"][0]), "rank"),
        sort_cards(list(state["round"]["hands"][1]), "rank"),
    ]

    for player_index in (0, 1):
        state["scores"][player_index] += summary["score_award"][player_index]

    knocker_name = state["players"][knocker]["name"]
    round_winner_name = state["players"][summary["round_winner"]]["name"]
    if summary["reason"] == "gin":
        append_log(state, f"{knocker_name} went gin for {summary['point_delta']} points.")
    elif summary["reason"] == "undercut":
        append_log(state, f"{round_winner_name} undercut for {summary['point_delta']} points.")
    else:
        append_log(state, f"{knocker_name} knocked for {summary['point_delta']} points.")

    if max(state["scores"]) >= state["target_score"]:
        state["game_over"] = True
        if state["scores"][0] > state["scores"][1]:
            state["winner"] = 0
        elif state["scores"][1] > state["scores"][0]:
            state["winner"] = 1
        else:
            state["winner"] = summary["round_winner"]
        append_log(state, f"Match over. {state['players'][state['winner']]['name']} wins.")


def continue_after_round(state: dict[str, Any]) -> None:
    """Advance from a finished round to the next round or reset the match.

    Args:
        state: Match state.
    """
    if not state["round"]["round_over"]:
        raise ValueError("Round is not over.")

    if state["game_over"]:
        state["scores"] = [0, 0]
        state["round_index"] = 0
        state["winner"] = None
        state["game_over"] = False
        state["dealer"] = 0
        append_log(state, "New match started.")
        start_new_round(state, dealer=state["dealer"])
        return

    next_dealer = 1 - state["dealer"]
    start_new_round(state, dealer=next_dealer)


def validate_turn(state: dict[str, Any], player_index: int) -> None:
    """Raise when an action is attempted out of turn.

    Args:
        state: Match state.
        player_index: Acting player index.
    """
    if state["round"]["turn"] != player_index:
        raise ValueError("It is not your turn.")


def draw_from_stock(state: dict[str, Any], player_index: int) -> None:
    """Draw the top card from the stock pile.

    Args:
        state: Match state.
        player_index: Acting player index.
    """
    validate_turn(state, player_index)
    round_state = state["round"]
    if round_state["round_over"]:
        raise ValueError("The round is over.")
    if round_state["stage"] not in ("draw", "offer_first_upcard"):
        raise ValueError("You cannot draw from stock right now.")

    if round_state["stage"] == "offer_first_upcard":
        offered_to = round_state["offered_to"]
        if player_index != offered_to:
            raise ValueError("You are not being offered the opening discard.")
        round_state["first_upcard_declines"].append(player_index)
        append_log(state, f"{state['players'][player_index]['name']} declined the opening discard.")
        if len(round_state["first_upcard_declines"]) == 1:
            round_state["offered_to"] = 1 - offered_to
            round_state["turn"] = 1 - offered_to
            append_log(state, f"Opening discard is now offered to {state['players'][1 - offered_to]['name']}.")
            return
        round_state["stage"] = "draw"
        round_state["turn"] = state["dealer"]
        append_log(state, f"Both players declined the opening discard. {state['players'][state['dealer']]['name']} must draw from stock.")

    if not round_state["stock"]:
        raise ValueError("The stock pile is empty.")

    card = round_state["stock"].pop()
    round_state["hands"][player_index].append(card)
    round_state["hands"][player_index] = sort_cards(
        round_state["hands"][player_index],
        state["players"][player_index].get("sort_mode", "rank"),
    )
    round_state["stage"] = "discard"
    round_state["pending_knock"] = False
    append_log(state, f"{state['players'][player_index]['name']} drew from stock.")


def draw_from_discard(state: dict[str, Any], player_index: int) -> None:
    """Draw the top card from the discard pile.

    Args:
        state: Match state.
        player_index: Acting player index.
    """
    validate_turn(state, player_index)
    round_state = state["round"]
    if round_state["round_over"]:
        raise ValueError("The round is over.")
    if round_state["stage"] not in ("draw", "offer_first_upcard"):
        raise ValueError("You cannot draw from discard right now.")

    if not round_state["discard"]:
        raise ValueError("Discard pile is empty.")

    if round_state["stage"] == "offer_first_upcard" and player_index != round_state["offered_to"]:
        raise ValueError("The opening discard is not currently offered to you.")

    card = round_state["discard"].pop()
    round_state["hands"][player_index].append(card)
    round_state["hands"][player_index] = sort_cards(
        round_state["hands"][player_index],
        state["players"][player_index].get("sort_mode", "rank"),
    )
    round_state["stage"] = "discard"
    round_state["pending_knock"] = False
    append_log(state, f"{state['players'][player_index]['name']} drew {card} from discard.")


def set_pending_knock(state: dict[str, Any], player_index: int, pending: bool) -> None:
    """Set or clear knock intent during the discard phase.

    Args:
        state: Match state.
        player_index: Acting player index.
        pending: Desired knock intent state.
    """
    validate_turn(state, player_index)
    round_state = state["round"]
    if round_state["stage"] != "discard":
        raise ValueError("You can only knock during your discard phase.")
    round_state["pending_knock"] = pending
    if pending:
        append_log(state, f"{state['players'][player_index]['name']} is attempting to knock.")


def discard_card(state: dict[str, Any], player_index: int, card: str) -> None:
    """Discard a card and possibly end the round.

    Args:
        state: Match state.
        player_index: Acting player index.
        card: Card to discard.
    """
    validate_turn(state, player_index)
    round_state = state["round"]
    if round_state["round_over"]:
        raise ValueError("The round is over.")
    if round_state["stage"] != "discard":
        raise ValueError("You are not in the discard phase.")

    hand = round_state["hands"][player_index]
    if card not in hand:
        raise ValueError("That card is not in your hand.")

    hand.remove(card)
    round_state["discard"].append(card)
    append_log(state, f"{state['players'][player_index]['name']} discarded {card}.")

    if round_state["pending_knock"]:
        evaluation = evaluate_hand(hand)
        if evaluation["deadwood_value"] > 10:
            hand.append(card)
            hand[:] = sort_cards(hand, state["players"][player_index].get("sort_mode", "rank"))
            round_state["discard"].pop()
            raise ValueError(f"Deadwood must be 10 or less to knock. Current deadwood: {evaluation['deadwood_value']}.")
        finish_round(state, knocker=player_index)
        return

    round_state["turn"] = 1 - player_index
    round_state["stage"] = "draw"
    round_state["pending_knock"] = False


def set_sort_mode(state: dict[str, Any], player_index: int, mode: str) -> None:
    """Update a player's preferred hand sort mode.

    Args:
        state: Match state.
        player_index: Target player index.
        mode: `rank` or `suit`.
    """
    if mode not in ("rank", "suit"):
        raise ValueError("Sort mode must be 'rank' or 'suit'.")
    state["players"][player_index]["sort_mode"] = mode
    state["round"]["hands"][player_index] = sort_cards(state["round"]["hands"][player_index], mode)


def rename_player(state: dict[str, Any], player_index: int, name: str) -> None:
    """Set a player's display name.

    Args:
        state: Match state.
        player_index: Player index.
        name: New display name.
    """
    clean_name = (name or "").strip()[:24]
    state["players"][player_index]["name"] = clean_name or f"Player {player_index + 1}"


def make_public_state(state: dict[str, Any], viewer: int) -> dict[str, Any]:
    """Build a redacted state snapshot for one player.

    Args:
        state: Match state.
        viewer: Player index receiving the snapshot.

    Returns:
        JSON-serializable public state.
    """
    opponent = 1 - viewer
    round_state = state["round"]
    payload: dict[str, Any] = {
        "you": viewer,
        "players": [
            {"name": state["players"][0]["name"]},
            {"name": state["players"][1]["name"]},
        ],
        "scores": list(state["scores"]),
        "dealer": state["dealer"],
        "round_index": state["round_index"],
        "target_score": state["target_score"],
        "game_over": state["game_over"],
        "winner": state["winner"],
        "turn": round_state["turn"],
        "stage": round_state["stage"],
        "offered_to": round_state["offered_to"],
        "first_upcard": round_state["first_upcard"],
        "first_upcard_declines": list(round_state["first_upcard_declines"]),
        "pending_knock": round_state["pending_knock"] and round_state["turn"] == viewer,
        "stock_count": len(round_state["stock"]),
        "discard_top": round_state["discard"][-1] if round_state["discard"] else None,
        "your_hand": list(round_state["hands"][viewer]),
        "your_deadwood": evaluate_hand(round_state["hands"][viewer])["deadwood_value"],
        "opponent_count": len(round_state["hands"][opponent]),
        "round_over": round_state["round_over"],
        "log": list(state["log"]),
        "sort_mode": state["players"][viewer].get("sort_mode", "rank"),
    }

    if round_state["summary"] is not None:
        payload["summary"] = round_state["summary"]
        payload["revealed_hands"] = round_state["revealed_hands"]
    else:
        payload["summary"] = None
        payload["revealed_hands"] = None
    return payload
