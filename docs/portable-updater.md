# Portable Installation und Updater

Der Ladies Explorer kann als portable, release-basierte Installation betrieben werden. Programmcode, reproduzierbare Runtime und persistente Daten sind dabei getrennt:

```text
LadiesExplorer/
├── start.command / start.cmd
├── update.command / update.cmd
├── current
├── .runtime/
│   └── python/                 # von uv verwaltetes app-lokales Python 3.12
├── releases/
│   ├── v9.7.0/
│   │   └── .venv/             # releasebezogene Python-Umgebung
│   └── v9.7.1/
│       └── .venv/
└── data/
    ├── ladies.sqlite3
    ├── ladies_data/
    └── logs/
```

`releases/` enthält austauschbaren Programmcode und pro Release ein eigenes reproduzierbares `.venv`. `.runtime/python/` enthält das von `uv` verwaltete app-lokale Python und bleibt beim Wechsel zwischen Releases erhalten. `data/` enthält ausschließlich den permanenten Nutzerdatenbestand und wird von Updates nicht ersetzt.

Das System-Python von macOS oder Windows ist **nicht** die App-Runtime. Die Portable-Launcher verwenden ein vorhandenes Release-venv oder provisionieren über `uv` zuerst Python 3.12 unter `<root>/.runtime/python` und bauen daraus das Release-venv auf.

## Erstinstallation

GitHub-Releases stellen ein kanonisches Asset `ladies-explorer-vX.Y.Z.zip` bereit. Nach dem Entpacken kann die portable Installation angelegt werden.

Für die einmalige Runtime-Provisionierung wird `uv` verwendet. Dadurch muss insbesondere das von macOS mitgelieferte System-Python nicht verändert oder als App-Runtime verwendet werden.

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

Nicht aus einer Legacy-Installation übernommen werden reproduzierbare Runtime-Daten wie `.venv/`, `.runtime` oder `node_modules/`. Die Portable-Installation erzeugt ihre eigene Runtime neu.

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

Der Launcher löst die Python-Runtime in dieser Reihenfolge auf:

1. vorhandenes `<root>/releases/<current>/.venv` verwenden;
2. falls es fehlt, über `uv` ein app-lokales Python 3.12 unter `<root>/.runtime/python` provisionieren;
3. daraus das Release-venv synchronisieren;
4. erst danach den Portable Manager und `ladies_explorer.py` starten.

Dadurch kann beispielsweise ein macOS-System-Python 3.9 vorhanden sein, ohne dass es für die Anwendung benutzt wird.

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

Auch der Updater läuft über die app-eigene Runtime des aktuell aktiven Releases und nicht über das System-Python.

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
