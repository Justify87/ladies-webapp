# Ladies Explorer

Lokale, mobile-first Web-App zum Durchsuchen, Wiedererkennen und Verwalten von Ladies-Profilen. Die Anwendung speichert Personen, Anzeigenhistorie, Medien, Likes/Favoriten und einen integrierten personenbezogenen WhatsApp-Chat lokal.

## Start

Mit `uv`:

```bash
uv run python ladies_explorer.py --host 0.0.0.0
```

Oder direkt mit Python:

```bash
python ladies_explorer.py --host 0.0.0.0
```

Der Start ist selbstheilend ausgelegt. Die Anwendung prüft die verwendete Python-Umgebung sowie Node.js/npm und richtet fehlende Projekt-Abhängigkeiten automatisch ein.

- `uv run` synchronisiert die Python-Abhängigkeiten aus `pyproject.toml`.
- Bei direktem Start innerhalb einer Virtualenv werden fehlende Pakete genau in diese Python-Umgebung installiert.
- Bei direktem Start mit einem System-Python wird das System nicht ungefragt verändert: vorhandenes `uv` wird benutzt, andernfalls wird lokal `.venv/` angelegt.
- Eine kompatible vorhandene Node.js-/npm-Installation wird verwendet.
- Fehlt Node.js oder ist die Version ungeeignet, wird eine projektlokale Node-Runtime unter `.runtime/` eingerichtet.
- Die WhatsApp-Abhängigkeiten werden reproduzierbar mit `npm ci` aus dem committed `package-lock.json` installiert und nur bei geändertem Lockfile erneut synchronisiert.

Für den Zugriff vom Smartphone im selben WLAN gibt der Start mit `--host 0.0.0.0` eine einmalige Pairing-URL aus. Diese zuerst auf dem Smartphone öffnen.

## Lokale Dateien

Standardmäßig verwendet die App versionsneutrale Pfade:

- Datenbank: `ladies.sqlite3`
- Archiv und Laufzeitdaten: `ladies_data/`
- Debug-Log: `logs/ladies_explorer.log`
- lokale Python-Umgebung, falls benötigt: `.venv/`
- lokale Node-Runtime, falls benötigt: `.runtime/`

Beim ersten Start nach der Umstellung werden bestehende Standarddaten automatisch übernommen:

- `ladies_v9.sqlite3` → `ladies.sqlite3`
- `ladies_v9_data/` → `ladies_data/`

Eigene Pfade über `--db` oder `--data-dir` bleiben möglich.

## Projektstruktur

```text
ladies_explorer.py            Startpunkt und Runtime-Bootstrap
pyproject.toml                Python-Projekt und Abhängigkeiten
requirements.txt              kompatible einfache Dependency-Liste
ladies/                       Anwendung
  app.py                      Flask-App-Integration
  web.py                      Kern-Web/API
  static/                     Frontend
  templates/                  HTML
  messaging/                  personenbezogener Chat
    service.py                Messaging-Basis
    service_runtime.py        WhatsApp-/Identity-Härtung
    whatsapp_manager.py       Node-Worker-Verwaltung
    whatsapp_worker/          Baileys-Worker + package-lock.json
tests/                        Unit- und Regressionstests
```

Versionsnummern gehören zur Release-/UI-Version und nicht mehr in Datei- oder Ordnernamen.

## Nachrichten

Der normale Tab **Nachrichten** ist bewusst eine persönliche Kontaktliste und keine automatische Liste aller bekannten Telefonnummern.

Eine bekannte Person erscheint dort nur, wenn:

1. mindestens eine eigene ausgehende Nachricht für diese Person existiert und
2. die Person Favorit oder Like ist.

Favoriten werden zuerst angezeigt, danach kontaktierte Likes. Ein bloßes Like/Favorisieren oder das automatische Erkennen einer Telefonnummer erzeugt keinen sichtbaren Eintrag im normalen Nachrichten-Tab.

Unbekannte eingehende WhatsApp-Kontakte werden weiterhin verlustfrei gespeichert und können intern einer Person zugeordnet werden; sie werden nicht mit der normalen persönlichen Nachrichtenliste vermischt.

## Profilkontakt

In der Profilübersicht gibt es nur noch die Hauptaktionen:

- **Chat öffnen** – öffnet den integrierten personenbezogenen Chat; die aktuelle Telefonnummer wird im Klartext direkt an der Aktion angezeigt.
- **Like**
- **Favorit**

Ein zusätzlicher Kontakt-/WhatsApp-Block am Ende des Profils wird nicht mehr sichtbar angezeigt. Der separate externe WhatsApp-Button entfällt.

## WhatsApp-Integration

WhatsApp läuft über einen internen Node-/Baileys-Worker. Der normale Startpunkt bleibt ausschließlich `ladies_explorer.py`; ein zweites Terminal oder ein zweiter HTTP-Server ist nicht nötig.

Direkte Baileys-Abhängigkeit:

```text
@whiskeysockets/baileys@7.0.0-rc14
```

Die Integration ist LID/PN-aware. `Person` bleibt die kanonische fachliche Identität; Telefonnummern/JIDs/LIDs sind Endpunkte bzw. Evidenz. Lokale Chat-Historie bleibt auch nach Logout erhalten.

## Sicherheit im Heimnetz

Localhost bleibt ohne zusätzliche Anmeldung nutzbar. Bei Bindung an `0.0.0.0` schützt die App `/api/*` und `/archive/*` für andere Geräte über ein lokales Pairing-Cookie. Das Pairing-Secret liegt ausschließlich im lokalen Datenverzeichnis.

## Tests

Lokal:

```bash
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub Actions prüft zusätzlich Python-Compile, Flask-Smoke-Test, Node-/Frontend-Syntax, reproduzierbares `npm ci` und die erwartete Baileys-Version.

## Bewusste Grenzen

- reale WhatsApp-QR-Kopplung und echte E2E-Nachrichten benötigen ein reales WhatsApp-Konto für den manuellen Test;
- Textversand ist integriert; eingehende Bilder werden lokal archiviert und angezeigt;
- kein WhatsApp-History-Import, keine Gruppen, Calls, Broadcasts oder automatisches Outreach;
- Smartphone-Keyboard/Viewport bleibt ein realer Gerätetest.
