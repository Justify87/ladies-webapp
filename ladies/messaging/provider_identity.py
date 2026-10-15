from __future__ import annotations

from typing import Any, Mapping


def normalize_provider_key(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the Baileys message-key fields used by the application."""
    if not isinstance(value, Mapping):
        return None
    message_id = value.get("id")
    remote_jid = value.get("remoteJid") or value.get("remote_jid")
    if not message_id or not remote_jid:
        return None
    from_me = value.get("fromMe")
    if from_me is None:
        from_me = value.get("from_me")
    return {
        "remoteJid": str(remote_jid),
        "remoteJidAlt": value.get("remoteJidAlt") or value.get("remote_jid_alt"),
        "participant": value.get("participant"),
        "participantAlt": value.get("participantAlt") or value.get("participant_alt"),
        "id": str(message_id),
        "fromMe": bool(from_me),
    }


def message_identity(value: Mapping[str, Any] | None) -> str | None:
    """Match Baileys' stringifyMessageKey: remoteJid,id,fromMe."""
    key = normalize_provider_key(value)
    if key is None:
        return None
    return f"{key['remoteJid']},{key['id']},{'1' if key['fromMe'] else '0'}"


def message_raw_id(value: Mapping[str, Any] | str | None) -> str | None:
    if isinstance(value, Mapping):
        raw = value.get("id")
        return str(raw) if raw else None
    if not value:
        return None
    text = str(value)
    parts = text.rsplit(",", 2)
    if len(parts) == 3 and parts[2] in {"0", "1"}:
        return parts[1] or None
    return text


__all__ = ["message_identity", "message_raw_id", "normalize_provider_key"]
