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

## Portable Installation und Updates

GitHub Releases veröffentlichen zusätzlich zum Source-Archiv ein kanonisches `ladies-explorer-vX.Y.Z.zip`. Dieses Paket kann einmalig als portable Installation eingerichtet werden. Danach liegen versionierter Programmcode und persistente Nutzerdaten getrennt:

```text
LadiesExplorer/
├── start.command / start.cmd
├── update.command / update.cmd
├── current
├── releases/
└── data/
    ├── ladies.sqlite3
    ├── ladies_data/
    └── logs/
```

`start.command` bzw. `start.cmd` startet immer die in `current` aktive Release-Version und übergibt dabei dieselben persistenten DB-, Daten- und Logpfade. Dadurch bleiben insbesondere WhatsApp-Auth, Chats, Medien, Profile, Preise und LAN-Gerätesessions bei einem App-Update erhalten.

`update.command` bzw. `update.cmd` prüft nur auf ausdrücklichen Aufruf den neuesten stabilen GitHub Release, validiert Release-Asset, SHA-256-Digest, ZIP-Pfade und Manifest, installiert zunächst in ein Staging-Verzeichnis und schaltet `current` erst danach atomar um. Die zwei neuesten Releases bleiben für einen Code-Rollback erhalten.

Eine bisherige entpackte Installation kann bei der Ersteinrichtung über `--legacy-root` übernommen werden. `.venv`, `.runtime` und `node_modules` werden bewusst nicht migriert; sie sind reproduzierbare Runtime-Daten.

Details: [`docs/portable-updater.md`](docs/portable-updater.md).

## Lokale Dateien

Beim direkten, nicht-portablen Start standardmäßig:

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

Die Anwendung bleibt bewusst ein kompakter lokaler Monolith: Flask + SQLAlchemy + SQLite im Backend, Browser-ES-Module ohne Frontend-Framework und ein separater interner Baileys-Worker für WhatsApp.

```text
ladies_explorer.py                 Start, Python-/Node-Bootstrap
pyproject.toml                     Version + Python-Abhängigkeiten
ladies/
  app.py                           Application Composition
  web.py                           Core-HTTP/API
  database.py                      SQLite/SQLAlchemy + Migration
  config.py                        öffentliche Frontend-Konfiguration
  errors.py                        einheitliche API-Fehler
  request_parsing.py               Request-Validierung
  logging_utils.py                 Log-Redaction
  background.py                    begrenzte In-Process-Hintergrundarbeit
  core/
    repositories/                  fachliche Repository-/Readmodel-Grenzen
    services/                      Search/Profile-Orchestrierung
  messaging/
    service.py                     kanonischer MessagingService
    routes.py                      Messaging-HTTP/API
    gateway.py                     Python ↔ Baileys Worker/IPC
    media_store.py                 sichere WhatsApp-Medienablage
    history_cache.py               begrenzte Recent-History-Rehydration
    whatsapp_worker/               Baileys-7-Worker
  pricing/
    service.py                     Price Intelligence / Revisionen
    extraction_core.py             Extraktionslogik
    media_locator.py               sicherer lokaler Medienzugriff
  static/
    app.js                         kanonischer Browser-Core / öffentliche Modulverträge
    messaging.js                   Messaging-UI
    profile_messaging_ui.js        Profil↔Chat-Integration
    pricing.js                     Pricing-UI
    js/chat_profile.js             Chat↔Profil-Integration
    css/                           statische Feature-Styles
  templates/index.html             HTML + ES-Module-Entrypoints
tests/                             Unit-/Regression-/Integrationstests
```

Core, Messaging und Pricing teilen sich innerhalb einer App dieselbe `Database`-Instanz. Schemaerstellung und Migration erfolgen zentral vor Nutzung der Feature-Services.

Im Browser gibt es keinen `window.LadiesApp`-Runtime-Layer, keine Hook-Setter-Pipeline und keinen `window.fetch`-Monkeypatch. Feature-Skripte werden als `type="module"` geladen und importieren ihre benötigten Verträge explizit aus `app.js`. Messaging veröffentlicht für die Pricing-Integration kleine DOM-Lifecycle-Events für geöffnete, aktualisierte und geschlossene Threads. Styles werden als statische CSS-Assets geladen und nicht aus JavaScript injiziert.

## Identität

`Person` bleibt die kanonische fachliche Identität. Eine Person ist weder eine einzelne Anzeige noch eine Telefonnummer oder WhatsApp-JID/LID.

Zu einer Person können gehören:

- mehrere/erneuerte Ladies-Anzeigen;
- mehrere Telefonnummern;
- WhatsApp-PN-/LID-Endpunkte;
- Bild-/Telefon-/Source-ID-Evidenz;
- Historie, Medien, Preise und Treffen.

Schreibende Identity-/Person-Operationen liegen im `PersonRepository`; größere Person-/Listing-Readmodels sind bewusst in einer separaten Read-Grenze gehalten. Merge/Split und Ingest erzeugen dadurch keine zweite fachliche Person-Implementierung.

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

Ein akzeptierter Vorschlag wird innerhalb einer zusammenhängenden Datenbanktransaktion geprüft und persistiert. Medien werden ausschließlich über den `MediaLocator` aufgelöst. Die Browser-Integration beobachtet den Messaging-Lifecycle explizit und verändert weder `fetch` noch globale App-Funktionen.

## Suche und Filter

Search- und Profile-Orchestrierung liegen außerhalb der Flask-Routen in Application Services. Der Browser erhält Kategorien, Filterkatalog, Defaults und App-Version aus der zentralen Backend-Konfiguration (`/api/config`) statt dieselben Filterdefinitionen mehrfach zu pflegen.

Die ehemalige Search-/Filter-Patchschicht ist entfernt. Suche, Preview und Filterdarstellung gehören zur kanonischen Browserimplementierung; es gibt keine nachgeladenen `runtime.js`, `search_ui.js` oder `filter_ui.js`-Overrides mehr.

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

GitHub Actions bündelt die Verifikation in einem Linux-Job: Python-Compile, vollständige Unit-/Regressionstests inklusive App-/Flask-Verträge, Portable-Release-Build/ZIP-Prüfung, Bootstrap-Selbstheilung in einer frischen Virtualenv, sämtliche Browser-JavaScript-Dateien im ES-Module-Modus, den Node-Worker, reproduzierbares `npm ci` und die erwartete Baileys-Version. Veraltete Läufe desselben PRs werden automatisch abgebrochen; reine Doku- und Release-Note-Änderungen lösen keinen separaten CI-Lauf aus.

Die Tests sollen öffentliche API-/Service-/Sicherheitsverträge absichern. Reine Source-String-Checks werden nur für kleine Architektur-Invarianten wie das Fehlen einer Transitional Runtime oder eines globalen Monkeypatches verwendet.

## Bewusste Grenzen

- reale WhatsApp-QR-Kopplung und echte E2E-Nachrichten benötigen einen manuellen Test mit einem realen WhatsApp-Konto;
- keine WhatsApp-Gruppen, Calls, Broadcasts oder automatisches Outreach;
- Full-History-Sync bleibt deaktiviert; Rehydration ist bewusst klein und best effort;
- Smartphone-Keyboard, Viewport und reale Touch-Gesten bleiben Teil der manuellen Geräteabnahme.
