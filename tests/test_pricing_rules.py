from datetime import datetime, timezone

from ladies_v9.pricing.context import ContextMessage, build_context
from ladies_v9.pricing.rules import extract_with_rules, merge_partial
from ladies_v9.pricing.signal import detect_price_signal


def _ctx(*messages):
    rows=[]
    for i,(direction,text) in enumerate(messages, start=1):
        rows.append(ContextMessage(i,direction,"TEXT",text,datetime(2026,10,12,18,i,tzinfo=timezone.utc)))
    return build_context(thread_id=1,person_id=1,rows=rows,seed_message_id=rows[-1].message_id)


def test_signal_detects_duration_price_pair():
    result=detect_price_signal("30 min 100 €, 1h 160 €")
    assert result.strong
    assert "duration_amount" in result.reasons


def test_signal_understands_bare_answer_after_price_question():
    result=detect_price_signal("100",previous_outgoing="Was kostet 30 Minuten?")
    assert result.strong
    assert "bare_answer" in result.reasons


def test_rule_extractor_builds_offers_and_service_surcharge():
    context=_ctx(
        ("OUT","Was kostet es bei dir?"),
        ("IN","30 min 100 €, 1h 160 €"),
        ("IN","Küssen inklusive"),
        ("IN","AV +50 €"),
    )
    proposal=extract_with_rules(context)
    assert {(x["duration_minutes"],x["amount_cents"]) for x in proposal["offers"]}=={(30,10000),(60,16000)}
    rules={(x["service_key"],x["status"],x["amount_cents"]) for x in proposal["service_rules"]}
    assert ("KISSING","INCLUDED",None) in rules
    assert ("ANAL","SURCHARGE",5000) in rules


def test_partial_merge_keeps_unmentioned_offer():
    base=extract_with_rules(_ctx(("IN","30 min 100 €, 1h 160 €")))
    patch=extract_with_rules(_ctx(("IN","1h jetzt 180 €")),has_existing_price_list=True)
    merged=merge_partial(base,patch)
    assert {(x["duration_minutes"],x["amount_cents"]) for x in merged["offers"]}=={(30,10000),(60,18000)}


def test_numeric_reply_uses_single_duration_from_question():
    context=_ctx(("OUT","Was kosten 30 Minuten?"),("IN","100"))
    proposal=extract_with_rules(context)
    assert proposal["offers"][0]["duration_minutes"]==30
    assert proposal["offers"][0]["amount_cents"]==10000
    assert {x["message_id"] for x in proposal["offers"][0]["evidence"]}=={1,2}
