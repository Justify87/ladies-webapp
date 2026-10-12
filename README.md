# Ladies Explorer V9.2

Ladies Explorer ist eine lokale, mobile-first Web-App zur personenzentrierten Verwaltung von Ladies-Anzeigen, Identity-Evidence, Historie, Medien, Listen, Preisen und Treffen. V9.2 erweitert die bestehende V9.1-Anwendung additiv um einen integrierten personenbezogenen WhatsApp-Chat über Baileys.

## Start

Die Anwendung hat weiterhin genau **einen** normalen Startpunkt:

```bash
pip install -r requirements_v9_1.txt
python ladies_explorer_v9_1.py
```

Danach öffnest du `http://127.0.0.1:8765`.

Für WhatsApp wird **Node.js 20 oder neuer** benötigt. Fehlt Node, startet Ladies Explorer trotzdem vollständig; nur **WhatsApp & Chat** ist nicht verfügbar. Die Worker-Abhängigkeiten werden intern aus dem committed `package-lock.json` reproduzierbar mit `npm ci` installiert. Es ist kein zweites Terminal und kein zweiter Server nötig.

## Smartphone im Heimnetz

Soll die lokale Web-App von einem Smartphone erreichbar sein, starte sie z. B. mit:

```bash
python ladies_explorer_v9_1.py --host 0.0.0.0
```

Der Launcher zeigt dann eine **Heimnetz-Pairing-URL** an. Diese URL wird auf dem Smartphone einmal geöffnet; danach speichert der Browser ein lokales `HttpOnly`-/`SameSite=Strict`-Cookie. Nicht gekoppelte LAN-Geräte erhalten keinen Zugriff auf `/api/*` oder `/archive/*`. Localhost auf dem Rechner bleibt ohne Pairing nutzbar.

Das lokale Pairing-Secret liegt im Data-Directory unter `ladies_v9_data/lan_access_token` und wird nicht ins Repository eingecheckt.

## WhatsApp einrichten

1. Ladies Explorer normal starten.
2. **Mehr → Einstellungen → WhatsApp & Chat** öffnen.
3. **WhatsApp verbinden** wählen.
4. In WhatsApp **Verknüpfte Geräte → Gerät hinzufügen** öffnen und den QR-Code scannen.
5. Bei späteren Starts wird eine vorhandene Session automatisch wieder verbunden.

Die WhatsApp-Session liegt ausschließlich lokal unter:

```text
ladies_v9_data/whatsapp/auth/
```

**Verbindung entfernen** invalidiert die WhatsApp-Session. Die lokale Ladies-Chat-Historie bleibt erhalten.

## Fachliches Chatmodell

WhatsApp ist nur der Transport. Die fachliche Identität bleibt die bestehende `Person`.

```text
Person
  ├─ Listings / Anzeigenhistorie
  ├─ PersonIdentifier / Identity-Evidence
  ├─ ContactEndpoint A (WhatsApp-Endpunkt)
  ├─ ContactEndpoint B (spätere Nummer/LID)
  └─ genau ein personenbezogener ChatThread
       └─ ChatMessage ...
```

Eine neue Telefonnummer erzeugt deshalb **keinen neuen Personenchat**, wenn die vorhandene V9-Identity-Logik sie derselben Person zuordnet. Alte Nachrichten behalten ihren ursprünglichen Endpunktbezug, solange Endpunkte nicht technisch als derselbe LID/PN-Kontakt zusammengeführt werden.

Unbekannte Nummern und LID-only-Kontakte werden in einem unassigned Thread gespeichert und gehen nicht verloren. Eine manuelle Zuordnung verschiebt ihre Nachrichten in den vorhandenen Person-Thread. Ist eine Telefonnummer bereits einer anderen Person zugeordnet, wird die widersprüchliche Zuordnung abgelehnt statt stillschweigend gelernt.

Wenn Baileys bei einem zunächst LID-only bekannten Kontakt später die Telefonnummer offenlegt, wird sie nur bei konsistenter Identity-Evidence übernommen und als `PersonIdentifier(PHONE)` gespiegelt. Konflikte erhalten den Zustand `IDENTITY_CONFLICT`.

## V9.2 Messaging-Architektur

```text
Ladies Python App
  ├─ Flask / bestehende V9 API
  ├─ SQLite / bestehende V9 Datenbank
  ├─ bestehende Identity-Logik
  ├─ MessagingService
  ├─ browser-sicherer SSE Event Bridge
  └─ BaileysWorkerManager
       └─ Node ESM Worker + @whiskeysockets/baileys
```

Der Worker kommuniziert ausschließlich über **JSON Lines auf stdin/stdout** mit Python. Er eröffnet keinen eigenen HTTP-Port und wird nicht direkt vom Browser angesprochen. `stdout` ist Maschinenprotokoll; Worker-Logs laufen über `stderr` in das vorhandene Ladies-Logging.

### Baileys

V9.2 pinnt exakt:

```text
@whiskeysockets/baileys = 7.0.0-rc14
```

Zusätzlich ist `package-lock.json` committed. CI und Runtime verwenden denselben deterministischen Installationspfad:

```text
npm ci --omit=dev --no-audit --no-fund
```

Der Worker ist Baileys-7/LID-aware: `@lid` bleibt Provider-Adresse, `remoteJidAlt`/PN ist Telefonnummer-Evidence und eine LID wird niemals als Telefonnummer gespeichert.

### Ausgehende Nachrichten

Eine Nachricht wird zuerst lokal als `PENDING` gespeichert. Der Worker korreliert den lokalen Message-Identifier mit der WhatsApp-Provider-ID. Eigene `messages.upsert`-Echos werden für lokal initiierte Sends unterdrückt; Nachrichten, die auf einem anderen verknüpften Gerät geschrieben wurden, werden weiterhin importiert.

