# Ladies Explorer

Lokale, mobile-first Web-App zum Durchsuchen, Wiedererkennen und Verwalten von Ladies-Profilen. Die Anwendung speichert Personen, Anzeigenhistorie, Medien, Likes/Favoriten, Treffen/Preise und einen integrierten personenbezogenen WhatsApp-Chat lokal.

## Start

Mit `uv`:

```bash
uv run python ladies_explorer.py --host 0.0.0.0
```

Oder direkt mit Python:

```bash
python ladies_explorer.py --host 0.0.0.0
```

Der Start ist selbstheilend ausgelegt:

- `pyproject.toml` ist die einzige Python-Dependency- und Versionsquelle;
- `uv run` synchronisiert daraus die Python-Abhängigkeiten;
- bei direktem Start in einer Virtualenv werden fehlende Pakete in genau diese Umgebung installiert;
- bei System-Python wird das System nicht ungefragt verändert: vorhandenes `uv` wird benutzt, sonst lokal `.venv/` angelegt;
- eine kompatible Node.js-/npm-Installation wird verwendet, andernfalls eine projektlokale Runtime unter `.runtime/` eingerichtet;
- WhatsApp-Abhängigkeiten werden reproduzierbar per `npm ci` aus `package-lock.json` installiert.

Für den Zugriff vom Smartphone im selben WLAN gibt der Start mit `--host 0.0.0.0` eine Pairing-URL aus. Diese zuerst auf dem Smartphone öffnen.

## Lokale Dateien

Standardmäßig:

- Datenbank: `ladies.sqlite3`
- Archiv/Laufzeitdaten: `ladies_data/`
- Log: `logs/ladies_explorer.log`
- optionale Python-Umgebung: `.venv/`
- optionale Node-Runtime: `.runtime/`

Bestehende alte Standardpfade werden beim Start übernommen:

- `ladies_v9.sqlite3` → `ladies.sqlite3`
- `ladies_v9_data/` → `ladies_data/`

Eigene Pfade über `--db` und `--data-dir` bleiben möglich.

## Architektur V9.6

Die Anwendung bleibt bewusst ein kompakter lokaler Monolith: Flask + SQLAlchemy + SQLite im Backend, Vanilla JavaScript im Browser und ein separater interner Baileys-Worker für WhatsApp.

```text
ladies_explorer.py                 Start, Python-/Node-Bootstrap
pyproject.toml                     Version + Python-Abhängigkeiten
ladies/
  app.py                           Integration der Features
  web.py                           Core-HTTP/API
  database.py                      SQLite/SQLAlchemy + Migration
  config.py                        öffentliche Frontend-Konfiguration
  errors.py                        einheitliche API-Fehler
  request_parsing.py               Request-Validierung
  logging_utils.py                 Log-Redaction
  core/
    repositories/                  fachliche Repository-Grenzen
    services/                      Search/Profile-Orchestrierung
  messaging/
    service.py                     kanonischer MessagingService
    routes.py                      Messaging-HTTP/API
    gateway.py                     Python ↔ Baileys Worker/IPC
    media_store.py                 sichere WhatsApp-Medienablage
    history_cache.py               begrenzte Recent-History-Rehydration
    whatsapp_worker/               Baileys-7-Worker
  pricing/
    service.py                     Price Intelligence
    extraction_core.py             Extraktions-/Revisionslogik
    media_locator.py               sicherer lokaler Medienzugriff
  static/                          Browser-Frontend
  templates/                       HTML
tests/                             Unit-/Regression-/Integrationstests
```

Core, Messaging und Pricing teilen sich innerhalb einer App dieselbe `Database`-Instanz. Schemaerstellung und Migration erfolgen zentral vor Nutzung der Feature-Services.

## Identität

`Person` bleibt die kanonische fachliche Identität. Eine Person ist weder eine einzelne Anzeige noch eine Telefonnummer oder WhatsApp-JID/LID.

Zu einer Person können gehören:

- mehrere/erneuerte Ladies-Anzeigen;
- mehrere Telefonnummern;
- WhatsApp-PN-/LID-Endpunkte;
- Bild-/Telefon-/Source-ID-Evidenz;
- Historie, Medien, Preise und Treffen.

## Nachrichten / WhatsApp

WhatsApp läuft über den internen Baileys-Worker. Es gibt keinen zweiten HTTP-Server und keinen separat zu startenden Prozess für den Benutzer.

