from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def main() -> None:
    models = source("ladies/messaging/models.py")
    require("class ChatClassification" in models, "thread classification model")
    require("class EndpointClassification" in models, "endpoint classification model")
    require('__tablename__ = "chat_classifications"' in models, "thread classification table")
    require('__tablename__ = "endpoint_classifications"' in models, "endpoint classification table")

    classifier = source("ladies/messaging/classification.py")
    ast.parse(classifier)
    for forbidden in ("PersonUserState", "favorite", "preference", "DISLIKE", "LIKE"):
        require(forbidden not in classifier, f"classification independent from discovery state: {forbidden}")
    require('LADIES_CONFIRMED = "LADIES_CONFIRMED"' in classifier, "confirmed state")
    require('UNKNOWN = "UNKNOWN"' in classifier, "unknown state")
    require('NON_LADIES = "NON_LADIES"' in classifier, "non-Ladies state")
    require("if row.status in {LADIES_CONFIRMED, NON_LADIES}:" in classifier, "sticky explicit decisions")
    require("if any(item.status == NON_LADIES" in classifier, "endpoint exclusion survives thread moves")
    require("if thread.person_id is not None:" in classifier, "persisted exact identity can promote unknown")
    require("Ein Ladies-Chat muss zuerst einer Ladies-Person zugeordnet werden" in classifier, "confirmed requires person identity")

    service = source("ladies/messaging/service_classified.py")
    ast.parse(service)
    require("_auto_identity_allowed" in service, "automatic identity guard")
    require("row.status != NON_LADIES" in service, "manual exclusion blocks automatic identity")
    require("if not add_system_message and not self._auto_identity_allowed" in service, "only automatic assignment is blocked")

    core_routes = source("ladies/messaging/routes_v2.py")
    require("from .service_classified import MessagingService" in core_routes, "canonical routes use classification-aware service")
    public_routes = source("ladies/messaging/routes.py")
    require("from .routes_classified import install_messaging" in public_routes, "classification router is public entry point")
    routes = source("ladies/messaging/routes_classified.py")
    require('@app.get("/api/messaging/inbox")' in routes, "classified Ladies inbox route")
    require('@app.get("/api/messaging/review")' in routes, "unassigned review route")
    require('@app.post("/api/messaging/threads/<int:thread_id>/classification")' in routes, "manual classification route")
    require('service.threads("ALL")' in routes, "classification projects from complete persisted provider store")

    effects = source("ladies/static/explorer/js/effects/messaging.js")
    require("api.messagingInbox('ALL')" in effects, "frontend loads classified inbox")
    require("api.messagingReview()" in effects, "frontend loads review queue")
    require("api.threads(" not in effects, "frontend no longer renders raw provider thread list")
    require("explicit_profile_action" in effects, "profile action confirms Ladies identity")
    require("manual_assignment" in effects, "manual assignment confirms Ladies identity")
    require("manual_exclusion" in effects, "manual exclusion is persisted")

    selectors = source("ladies/static/explorer/js/app/selectors.js")
    require("LADIES_CONFIRMED" in selectors, "normal inbox uses confirmed classification")
    require("classification!=='UNKNOWN'" in selectors, "review queue uses unknown classification")
    require("NON_LADIES" not in source("ladies/static/explorer/js/ui/screens/message_list.js"), "non-Ladies chats have no visible list surface")

    commands = source("ladies/static/explorer/js/app/commands.js")
    require("review-assign" in commands, "review queue assignment action")
    require("review-non-ladies" in commands, "review queue exclusion action")

    print("messaging classification architecture checks passed")


if __name__ == "__main__":
    main()
