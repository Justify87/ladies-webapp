from __future__ import annotations

from .utils import clean_text

CATEGORY_MAP = {
    "": "",
    "huren-hostessen": "huren-hostessen",
    "locations": "bordelle-laufhaeuser",  # compatibility with V6
    "bordelle-laufhaeuser": "bordelle-laufhaeuser",
    "escort": "escort",
    "transen-shemale": "transen-shemale",
    "erotische-massage": "erotische-massage",
    "domina-sm": "domina-sm",
    "fkk-clubs": "fkk-saunaclubs",  # compatibility with V6
    "fkk-saunaclubs": "fkk-saunaclubs",
    "swinger": "swinger",
    "telefonsex": "telefonsex",
    "onlineservice": "onlineservice",
}

FILTER_GROUP_MAP = {
    "figur": "figur", "typ": "typ", "geschlecht": "geschlecht",
    "busen": "busen", "intimbereich": "intimbereich", "hautton": "hautton",
    "haarfarbe": "haarfarbe", "haarlaenge": "haarlaenge", "augenfarbe": "augenfarbe",
    "koerperschmuck": "koerperschmuck", "sonstiges": "sonstiges", "service": "service",
    "treff": "treff", "treffpunkt": "treff", "location": "location", "zeiten": "zeiten",
}

