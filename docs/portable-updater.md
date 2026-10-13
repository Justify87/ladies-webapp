# Portable Installation und Local Web Launcher Manager

Der Ladies Explorer wird portabel und release-basiert betrieben. Für den Benutzer existiert genau **ein** Manager: der lokale Web Launcher Manager. Er übernimmt Start, Stop, Updates, Datenmigration, Recovery, Logs und das Öffnen der Anwendung im Standardbrowser.

`tools/portable_manager.py` existiert nicht mehr. Wiederverwendbare technische Primitiven für Installation, Release-Download, Digest-/ZIP-Prüfung, Lifecycle-Lock und Legacy-Datenübernahme liegen in `tools/launcher_core.py`. Dieses Modul besitzt keine eigene CLI und ist kein zweiter Manager.

```text
LadiesExplorer/
├── start.command / start.cmd
├── update.command / update.cmd      # Kompatibilitätsalias zum selben Manager
├── current
├── .runtime/
│   └── python/
├── .state/
│   ├── launcher_manager.py
│   ├── launcher_core.py
│   ├── launcher_manager.html
│   ├── manager.json
│   ├── manager.log
│   ├── app.pid
│   ├── app-console.log
│   └── backups/
├── releases/
│   ├── vX.Y.Z/
│   │   └── .venv/
│   └── ...
└── data/
    ├── ladies.sqlite3
    ├── ladies_data/
    └── logs/
```

`releases/` enthält austauschbaren Programmcode und pro Release ein eigenes reproduzierbares `.venv`. `.runtime/python/` enthält das von `uv` verwaltete app-lokale Python. `.state/` enthält Manager-/Prozesszustand und Sicherungen. `data/` enthält den permanenten Nutzerdatenbestand.

Das System-Python von macOS oder Windows ist **nicht** die App-Runtime.

## Erstinstallation

GitHub-Releases stellen das kanonische Asset `ladies-explorer-vX.Y.Z.zip` bereit.

macOS/Linux:

```bash
./packaging/install.command
```

Windows:

```cmd
packaging\install.cmd
```

Die Installer starten `launcher_manager.py install`. Wenn kein Release-venv vorhanden ist, wird Python 3.12 über `uv` provisioniert; das Betriebssystem-Python wird nicht als App-Runtime vorausgesetzt.

Ein eigener Zielpfad ist möglich:

```bash
./packaging/install.command --root /pfad/zu/LadiesExplorer
```

### Bestehende Daten einmalig übernehmen

```bash
./packaging/install.command --legacy-root /pfad/zur/alten/installation
```

Die alte App muss vollständig beendet sein. Übernommen werden, sofern vorhanden:

- `ladies.sqlite3` als konsistenter SQLite-Backup-Snapshot;
- `ladies_data/` einschließlich WhatsApp-Auth, Medien, Recent-History und LAN-Gerätesessions;
- `logs/`.

WAL-/SHM-Dateien werden nicht roh kopiert. Reproduzierbare Runtime-Daten wie `.venv/`, `.runtime/` oder `node_modules/` werden nicht migriert.

## Zentraler Start

```bash
./start.command
```

beziehungsweise:

```cmd
start.cmd
```

Der Root-Launcher spiegelt `launcher_manager.py`, `launcher_core.py` und `launcher_manager.html` gemeinsam aus dem aktiven Release nach `.state/`. Danach startet er ausschließlich den Local Web Launcher Manager.

Der Manager bindet nur an `127.0.0.1`, verwendet pro Start ein zufälliges Zugriffstoken und öffnet seine Oberfläche automatisch im Standardbrowser. Ein erneuter Launcher-Aufruf öffnet die bereits laufende Manager-Instanz, statt eine zweite zu erzeugen.

## Manager-Oberfläche

Die Oberfläche stellt nur die betrieblich relevanten Funktionen bereit:

- installierte Version und App-Status;
- **Starten** / **Stoppen** / **App öffnen**;
- **Update prüfen** / **Aktualisieren**;
- DB- und Release-Schema;
- letzten Pre-Update-Backupstand;
- Datenordner;
- Manager-/App-Aktivität und Logs;
- Manager beenden.

### App starten

Der Manager:

1. verwendet ein vorhandenes Release-venv oder provisioniert Python 3.12 über `uv`;
2. synchronisiert das Release-venv reproduzierbar aus `pyproject.toml` und `uv.lock`;
3. startet `ladies_explorer.py` mit den persistenten Pfaden;
4. wartet auf `/api/config`;
5. öffnet erst nach erfolgreicher Readiness die Anwendung im Standardbrowser.

