import unittest
from datetime import datetime, timedelta, timezone

from ladies_v9.pricing.context import ContextMessage, build_context
from ladies_v9.pricing.normalize import extract_money, extract_price_duration_pairs
from ladies_v9.pricing.rules import extract_with_rules, merge_partial
from ladies_v9.pricing.signal import detect_price_signal


def _ctx(*messages):
    rows = []
    for i, (direction, text) in enumerate(messages, start=1):
        rows.append(ContextMessage(i, direction, "TEXT", text, datetime(2026, 10, 12, 18, i, tzinfo=timezone.utc)))
    return build_context(thread_id=1, person_id=1, rows=rows, seed_message_id=rows[-1].message_id)


class PricingRulesTests(unittest.TestCase):
    def test_signal_detects_duration_price_pair(self):
        result = detect_price_signal("30 min 100 €, 1h 160 €")
        self.assertTrue(result.strong)
        self.assertIn("duration_amount", result.reasons)

    def test_signal_understands_bare_answer_after_price_question(self):
        result = detect_price_signal("100", previous_outgoing="Was kostet 30 Minuten?")
        self.assertTrue(result.strong)
        self.assertIn("bare_answer", result.reasons)

    def test_rule_extractor_builds_offers_and_service_surcharge(self):
        context = _ctx(
            ("OUT", "Was kostet es bei dir?"),
            ("IN", "30 min 100 €, 1h 160 €"),
            ("IN", "Küssen inklusive"),
            ("IN", "AV +50 €"),
        )
        proposal = extract_with_rules(context)
        self.assertEqual(
            {(x["duration_minutes"], x["amount_cents"]) for x in proposal["offers"]},
            {(30, 10000), (60, 16000)},
        )
        rules = {(x["service_key"], x["status"], x["amount_cents"]) for x in proposal["service_rules"]}
        self.assertIn(("KISSING", "INCLUDED", None), rules)
        self.assertIn(("ANAL", "SURCHARGE", 5000), rules)

    def test_compact_multiline_price_list_from_real_chat(self):
        message = """👄Hallo Schatz 👄

♥️Mein Preis ♥️

 50 20 Minuten
70 30 Minuten
10045 Minuten
130 Std

💖Mein Service💖

Blassen f**cken Massage leken Anfassen Stellungen wechseln 69 d@ggy

Adresse
Leipzig
Härtelstr.9a"""
        proposal = extract_with_rules(_ctx(("IN", message)))
        self.assertEqual(
            {(x["duration_minutes"], x["amount_cents"]) for x in proposal["offers"]},
            {(20, 5000), (30, 7000), (45, 10000), (60, 13000)},
        )
        self.assertEqual(proposal["update_mode"], "FULL")
        signal = detect_price_signal(message)
        self.assertTrue(signal.strong)
        self.assertIn("structured_price_rows", signal.reasons)

    def test_compact_price_and_duration_without_space_is_split_plausibly(self):
        rows = extract_price_duration_pairs("10045 Minuten")
        self.assertEqual([(x["amount_cents"], x["duration_minutes"]) for x in rows], [(10000, 45)])

    def test_hour_price_shorthand_means_one_hour(self):
        rows = extract_price_duration_pairs("130 Std")
        self.assertEqual([(x["amount_cents"], x["duration_minutes"]) for x in rows], [(13000, 60)])
        self.assertEqual(extract_price_duration_pairs("2 Std"), [])

    def test_partial_merge_keeps_unmentioned_offer(self):
        base = extract_with_rules(_ctx(("IN", "30 min 100 €, 1h 160 €")))
        patch = extract_with_rules(_ctx(("IN", "1h jetzt 180 €")), has_existing_price_list=True)
        merged = merge_partial(base, patch)
        self.assertEqual(
            {(x["duration_minutes"], x["amount_cents"]) for x in merged["offers"]},
            {(30, 10000), (60, 18000)},
        )

    def test_numeric_reply_uses_single_duration_from_question(self):
        context = _ctx(("OUT", "Was kosten 30 Minuten?"), ("IN", "100"))
        proposal = extract_with_rules(context)
        self.assertEqual(proposal["offers"][0]["duration_minutes"], 30)
        self.assertEqual(proposal["offers"][0]["amount_cents"], 10000)
        self.assertEqual({x["message_id"] for x in proposal["offers"][0]["evidence"]}, {1, 2})

    def test_duration_range_is_not_misread_as_money_range(self):
        self.assertEqual(extract_money("30-60 min"), [])
        proposal = extract_with_rules(_ctx(("IN", "30-60 min")))
        self.assertEqual(proposal["offers"], [])

    def test_old_price_question_cannot_cross_question_gap(self):
        start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        rows = [
            ContextMessage(1, "OUT", "TEXT", "Was kosten 30 Minuten?", start),
            ContextMessage(2, "IN", "TEXT", "100", start + timedelta(hours=2)),
        ]
        context = build_context(thread_id=1, person_id=1, rows=rows, seed_message_id=2)
        self.assertEqual([x.message_id for x in context.messages], [2])
        self.assertEqual(extract_with_rules(context)["offers"], [])


if __name__ == "__main__":
    unittest.main()