Frühe `SENT`/`DELIVERED`/`READ`-Receipts werden über die lokale Message-ID zurückgeführt, sodass auch ungünstige Event-Reihenfolgen weder Duplikate erzeugen noch einen bereits höheren Status zurückstufen. **Erneut senden** verwendet dieselbe lokale Nachricht statt eine zweite Bubble anzulegen.

### Realtime / Datenschutzgrenze

Browser-Updates laufen per `EventSource` / Server-Sent Events. Der Browser erhält ausschließlich minimale Domain-Events, z. B.:

```text
message        -> thread_id + message_id
message_update -> thread_id + message_id + status
presence       -> thread_id + endpoint_id + state
qr_available   -> nur das Signal, dass ein QR verfügbar ist
```

Rohe Baileys-Daten wie JIDs, Telefonnummern, Nachrichtentexte, QR-Inhalte, IPC-Request-IDs oder Base64-Medien werden **nicht** durch den SSE-Bus gereicht. Den QR-Code ruft die gekoppelte UI gezielt über den QR-Endpunkt ab.

### Presence

Der optionale Schalter **Tippstatus senden** ist standardmäßig aktiv und wird lokal persistent gespeichert. Beim echten Tippen wird `composing` gesendet; nach ungefähr 2,5 Sekunden Inaktivität sowie vor dem Senden folgt `paused`.

Eingehende Presence wird serverseitig auf `endpoint_id` und `thread_id` gemappt. Ein Presence-Event kann dadurch nicht versehentlich als „tippt …“ im gerade geöffneten Chat einer anderen Person erscheinen.

### Historie und Pagination

Die lokale SQLite-Historie bleibt vollständig erhalten. Der Chat lädt die jüngsten Nachrichten zuerst und holt beim Hochscrollen ältere Nachrichten cursor-basiert nach. Die UI ist damit nicht mehr auf die ersten 150/200 Nachrichten eines Threads begrenzt.

### Medien

V9.2 zeigt eingehende Bilder an. Baileys lädt ein Bild im Worker, Python archiviert es unter:

```text
ladies_v9_data/whatsapp/media/
```

Das Base64-Transportpayload bleibt ausschließlich auf der internen Worker-Python-Grenze. In SQLite und Browser werden nur die lokalen Medienmetadaten bzw. die lokale URL verwendet. Videos/Audio werden als Nachrichtentyp erkannt, aber in V9.2 noch nicht vollständig als Medienverwaltung ausgebaut.

## Datenschutz und Logging

Nicht in normale Logs gehören:

- Nachrichtentexte;
- vollständige Telefonnummern;
- QR-Inhalte;
- Auth-Schlüssel oder Session-Credentials.

Worker-Diagnostik wird beim Übergang in das Python-Logging zusätzlich auf lange Ziffernfolgen maskiert. IPC-Antworten und rohe Transportereignisse bleiben intern.

Lokale Daten und installierte Worker-Pakete sind per `.gitignore` ausgeschlossen:

```text
ladies_v9_data/
ladies_v9_data/whatsapp/
ladies_v9/messaging/whatsapp_worker/node_modules/
```

## Entwicklerhinweise

Wichtige Dateien:

```text
ladies_v9/messaging/models.py              Messaging-Tabellen
ladies_v9/messaging/service.py             gemeinsame Chat-/Endpoint-Fachlogik
ladies_v9/messaging/service_v92.py         V9.2 Identity, Send, Events, Pagination
ladies_v9/messaging/integration.py         Person-Merge-Reconciliation
ladies_v9/messaging/web.py                 Flask Messaging API + SSE + QR
ladies_v9/messaging/whatsapp_manager.py    Node-Lifecycle + threadsicheres JSONL IPC
ladies_v9/messaging/whatsapp_worker/       Baileys-7-Worker + Lockfile
ladies_v9/static/messaging.js              integrierte mobile Chat-UI
ladies_v9/static/messaging.css             V9.x Messaging-Design
```

### Tests

Ohne echten WhatsApp-Account:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall ladies_v9 ladies_explorer_v9_1.py
node --check ladies_v9/messaging/whatsapp_worker/worker.mjs
node --check ladies_v9/static/messaging.js
node --check ladies_v9/static/messaging_bridge.js
```

Die Regressionstests decken insbesondere ab:

- bekannte und neue Nummern sowie LID-only-Kontakte;
- LID→PN-Lernen und Identity-Konflikte;
- Zusammenführung von LID- und PN-Endpunkten;
- Person-/Thread-Merges;
- idempotente Provider-Nachrichten;
- PENDING/SENT/FAILED sowie frühe READ-Receipts;
- Retry ohne zweite Nachricht;
- browser-sichere SSE-Events ohne JID/QR/Text/Base64-Leak;
- Presence-Zuordnung zum richtigen Thread;
- Cursor-Pagination;
- LAN-Pairing und Schutz von API/Archiv;
- Erhalt der lokalen Historie nach WhatsApp-Logout.

Automatisierte Tests benötigen keinen echten WhatsApp-Account. Eine reale QR-Kopplung, echte Nachrichten mit einem Testkonto und der Soft-Keyboard-Test auf einem Smartphone bleiben bewusst manuelle E2E-Schritte.

### Session zurücksetzen

Im normalen Betrieb immer **Mehr → Einstellungen → WhatsApp & Chat → Verbindung entfernen** verwenden. Für lokale Entwicklung kann bei beendeter App alternativ `ladies_v9_data/whatsapp/auth/` gelöscht werden. Die SQLite-Chat-Historie darf dafür nicht gelöscht werden.