Direkte Baileys-Abhängigkeit:

```text
@whiskeysockets/baileys@7.0.0-rc14
```

Unterstützt werden unter anderem:

- persistente QR-Kopplung und automatischer Reconnect;
- PN/LID-aware Personen-/Endpoint-Zuordnung;
- Text, Bilder, Videos, Audio/Voice und Dokumente;
- Replies, Reactions, Edit/Delete und Read/Presence;
- View-once mit privater lokaler Ablage und einmaligem Abruf;
- mehrere Telefonnummern derselben Person in einem personenbezogenen Chat;
- begrenzte Recent-History-Rehydration nach einer neu aufgebauten App-Datenbank.

Im normalen Nachrichten-Tab erscheinen zugeordnete Chats, sobald mindestens eine echte ein- oder ausgehende Nachricht existiert. Leere Platzhalter bleiben verborgen. Favoriten werden zuerst sortiert, danach Likes und anschließend neutrale Unterhaltungen. Unzugeordnete WhatsApp-Kontakte bleiben separat erhalten.

Ein Like kann im Hintergrund eine best-effort Rehydration der letzten Nachrichten auslösen. Diese Arbeit läuft in einem kleinen begrenzten, deduplizierenden In-Process-Executor und kann einen erfolgreichen Like nicht in einen Fehler verwandeln.

## Pricing / Price Intelligence

Die Price-Intelligence-Schicht bleibt rule-first und lokal integrierbar. Sie kann relevante Chattexte und Medienkontext erfassen, optional OCR verwenden, Preisvorschläge erzeugen und akzeptierte Änderungen als nachvollziehbare Revisionen/Evidenz persistieren. OCR-/Model-Runtimes sind optionale Adapter und keine Voraussetzung für die Kernanwendung.

## Suche und Filter

Search- und Profile-Orchestrierung liegen außerhalb der Flask-Routen in Application Services. Der Browser erhält Kategorien, Filterkatalog, Defaults und App-Version aus der zentralen Backend-Konfiguration (`/api/config`) statt dieselben Filterdefinitionen mehrfach zu pflegen.

## Sicherheit im Heimnetz

Localhost bleibt ohne zusätzliche Anmeldung nutzbar. Für andere Geräte im LAN schützt die App `/api/*` und `/archive/*`.

Pairing und dauerhafte Browser-Session sind getrennt:

1. der Launcher zeigt ein Pairing-Secret/URL;
2. erfolgreiches Pairing erzeugt eine eigene zufällige Device-Session;
3. der Browser speichert nur diese Session als `HttpOnly`, `SameSite=Strict` Cookie;
4. das Pairing-Secret wird nicht als langfristiges Browser-Credential wiederverwendet.

Weitere Härtungen umfassen unter anderem URL-Allowlisting, Media-Root-Prüfungen, View-once-Atomics, Upload-/Proxy-Größenlimits, sichere Attachment-Auslieferung, Request-IDs, redigierte Logs und browserfreundliche Fehler ohne interne Exception-Details.

## Datenbank

SQLite bleibt bewusst erhalten. Die App verwendet unter anderem:

- WAL;
- Foreign Keys;
- Busy Timeout;
- `PRAGMA user_version` als Schema-Version;
- Backup vor tatsächlichen Migrationen;
- fail-closed Verhalten bei nicht sicher automatisch migrierbaren Legacy-Strukturen.

Bestehende Datenbanken dürfen durch ein V9.6-Upgrade nicht stillschweigend neu erzeugt oder verworfen werden.

## Tests

Lokal:

```bash
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub Actions prüft Python-Compile, Unit-/Regressionstests, Flask-Smoke-Test, Bootstrap auf Linux/macOS, sämtliche Browser-JavaScript-Dateien, den Node-Worker, reproduzierbares `npm ci` und die erwartete Baileys-Version.

## Bewusste Grenzen

- reale WhatsApp-QR-Kopplung und echte E2E-Nachrichten benötigen einen manuellen Test mit einem realen WhatsApp-Konto;
- keine WhatsApp-Gruppen, Calls, Broadcasts oder automatisches Outreach;
- Full-History-Sync bleibt deaktiviert; Rehydration ist bewusst klein und best effort;
- Smartphone-Keyboard, Viewport und reale Touch-Gesten bleiben Teil der manuellen Geräteabnahme.
