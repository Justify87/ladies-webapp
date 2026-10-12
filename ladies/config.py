from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .source import CATEGORY_MAP, FILTER_GROUP_ORDER, FILTER_VALUE_MAP

CATEGORY_LABELS = {
    "": "Alle",
    "huren-hostessen": "Ladies",
    "bordelle-laufhaeuser": "Bordelle & Laufhäuser",
    "escort": "Escort",
    "transen-shemale": "Trans",
    "erotische-massage": "Erotische Massage",
    "domina-sm": "Domina / SM",
    "fkk-saunaclubs": "FKK / Saunaclubs",
    "swinger": "Swinger",
    "telefonsex": "Telefonsex",
    "onlineservice": "Online-Service",
}


def application_version() -> str:
    try:
        return version("ladies-explorer")
    except PackageNotFoundError:
        return "9.6.0"


def frontend_config(*, default_city: str, default_radius: int) -> dict:
    categories = []
    seen = set()
    for incoming, canonical in CATEGORY_MAP.items():
        if incoming != canonical or canonical in seen:
            continue
        seen.add(canonical)
        categories.append({"value": canonical, "label": CATEGORY_LABELS.get(canonical, canonical or "Alle")})

    groups = []
    for group in FILTER_GROUP_ORDER:
        if group in {"alter", "groesse"}:
            continue
        values = FILTER_VALUE_MAP.get(group, {})
        groups.append({
            "key": group,
            "options": [{"label": label, "value": slug} for label, slug in values.items()],
        })

    return {
        "version": application_version(),
        "defaults": {"city": default_city, "radius_km": int(default_radius)},
        "categories": categories,
        "filter_groups": groups,
    }
