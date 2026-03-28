"""Regression tests for authoritative Gin Rummy game state transitions."""

from __future__ import annotations

import unittest

from engine import (
    apply_layoffs,
    continue_after_round,
    discard_card,
    draw_from_discard,
    draw_from_stock,
    evaluate_hand,
    finish_round,
    make_initial_state,
    make_public_state,
    must_draw_from_stock,
)


def advance_past_opening_offer(state: dict) -> int:
    """Advance a fresh round through the opening-upcard decision flow.

    Returns the player index who is forced to draw from stock after both
    players decline the initial upcard.
    """
    first_player = state["round"]["turn"]
    draw_from_stock(state, first_player)
    second_player = state["round"]["turn"]
    draw_from_stock(state, second_player)
    forced_draw_player = state["round"]["turn"]
    return forced_draw_player


class EngineRegressionTests(unittest.TestCase):
    """Protect high-risk game-state transitions from accidental regressions."""

    def test_opening_offer_passes_force_non_dealer_to_draw_from_stock(self) -> None:
        state = make_initial_state(seed=1)

        forced_draw_player = advance_past_opening_offer(state)

        self.assertEqual(state["round"]["stage"], "draw")
        self.assertTrue(must_draw_from_stock(state["round"]))
        self.assertEqual(forced_draw_player, 1 - state["dealer"])

        with self.assertRaisesRegex(ValueError, "must draw from stock"):
            draw_from_discard(state, forced_draw_player)

    def test_forced_opening_stock_draw_only_blocks_discard_once(self) -> None:
        state = make_initial_state(seed=1)
        forced_draw_player = advance_past_opening_offer(state)

        draw_from_stock(state, forced_draw_player)
        self.assertFalse(must_draw_from_stock(state["round"]))

        drawn_card = state["round"]["hands"][forced_draw_player][-1]
        discard_card(state, forced_draw_player, drawn_card)

        viewer_state = make_public_state(state, state["round"]["turn"])
        self.assertEqual(viewer_state["stage"], "draw")
        self.assertFalse(viewer_state["must_draw_from_stock"])

    def test_drawn_card_stays_on_right_until_discard_then_melds_regroup(self) -> None:
        state = make_initial_state(seed=1)
        forced_draw_player = advance_past_opening_offer(state)

        draw_from_stock(state, forced_draw_player)
        last_drawn = state["round"]["last_drawn"][forced_draw_player]
        self.assertIsNotNone(last_drawn)
        self.assertEqual(state["round"]["hands"][forced_draw_player][-1], last_drawn)

        discard_card(state, forced_draw_player, last_drawn)

        hand = state["round"]["hands"][forced_draw_player]
        evaluation = evaluate_hand(hand)
        melded_cards = [card for meld in evaluation["melds"] for card in meld]
        deadwood_cards = list(evaluation["deadwood"])
        if melded_cards and deadwood_cards:
            last_meld_index = max(hand.index(card) for card in melded_cards)
            first_deadwood_index = min(hand.index(card) for card in deadwood_cards)
            self.assertLess(last_meld_index, first_deadwood_index)

    def test_match_wins_persist_across_new_match_reset(self) -> None:
        state = make_initial_state(seed=1)
        state["target_score"] = 1

        finish_round(state, knocker=0)
        self.assertTrue(state["game_over"])
        self.assertEqual(sum(state["match_wins"]), 1)
        wins_before_reset = list(state["match_wins"])

        continue_after_round(state)
        self.assertEqual(state["scores"], [0, 0])
        self.assertEqual(state["match_wins"], wins_before_reset)
        self.assertFalse(state["game_over"])

    def test_layoff_sequence_metadata_is_present(self) -> None:
        knocker_eval = evaluate_hand(["H7", "H8", "H9", "C4", "D4", "S4", "CA", "D2", "S3", "CK"])
        defender_eval = evaluate_hand(["H10", "CJ", "DQ", "SA", "C2", "D3", "S5", "C6", "D7", "S8"])

        after_layoff = apply_layoffs(knocker_eval, defender_eval)

        self.assertIn("layoff_sequence", after_layoff)
        self.assertIn("melds_after_layoff", after_layoff)
        self.assertTrue(after_layoff["layoff_sequence"])
        first_step = after_layoff["layoff_sequence"][0]
        self.assertIn("card", first_step)
        self.assertIn("target_meld", first_step)

    def test_public_state_contains_expected_player_safe_fields(self) -> None:
        state = make_initial_state(seed=2)
        public_state = make_public_state(state, 0)

        self.assertEqual(len(public_state["your_hand"]), 10)
        self.assertIsInstance(public_state["match_wins"], list)
        self.assertIn("your_deadwood", public_state)
        self.assertIn("your_last_drawn", public_state)
        self.assertEqual(public_state["opponent_count"], 10)


if __name__ == "__main__":
    unittest.main()
