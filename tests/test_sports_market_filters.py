from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import new_sports_market_filters as filters  # noqa: E402


def pm_event(title: str, market: dict) -> dict:
    return {
        "id": "event-1",
        "slug": "team-alpha-vs-team-beta",
        "title": title,
        "active": True,
        "closed": False,
        "markets": [market],
    }


def pm_market(question: str, outcomes: str = '["Team Alpha","Team Beta"]') -> dict:
    return {
        "id": "market-1",
        "question": question,
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "outcomes": outcomes,
        "clobTokenIds": '["token-alpha","token-beta"]',
    }


class SportsMarketFilterTests(unittest.TestCase):
    def test_pm_two_participant_winner_market_is_eligible(self) -> None:
        event = pm_event("Team Alpha vs. Team Beta", pm_market("Team Alpha vs. Team Beta winner"))

        self.assertTrue(filters.is_pm_binary_winner_market(event, event["markets"][0]))
        self.assertEqual(filters.eligible_pm_markets(event), [event["markets"][0]])

    def test_pm_derivatives_props_and_yes_no_propositions_are_skipped(self) -> None:
        bad_markets = [
            pm_market("Team Alpha spread"),
            pm_market("Total runs over under"),
            pm_market("Team Alpha player prop"),
            pm_market("World Cup championship winner"),
            pm_market("Team Alpha option payout"),
            pm_market("Crypto perpetual market"),
            pm_market("Will Team Alpha win?", outcomes='["Yes","No"]'),
        ]

        for market in bad_markets:
            with self.subTest(question=market["question"]):
                event = pm_event("Team Alpha vs. Team Beta", market)
                self.assertFalse(filters.is_pm_binary_winner_market(event, market))

    def test_ks_exact_two_participant_event_markets_are_eligible(self) -> None:
        markets = [
            {
                "ticker": "KXMLBGAME-ALPBET-ALP",
                "event_ticker": "KXMLBGAME-ALPBET",
                "title": "Team Alpha vs Team Beta winner",
                "yes_sub_title": "Team Alpha",
                "status": "open",
            },
            {
                "ticker": "KXMLBGAME-ALPBET-BET",
                "event_ticker": "KXMLBGAME-ALPBET",
                "title": "Team Alpha vs Team Beta winner",
                "yes_sub_title": "Team Beta",
                "status": "open",
            },
        ]

        self.assertEqual(filters.eligible_ks_binary_event_markets(markets), markets)
        self.assertEqual(filters.eligible_ks_market_groups(markets), markets)

    def test_ks_three_way_futures_props_and_option_like_titles_are_skipped(self) -> None:
        three_way = [
            {"ticker": "KXGAME-A", "event_ticker": "KXGAME", "title": "Alpha vs Beta", "yes_sub_title": "Alpha", "status": "open"},
            {"ticker": "KXGAME-B", "event_ticker": "KXGAME", "title": "Alpha vs Beta", "yes_sub_title": "Beta", "status": "open"},
            {"ticker": "KXGAME-DRAW", "event_ticker": "KXGAME", "title": "Alpha vs Beta draw", "yes_sub_title": "Draw", "status": "open"},
        ]
        futures = [
            {
                "ticker": "KXFUTURE-A",
                "event_ticker": "KXFUTURE",
                "title": "League championship winner",
                "yes_sub_title": "Alpha",
                "status": "open",
            },
            {
                "ticker": "KXFUTURE-B",
                "event_ticker": "KXFUTURE",
                "title": "League championship winner",
                "yes_sub_title": "Beta",
                "status": "open",
            },
        ]
        option_like = [
            {"ticker": "KXOPT-A", "event_ticker": "KXOPT", "title": "Option payout", "yes_sub_title": "Alpha", "status": "open"},
            {"ticker": "KXOPT-B", "event_ticker": "KXOPT", "title": "Option payout", "yes_sub_title": "Beta", "status": "open"},
        ]

        self.assertEqual(filters.eligible_ks_binary_event_markets(three_way), [])
        self.assertEqual(filters.eligible_ks_binary_event_markets(futures), [])
        self.assertEqual(filters.eligible_ks_binary_event_markets(option_like), [])


if __name__ == "__main__":
    unittest.main()
