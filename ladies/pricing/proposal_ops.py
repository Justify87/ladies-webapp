from __future__ import annotations

from copy import deepcopy
from typing import Any

from .schema import empty_proposal, validate_proposal


def _key_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def merge_partial(base: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    patch = validate_proposal(patch)
    if not base or patch["update_mode"] == "FULL":
        out = deepcopy(patch)
        out["update_mode"] = "FULL"
        return out

    base = validate_proposal(base)
    out = deepcopy(base)
    by_duration = {
        (item.get("duration_minutes"), item.get("duration_max_minutes")): item
        for item in out["offers"]
    }
    for item in patch["offers"]:
        by_duration[(item.get("duration_minutes"), item.get("duration_max_minutes"))] = deepcopy(item)
    out["offers"] = sorted(
        by_duration.values(),
        key=lambda item: (item.get("duration_minutes") is None, item.get("duration_minutes") or 99999),
    )

    by_service = {
        (item.get("service_key") or "", _key_text(item.get("raw_label"))): item
        for item in out["service_rules"]
    }
    for item in patch["service_rules"]:
        by_service[(item.get("service_key") or "", _key_text(item.get("raw_label")))] = deepcopy(item)
    out["service_rules"] = list(by_service.values())

    by_condition = {
        (item.get("condition_type"), _key_text(item.get("label"))): item
        for item in out["conditions"]
    }
    for item in patch["conditions"]:
        by_condition[(item.get("condition_type"), _key_text(item.get("label")))] = deepcopy(item)
    out["conditions"] = list(by_condition.values())
    out["uncertainties"] = list(
        dict.fromkeys((out.get("uncertainties") or []) + (patch.get("uncertainties") or []))
    )
    out["update_mode"] = "FULL"
    return validate_proposal(out)


def proposal_diff(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, Any]:
    before = validate_proposal(before) if before else empty_proposal()
    after = validate_proposal(after)
    old = {
        (item.get("duration_minutes"), item.get("duration_max_minutes")): item
        for item in before["offers"]
    }
    new = {
        (item.get("duration_minutes"), item.get("duration_max_minutes")): item
        for item in after["offers"]
    }
    offers = []
    for key in sorted(set(old) | set(new), key=lambda value: (value[0] is None, value[0] or 99999)):
        left, right = old.get(key), new.get(key)
        if left == right:
            state = "UNCHANGED"
        elif left is None:
            state = "ADDED"
        elif right is None:
            state = "REMOVED"
        else:
            state = "CHANGED"
        offers.append({"key": list(key), "before": left, "after": right, "state": state})
    return {
        "offers": offers,
        "changed": any(item["state"] != "UNCHANGED" for item in offers)
        or before["service_rules"] != after["service_rules"]
        or before["conditions"] != after["conditions"],
    }
