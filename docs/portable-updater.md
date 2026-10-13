# Portable Installation und Updater

Der Ladies Explorer kann als portable, release-basierte Installation betrieben werden. Programmcode und persistente Daten sind dabei getrennt:

```text
LadiesExplorer/
├── start.command / start.cmd
├── update.command / update.cmd
├── current
├── releases/
│   ├── v9.6.3/
│   └── v9.6.4/
└── data/
    ├── ladies.sqlite3
    ├── ladies_data/
    └── logs/
```

`releases/` enthält austauschbaren Programmcode. `data/` enthält den permanenten Datenbestand und wird von Updates nicht ersetzt.

## Erstinstallation

GitHub-Releases stellen ein kanonisches Asset `ladies-explorer-vX.Y.Z.zip` bereit. Nach dem Entpacken kann die portable Installation angelegt werden.

macOS/Linux:

```bash
./packaging/install.command
```

Windows:

```cmd
packaging\install.cmd
```

Ohne weitere Argumente wird neben dem entpackten Release ein Ordner `LadiesExplorer` erzeugt. Ein anderer Zielpfad ist möglich:

```bash
./packaging/install.command --root /pfad/zu/LadiesExplorer
```

## Bestehende lokale Daten übernehmen

Eine bisherige entpackte Installation kann einmalig als Legacy-Quelle angegeben werden:

```bash
./packaging/install.command --legacy-root /pfad/zur/alten/installation
```

Die alte Ladies-Explorer-App muss dafür vollständig beendet sein. Bei interaktiver Ausführung fordert der Installer dafür eine ausdrückliche Bestätigung an. Für bewusst nicht-interaktive Migrationen kann nach dem Beenden der Altinstallation zusätzlich `--legacy-stopped` angegeben werden.

Übernommen werden, sofern vorhanden:

- `ladies.sqlite3` über die SQLite-Backup-API als konsistenter Datenbank-Snapshot; WAL/SHM-Sidecars werden nicht roh kopiert;
- `ladies_data/` inklusive WhatsApp-Auth, WhatsApp-Medien, Recent-History und LAN-Gerätesessions;
- `logs/`.

Nicht übernommen werden reproduzierbare Runtime-Daten wie `.venv/`, `.runtime` oder `node_modules/`.

Die alte Installation wird nicht gelöscht.

## Start

Nach der Installation wird die aktive Version ausschließlich über die Launcher im Installationsroot gestartet:

```bash
./start.command
```

beziehungsweise unter Windows:

```cmd
start.cmd
```

Der Launcher übergibt der aktiven Release-Version immer dieselben persistenten Pfade:

```text
--db       <root>/data/ladies.sqlite3
--data-dir <root>/data/ladies_data
--log      <root>/data/logs/ladies_explorer.log
```

Dadurch bleiben insbesondere WhatsApp-Kopplung, Chats, Medien, Profile, Preise und LAN-Gerätesessions über Releases hinweg erhalten.

## Update

Updates werden bewusst nur manuell gestartet:

```bash
./update.command
```

Der Updater:

1. liest die aktive Version aus `current`;
2. fragt den neuesten stabilen GitHub-Release ab;
3. erwartet exakt `ladies-explorer-vX.Y.Z.zip`;
4. prüft den von GitHub gelieferten SHA-256-Digest;
5. validiert ZIP-Pfade und `release-manifest.json`;
6. entpackt zunächst nach `releases/.staging-vX.Y.Z`;
7. aktiviert den neuen Release erst nach vollständiger Validierung atomar über `current`;
8. behält den neuen Release und den unmittelbar zuvor aktiven Release für einen gezielten Rollback.

Wenn Download, Digest, ZIP oder Manifest fehlschlagen, bleibt `current` unverändert.

`start`, `update` und `rollback` teilen sich einen atomaren Lifecycle-Lock. Dadurch kann während eines laufenden Starts beziehungsweise einer laufenden App kein Update/Rollback beginnen und während eines Updates/Rollbacks kein paralleler Start dazwischenlaufen. Unter Windows prüft der Manager Prozesszustände read-only über die Windows-Prozess-API und sendet dabei kein Signal an den Prozess.

## Rollback

Über den Manager kann auf die vorherige installierte Version zurückgeschaltet werden:

```bash
python3 releases/<aktuell>/tools/portable_manager.py rollback --root .
```

Ein Rollback wird blockiert, wenn das vorhandene SQLite-Schema neuer ist als das Schema der Zielversion. Datenbank-Backups und eigentliche Schema-Migrationen bleiben Verantwortung der bestehenden `Database`-Schicht; der Updater führt keine eigenen SQL-Migrationen oder automatischen Daten-Rollbacks aus.

## Status

```bash
python3 releases/<aktuell>/tools/portable_manager.py status --root .
```

zeigt unter anderem aktive Version, persistente Pfade, DB-Schema-Version und gegebenenfalls die laufende PID.

## Release-Erzeugung

`tools/build_release.py` erstellt das kanonische Distributions-ZIP aus getrackten Runtime-Dateien und erzeugt darin `release-manifest.json` mit App-Version, Tag, Commit, Entrypoint und DB-Schema-Version. Persistente Daten, Tests, CI-Dateien und lokale Runtime-Caches werden nicht in das Paket aufgenommen.

Der GitHub-Release-Workflow baut und validiert dieses Paket erst nach erfolgreicher Verifikation des exakten Release-Commits und lädt es anschließend als Release-Asset hoch.
