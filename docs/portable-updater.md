# Portable Installation und Launcher Manager

Der Ladies Explorer wird portabel und release-basiert betrieben. Programmcode, reproduzierbare Runtime und persistente Nutzerdaten bleiben voneinander getrennt. Für den Benutzer gibt es nach der Erstinstallation nur noch einen zentralen Einstieg: den lokalen Launcher Manager.

```text
LadiesExplorer/
├── start.command / start.cmd        # öffnet den Launcher Manager
├── update.command / update.cmd      # Kompatibilitätsalias zum selben Manager
├── current
├── .runtime/
│   └── python/                      # von uv verwaltetes app-lokales Python 3.12
├── .state/
│   ├── launcher_manager.py
│   ├── launcher_manager.html
│   ├── manager.json
│   ├── manager.log
│   ├── app.pid
│   ├── app-console.log
│   └── backups/
├── releases/
│   ├── v9.7.1/
│   │   └── .venv/
│   └── vX.Y.Z/
│       └── .venv/
└── data/
    ├── ladies.sqlite3
    ├── ladies_data/
    └── logs/
```

`releases/` enthält austauschbaren Programmcode und pro Release ein eigenes reproduzierbares `.venv`. `.runtime/python/` enthält das von `uv` verwaltete app-lokale Python. `.state/` enthält ausschließlich Manager-/Prozesszustand und Sicherungen. `data/` ist der permanente Nutzerdatenbestand und wird von Updates nicht ersetzt.

Das System-Python von macOS oder Windows ist **nicht** die App-Runtime.

## Erstinstallation

GitHub-Releases stellen das kanonische Asset `ladies-explorer-vX.Y.Z.zip` bereit. Nach dem Entpacken wird die Portable-Installation einmalig angelegt.

macOS/Linux:

```bash
./packaging/install.command
```

Windows:

```cmd
packaging\install.cmd
```

Ohne weitere Argumente wird neben dem entpackten Release ein Ordner `LadiesExplorer` erzeugt. Ein eigener Zielpfad ist möglich:

```bash
./packaging/install.command --root /pfad/zu/LadiesExplorer
```

Für die Runtime-Provisionierung wird `uv` verwendet. Das Betriebssystem-Python wird weder verändert noch als App-Runtime vorausgesetzt.

## Bestehende Daten einmalig übernehmen

Eine bisherige entpackte Installation kann bei der Erstinstallation als Legacy-Quelle angegeben werden:

```bash
./packaging/install.command --legacy-root /pfad/zur/alten/installation
```

Die alte Ladies-Explorer-App muss vollständig beendet sein. Interaktiv wird dies bestätigt; nicht-interaktiv kann nach bewusstem Beenden zusätzlich `--legacy-stopped` verwendet werden.

Übernommen werden, sofern vorhanden:

- `ladies.sqlite3` über die SQLite-Backup-API als konsistenter Snapshot;
- `ladies_data/` einschließlich WhatsApp-Auth, Medien, Recent-History und LAN-Gerätesessions;
- `logs/`.

WAL-/SHM-Dateien werden nicht roh kopiert. Reproduzierbare Runtime-Daten wie `.venv/`, `.runtime/` oder `node_modules/` werden nicht migriert. Die alte Installation wird nicht gelöscht.

## Zentraler Start

Nach der Installation wird nur noch der Launcher im Installationsroot verwendet:

```bash
./start.command
```

beziehungsweise unter Windows:

```cmd
start.cmd
```

Der Launcher startet **nicht direkt die Ladies-App**, sondern den lokalen Launcher Manager und öffnet dessen Weboberfläche automatisch im Standardbrowser.

Der Manager bindet ausschließlich an `127.0.0.1` und erhält pro Start ein zufälliges Zugriffstoken. Er ist damit nicht über das LAN erreichbar und besitzt keine Cloud-Komponente.

Ein erneuter Aufruf von `start.command` öffnet die bereits laufende Manager-Oberfläche wieder, statt eine zweite Manager-Instanz zu erzeugen.

## Manager-Oberfläche

Die Oberfläche ist bewusst klein und enthält nur die betrieblich relevanten Funktionen:

- installierte Version und App-Status;
- **Starten**;
- **Stoppen**;
- **App öffnen**;
- **Update prüfen**;
- **Aktualisieren**;
- Datenbank- und Release-Schema anzeigen;
- letzten Pre-Update-Backupstand anzeigen;
- Datenordner öffnen;
- aktuelle Manager-/App-Aktivität und Logauszüge anzeigen;
- Manager beenden.

### Starten

Beim Start der App löst der Manager die Runtime in dieser Reihenfolge auf:

1. vorhandenes `<root>/releases/<current>/.venv` verwenden;
2. falls es fehlt, über `uv` Python 3.12 unter `<root>/.runtime/python` provisionieren;
3. daraus das Release-venv reproduzierbar aus `pyproject.toml` und `uv.lock` synchronisieren;
4. `ladies_explorer.py` mit den permanenten Datenpfaden starten;
5. auf die Erreichbarkeit von `/api/config` warten;
6. erst nach erfolgreichem Start die Ladies-Explorer-Webseite im Standardbrowser öffnen.