```text
--db       <root>/data/ladies.sqlite3
--data-dir <root>/data/ladies_data
--log      <root>/data/logs/ladies_explorer.log
```

Bei einem Readiness-Timeout wird der gestartete Prozess wieder beendet, damit kein verwaister App-Prozess zurückbleibt.

### App stoppen

Die PID liegt unter `.state/app.pid`. Der Manager beendet die zugehörige Prozessgruppe zunächst sauber und erzwingt das Beenden nur nach Ablauf des Stop-Timeouts. Der Web-Manager selbst bleibt dabei verfügbar.

## Updates

`update.command` und `update.cmd` sind nur Kompatibilitäts-Entrypoints und öffnen denselben Web-Manager. Es existiert kein separater Updater und keine zweite Manager-CLI.

### Update prüfen

**Update prüfen** liest nur den neuesten stabilen GitHub-Release. Eine laufende App wird dabei nicht gestoppt oder verändert.

### Aktualisieren

Der kontrollierte Update-Ablauf ist:

1. neuesten stabilen Release bestimmen;
2. zuvor laufende App stoppen;
3. vorhandene SQLite-Datenbank konsistent nach `.state/backups/` sichern;
4. Release-Asset laden;
5. GitHub-SHA-256-Digest prüfen;
6. ZIP-Pfade und `release-manifest.json` validieren;
7. in ein Staging-Verzeichnis entpacken;
8. neuen Release atomar aktivieren und Root-Launcher aktualisieren;
9. Release-venv synchronisieren;
10. kanonische `Database.create_schema()`-Migration ausführen;
11. eine zuvor laufende App wieder starten und öffnen;
12. Manager, Core und UI nach `.state/` übernehmen;
13. den laufenden Web-Manager kontrolliert mit der neuen Manager-Version neu starten und die neue Manager-Oberfläche öffnen.

Die Release-Download-, Digest-, ZIP-, Manifest-, Staging- und Retention-Logik liegt als interne Core-Funktionalität in `launcher_core.py` und ist nur über den Web-Manager erreichbar.

### Fehlerfall und Recovery

Stop, Backup, Aktivierung, Runtime-Synchronisierung und Migration liegen im selben geschützten Ablauf. Scheitert ein Schritt, versucht der Manager **jeden Recovery-Schritt unabhängig**, sodass ein einzelner Recovery-Fehler spätere Rückrollschritte nicht blockiert:

1. eventuell noch laufende neue App stoppen;
2. vorhandenen Pre-Update-Datenbankstand wiederherstellen;
3. wenn vor dem Update noch keine DB existierte, eine während der fehlgeschlagenen Migration neu erzeugte DB samt WAL/SHM entfernen;
4. `current` auf den vorherigen Release zurücksetzen;
5. die Root-Launcher aus dem vorherigen Release wiederherstellen;
6. Manager, Core und UI des vorherigen Releases wieder nach `.state/` spiegeln;
7. eine zuvor laufende App nach Möglichkeit mit der vorherigen Version neu starten.

Fehlschläge einzelner Recovery-Schritte werden gesammelt und sichtbar gemeldet. Die letzten drei Pre-Update-Datenbankbackups werden aufbewahrt.

## Laufzeit- und Sicherheitsmodell

- Manager-Webserver ausschließlich auf `127.0.0.1` mit dynamischem Port;
- zufälliges Zugriffstoken pro Manager-Start;
- keine externe JavaScript-/CSS-Abhängigkeit;
- `Cache-Control: no-store`, `X-Frame-Options: DENY`, restriktive CSP;
- Start/Stop/Update werden serialisiert;
- parallele Manager-Aktionen werden abgewiesen;
- Prozess- und Managerzustand ausschließlich unter `.state/`;
- Nutzerdaten ausschließlich unter `data/`;
- System-Python wird nicht als App-Runtime verwendet.

## Release-Erzeugung

`tools/build_release.py` erstellt das kanonische ZIP und `release-manifest.json` mit App-Version, Tag, Commit, Entrypoint und DB-Schema-Version.

Ein gültiges Paket muss enthalten:

- `tools/launcher_manager.py`;
- `tools/launcher_core.py`;
- `tools/launcher_manager.html`;
- `packaging/install.command` / `install.cmd`;
- `packaging/start.command` / `start.cmd`;
- `packaging/update.command` / `update.cmd`.

`tools/portable_manager.py` ist ausdrücklich verboten und wird durch die Release-Validierung abgelehnt. Persistente Daten, lokale Runtime-Caches, Tests und CI-Dateien werden nicht ausgeliefert.
