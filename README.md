# Ladies Explorer V9.2

Ladies Explorer ist eine lokale, mobile-first Web-App zur personenzentrierten Verwaltung von Ladies-Anzeigen, Identity-Evidence, Historie, Medien, Listen, Preisen und Treffen. V9.2 erweitert die bestehende V9.1-Anwendung um einen integrierten personenbezogenen WhatsApp-Chat über Baileys.

## Start

Die Anwendung hat weiterhin genau **einen** normalen Startpunkt:

```bash
pip install -r requirements_v9_1.txt
python ladies_explorer_v9_1.py
```

Danach öffnest du `http://127.0.0.1:8765`.

Für die WhatsApp-Funktion wird **Node.js 20 oder neuer** benötigt. Fehlt Node, startet Ladies Explorer trotzdem vollständig; nur unter **WhatsApp & Chat** wird die Komponente als nicht verfügbar angezeigt. Die Node-Abhängigkeiten des internen Workers werden beim ersten tatsächlichen WhatsApp-Start automatisch installiert. Es ist kein zweites Terminal und kein zweiter Server nötig.

## WhatsApp einrichten

1. Ladies Explorer normal starten.
2. **Mehr → Einstellungen → WhatsApp & Chat** öffnen.
3. **WhatsApp verbinden** wählen.
4. In WhatsApp **Verknüpfte Geräte → Gerät hinzufügen** öffnen und den QR-Code scannen.
5. Fertig. Bei späteren Ladies-Starts wird eine vorhandene Session automatisch wieder verbunden.

Die WhatsApp-Session liegt ausschließlich im lokalen Data-Directory unter:

```text
ladies_v9_data/whatsapp/auth/
```

**Verbindung entfernen** löscht bzw. invalidiert nur die WhatsApp-Session. Die lokale Ladies-Chat-Historie bleibt erhalten.

## Fachliches Chatmodell

WhatsApp ist nur der Transport. Die fachliche Identität bleibt die bestehende `Person`.

```text
Person
  ├─ Listings / Anzeigenhistorie
  ├─ PersonIdentifier / Identity-Evidence
  ├─ ContactEndpoint A (WhatsApp-Nummer)
  ├─ ContactEndpoint B (spätere Nummer)
  └─ genau ein personenbezogener ChatThread
       └─ ChatMessage ...
```

Eine neue Telefonnummer erzeugt deshalb **keinen neuen Personenchat**, wenn die vorhandene V9-Identity-Logik die neue Anzeige derselben Person zuordnet. Alte Nachrichten behalten ihren ursprünglichen `endpoint_id`.

Unbekannte eingehende Nummern werden in einem unassigned Thread gespeichert und gehen nicht verloren. Die Zuordnung zu einer Person verschiebt die Nachrichten in deren vorhandenen Thread, ohne sie zu duplizieren.

## V9.2 Messaging-Architektur

```text
Ladies Python App
  ├─ Flask / bestehende V9 API
  ├─ SQLite / bestehende V9 Datenbank
  ├─ bestehende Identity-Logik
  ├─ MessagingService
  ├─ SSE Event Bridge
  └─ BaileysWorkerManager
       └─ Node ESM Worker + @whiskeysockets/baileys
```

Der Worker kommuniziert ausschließlich über **JSON Lines auf stdin/stdout** mit Python. Er eröffnet keinen eigenen HTTP-Port und wird nicht direkt vom Browser angesprochen. `stdout` ist Maschinenprotokoll; Worker-Logs laufen über `stderr` in das vorhandene Ladies-Logging.

### Baileys

V9.2 pinnt:

```text
@whiskeysockets/baileys = 7.0.0-rc14
```

Das ist die aktuellste veröffentlichte Baileys-7-Version zum Stand der V9.2-Implementierung. Die Integration nutzt bewusst keinen WhatsApp-History-Import. Ladies speichert neue ein- und ausgehende Nachrichten ab der Kopplung selbst.

### Realtime

Browser-Updates laufen per `EventSource` / Server-Sent Events. Neue Nachrichten, Statusänderungen, QR-/Connection-State, Presence und Unread-Änderungen benötigen deshalb kein permanentes Polling.

### Typing Presence

Der optionale Schalter **Tippstatus senden** ist standardmäßig aktiv und wird lokal persistent gespeichert. Beim echten Tippen wird `composing` gesendet; nach ungefähr 2,5 Sekunden Inaktivität sowie vor dem Senden folgt `paused`.

### Medien

V9.2 zeigt eingehende Bilder an. Baileys lädt das Bild im Worker, Python archiviert es unter:

```text
ladies_v9_data/whatsapp/media/
```

Die Nachricht speichert nur lokale Medienmetadaten und die lokale URL. Videos/Audio werden in der ersten Messaging-Ausbaustufe als Nachrichtentyp erkannt, aber nicht vollständig als Medienverwaltung ausgebaut.

## Datenschutz und Logging

Nicht in normale Logs gehören:

- Nachrichtentexte;
- vollständige Telefonnummern;
- QR-Inhalte;
- Auth-Schlüssel oder Session-Credentials.

Worker-Diagnostik wird beim Übergang in das Python-Logging zusätzlich auf lange Ziffernfolgen maskiert.

Die folgenden lokalen Daten sind per `.gitignore` ausgeschlossen:

```text
ladies_v9_data/
ladies_v9_data/whatsapp/
ladies_v9/messaging/whatsapp_worker/node_modules/
```

## Entwicklerhinweise

Wichtige Dateien:

```text
ladies_v9/messaging/models.py              Messaging-Tabellen
ladies_v9/messaging/service.py             lokale Chat-/Endpoint-Fachlogik
ladies_v9/messaging/service_v92.py         Session-Lifecycle, Medien, Settings
ladies_v9/messaging/integration.py         Person-Merge-Reconciliation
ladies_v9/messaging/web.py                 Flask Messaging API + SSE + QR
ladies_v9/messaging/whatsapp_manager.py    Node-Lifecycle + JSONL IPC
ladies_v9/messaging/whatsapp_worker/       Baileys-7-Worker
ladies_v9/static/messaging.js              integrierte mobile Chat-UI
ladies_v9/static/messaging.css             V9.x Messaging-Design
```

### Tests

Ohne echten WhatsApp-Account:

```bash
python -m unittest discover -s tests -p 'test_*.py'
python -m compileall ladies_v9 ladies_explorer_v9_1.py
node --check ladies_v9/messaging/whatsapp_worker/worker.mjs
```

Die Messaging-Tests verwenden einen Fake-Worker und prüfen insbesondere bekannte/neue Nummern, unassigned Zuordnung, Thread-Merge, Idempotenz, Sendestatus, Person-Merge und den Erhalt lokaler Historie nach Logout.

### Session zurücksetzen

Im normalen Betrieb immer **Mehr → Einstellungen → WhatsApp & Chat → Verbindung entfernen** verwenden. Für lokale Entwicklung kann bei beendeter App alternativ `ladies_v9_data/whatsapp/auth/` gelöscht werden. Die SQLite-Chat-Historie darf dafür nicht gelöscht werden.