Die App erhält immer dieselben persistenten Pfade:

```text
--db       <root>/data/ladies.sqlite3
--data-dir <root>/data/ladies_data
--log      <root>/data/logs/ladies_explorer.log
```

WhatsApp-Kopplung, Chats, Medien, Profile, Preise und LAN-Gerätesessions bleiben deshalb über Releases hinweg erhalten.

### Stoppen

Der Manager hält die PID der gestarteten App in `.state/app.pid`. Beim Stoppen wird die zugehörige Prozessgruppe sauber beendet. Falls der Prozess innerhalb des Stop-Timeouts nicht reagiert, erfolgt ein gezieltes hartes Beenden dieses App-Prozessbaums.

Der Manager selbst bleibt dabei geöffnet und kann die App anschließend wieder starten.

## Updates

Updates werden weiterhin ausschließlich manuell ausgelöst, jetzt aber über die Manager-Oberfläche.

`update.command` beziehungsweise `update.cmd` bleibt aus Kompatibilitätsgründen vorhanden, öffnet jedoch lediglich denselben Launcher Manager. Es existiert kein zweiter Update-Workflow mehr.

### Update prüfen

**Update prüfen** fragt nur den neuesten stabilen GitHub-Release ab. Eine laufende App wird dafür nicht gestoppt oder verändert.

### Aktualisieren

Beim eigentlichen Update orchestriert der Manager den kompletten Ablauf:

1. neuesten stabilen GitHub-Release bestimmen;
2. laufende Ladies-App automatisch stoppen;
3. konsistenten SQLite-Backupstand unter `.state/backups/` erzeugen;
4. Release-Asset `ladies-explorer-vX.Y.Z.zip` laden;
5. GitHub-SHA-256-Digest prüfen;
6. ZIP-Pfade und `release-manifest.json` validieren;
7. Release zunächst in einem Staging-Verzeichnis installieren;
8. `current` erst nach erfolgreicher Paketvalidierung atomar auf den neuen Release umschalten;
9. Python-Umgebung des neuen Releases synchronisieren;
10. Datenbankschema mit der kanonischen `Database.create_schema()`-Migration des neuen Releases aktualisieren;
11. bei Erfolg eine zuvor laufende App wieder starten;
12. nach erfolgreichem Neustart die App automatisch im Standardbrowser öffnen.

Die bestehende sichere Release-Logik aus `portable_manager.py` bleibt dabei für Download, Digestprüfung, ZIP-Härtung, Staging, Aktivierung und Release-Retention zuständig. Der Launcher Manager ergänzt darüber die Benutzerführung, Prozesssteuerung, Backups und Datenmigration.

### Fehlerfall und Recovery

Schlägt Download, Digestprüfung oder Paketvalidierung fehl, wird der neue Release nicht aktiviert.

Schlägt ein späterer Schritt nach Aktivierung fehl, insbesondere die Datenmigration:

- wird der Pre-Update-Datenbankstand aus `.state/backups/` wiederhergestellt;
- wird `current` auf die vorherige Version zurückgesetzt;
- wird eine zuvor laufende App nach Möglichkeit wieder mit der vorherigen Version gestartet.

Die letzten drei Pre-Update-Datenbankbackups werden aufbewahrt.

## Laufzeit- und Sicherheitsmodell

- Manager-Webserver: ausschließlich `127.0.0.1`, dynamischer Port;
- Manager-Zugriff: zufälliges Token pro Manager-Start;
- keine externe JavaScript-/CSS-Abhängigkeit der Manager-UI;
- `Cache-Control: no-store`, `X-Frame-Options: DENY`, restriktive CSP;
- Start/Stop/Update werden serialisiert; parallele Manager-Aktionen werden abgewiesen;
- App-PID und Manager-Zustand liegen nur unter `.state/`;
- App-Daten bleiben ausschließlich unter `data/`;
- System-Python wird nicht als App-Runtime verwendet.

## Rollback-Core

Der bestehende Portable Manager behält weiterhin die aktuelle und die unmittelbar vorherige Release-Version. Sein Rollback-Schutz verhindert das Zurückschalten auf einen Release, dessen unterstütztes DB-Schema älter als das vorhandene Datenbankschema ist.

Der Launcher Manager führt für normale Updates zusätzlich den oben beschriebenen Pre-Update-Backup-/Recovery-Pfad aus. Ein automatischer unkontrollierter Datenbank-Downgrade findet nicht statt.

## Release-Erzeugung

`tools/build_release.py` erstellt das kanonische Distributions-ZIP und erzeugt `release-manifest.json` mit App-Version, Tag, Commit, Entrypoint und DB-Schema-Version.

Das Paket muss neben dem bestehenden Portable Core auch enthalten:

- `tools/launcher_manager.py`;
- `tools/launcher_manager.html`;
- `packaging/start.command` / `start.cmd`;
- `packaging/update.command` / `update.cmd`.

Persistente Daten, lokale Runtime-Caches, Tests und CI-Dateien werden nicht ausgeliefert.

Der GitHub-Release-Workflow baut und validiert das Paket erst nach erfolgreicher Verifikation des exakten Release-Commits.