FILTER_VALUE_MAP: dict[str, dict[str, str]] = {
    'figur': {'sehr schlank': 'skinny', 'schlank': 'schlank', 'mittel': 'normal', 'mollig': 'mollig', 'sehr mollig': 'dick'},
    'typ': {'afrikanisch': 'afrikanisch', 'asiatisch': 'asia', 'deutsch': 'deutsch', 'exotisch': 'exotisch', 'latina': 'latina', 'orientalisch': 'arabisch', 'osteuropäisch': 'osteuropaeisch', 'skandinavisch': 'skandinavisch', 'westeuropäisch': 'europaeisch'},
    'geschlecht': {'weiblich': 'weiblich', 'männlich': 'maennlich', 'trans': 'trans', 'Paar/Duo': 'paar-duo'},
    'busen': {'klein': 'klein', 'mittel': 'mittel', 'groß': 'gross', 'riesig': 'dick', 'Naturbusen': 'naturbusen'},
    'intimbereich': {'total rasiert': 'rasiert', 'teilrasiert': 'teilrasiert', 'behaart': 'behaart', 'stark behaart': 'stark-behaart'},
    'hautton': {'helle Haut': 'hell', 'mittlere Haut': 'neutral', 'bräunliche Haut': 'braun', 'schwarze Haut': 'schwarz'},
    'haarfarbe': {'blond': 'blond', 'dunkelblond': 'dunkelblond', 'brünett': 'bruenett', 'rotbraun': 'rotbraun', 'dunkelbraun': 'dunkelbraun', 'schwarz': 'schwarz', 'rot': 'rot', 'grau': 'grau'},
    'haarlaenge': {'Glatze': 'glatze', 'kurz': 'kurz', 'kinnlang': 'kinnlang', 'schulterlang': 'schulterlang', 'rückenlang': 'rueckenlang'},
    'augenfarbe': {'braun': 'braun', 'blau': 'blau', 'grün': 'gruen', 'blau-grün, grau': 'blau-gruen-grau'},
    'koerperschmuck': {'Intim-Piercing': 'intimpiercing', 'Brust-Piercing': 'brustwarzenpiercing', 'Gesichts-/Nabelp.': 'piercing', 'Zungen-Piercing': 'zungenpiercing', 'Tattoos': 'sexy-tattoos', 'kein Körperschmuck': 'ohne'},
    'sonstiges': {'Nichtraucher': 'nichtraucher', 'Gesicht erkennbar': 'gesicht-erkennbar', 'mit Video': 'videos', 'Empfehlung': 'empfehlung', 'geimpft': 'geimpft', 'Mit 18+ Inhalten': 'ab18', 'mit Inhalten': 'ab18'},
    'service': {'AV': 'anal', 'ZK': 'zungenkuss', 'GV': 'gv-sex', 'GF6': 'girlfriendsex', 'Girlfriend-Sex': 'girlfriendsex', 'Franz.': 'franzoesisch', 'nur Massage': 'massage-only', 'D**p Thr**t': 'deepthroat', 'Franz. bei ihr': 'lecken', '69': '69-stellung', 'AV bei ihm': 'av-passiv', 'FA aktiv': 'fa-aktiv', 'FA passiv': 'fa-passiv', 'ZA aktiv': 'za-aktiv', 'ZA passiv': 'za-passiv', 'NS aktiv': 'natursekt-aktiv', 'NS passiv': 'natursekt-passiv', 'KV aktiv': 'kaviar-aktiv', 'KV passiv': 'kaviar-passiv', 'dominant': 'dominant', 'devot': 'devot', 'Damen': 'damen', 'Paare': 'paare', 'Menschen mit Behinderung': 'behinderte', 'Flotter Dreier (MFF)': 'flotter-dreier-mff', 'Zu dritt (MMF)': 'flotter-dreier-mmf', 'Herrenrunde': 'herrenrunde', 'GS (Gruppen-S*x)': 'gruppensex', 'SW (S*ndw*ch)': 'sandwich', 'Body-to-Body M.': 'body-to-body-massage', 'erotische Massage': 'erotische-massage', 'Ganzkörperm.': 'ganzkoerpermassage', 'HE (Handentsp.)': 'handentspannung', 'Intim-Massage': 'intim-massage', 'Öl-Massage': 'oel-massage', 'Pärchen-Massage': 'paarmassage', 'Po-Massage': 'po-massage', 'Pr*stata-Massage': 'prostata-massage', 'Synchron-/Duo-M.': 'duo-massage', 'Tantra-Massage': 'tantra-massage', 'Thai-Massage': 'thai-massage', 'Bondage/Fess**ung': 'bondage', 'Fetisch-Spielchen': 'fetisch-spiele', 'Fesselspiele aktiv': 'fesselspiele-aktiv', 'Fesselspiele passiv': 'fesselspiele-passiv', 'FS (Facesitting)': 'facesitting', 'Lack, Leder u. Latex': 'lack-leder-fetisch', 'Verbalerotik': 'dirty-talk', 'WS': 'ws-erotik', 'Dusch-/Badeservice': 'baden-duschen', 'Fotos/Videos möglich': 'fotos-videos', 'Fuß-/Schuherotik': 'fusserotik-high-heels', 'Intimrasur': 'intimrasur', 'Strapserotik': 'strapserotik', 'gekonnter Striptease': 'striptease', 'LS/Duo (Lesboshow)': 'lesbenshow', 'Nylonerotik': 'nylon-fetisch', 'Outdoor-Erotik': 'outdoor-sex', 'Rollenspiele': 'erotische-rollenspiele', 'Squ*rting': 'squirting', 'Begleitservice': 'begleitservice', 'Frivoles Ausgehen': 'frivol-ausgehen', 'Urlaubsbegleitung': 'urlaubsbegleitung', 'Pärchenbesuche': 'paarbesuche', 'Swingerclub-Begl.': 'swingerclub', 'Übernacht. b. Gast': 'uebernachtung-gast', 'Wochenende': 'wochenendbegleitung', 'Gastrobegleitung': 'gastrobegleitung', 'Messebegleitung': 'messebegleitung'},
    'treff': {'besuchbar': 'treffen-besuchbar', 'Hausbesuche': 'treffen-haus', 'Hotelbesuche': 'treffen-hotel', 'Bürobesuche': 'treffen-buero'},
    'location': {'für Rollstuhlfahrer geeignet': 'rollstuhlfahrer-geeignet'},
    'zeiten': {'jetzt geöffnet': 'jetzt-offen', 'vor 10 Uhr': 'vor-10', 'auch Samstag': 'auch-samstag', 'heute geöffnet': 'heute-offen', 'nach 22 Uhr': 'nach-22', 'auch Sonntag': 'auch-sonntag'},
}

FILTER_GROUP_ORDER = [
    "alter", "groesse", "figur", "typ", "busen", "intimbereich", "hautton",
    "haarfarbe", "koerperschmuck", "sonstiges", "haarlaenge", "geschlecht",
    "augenfarbe", "service", "treff", "location", "zeiten",
]


def canonical_category(value: str) -> str:
    category = clean_text(value).strip("/")
    if category in CATEGORY_MAP:
        return CATEGORY_MAP[category]
    raise ValueError(f"Unbekannte Rubrik: {category}")


def filter_value_slug(group: str, value: str) -> str:
    mapped_group = FILTER_GROUP_MAP.get(group)
    if not mapped_group:
        raise ValueError(f"Unbekannte Filtergruppe: {group}")
    values = FILTER_VALUE_MAP.get(mapped_group, {})
    cleaned = clean_text(value)
    if cleaned in values:
        return values[cleaned]
    if cleaned in values.values():
        return cleaned
    raise ValueError(f"Unbekannter Filterwert für {mapped_group}: {cleaned}")
