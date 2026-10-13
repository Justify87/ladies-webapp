# Ladies Explorer

> Lokale, mobile-first Web-App zum Durchsuchen, Wiedererkennen und Verwalten von Ladies-Profilen – inklusive Historie, Medien, Preisen und integriertem WhatsApp-Chat.

**Aktuelle Version:** V9.8.0 · **Plattform:** macOS / Windows / Linux · **Betrieb:** lokal auf deinem eigenen Rechner

## Wofür ist das Projekt?

Ladies Explorer sammelt die wichtigsten Informationen zu einer Person an einer Stelle. Dazu gehören unter anderem:

- Profile und erneuerte Anzeigen
- Bilder und Medien
- Likes, Favoriten und Historie
- Treffen und Preise
- personenbezogene WhatsApp-Chats
- Suche und Filter

Die Anwendung läuft lokal. Deine Daten liegen in deiner Installation und werden bei Updates getrennt vom Programmcode aufbewahrt.

---

## Schnellstart für Einsteiger

### 1. Voraussetzung: `uv`

`uv` wird für die Ersteinrichtung benötigt. Python selbst musst du **nicht** passend vorkonfigurieren – der Launcher richtet die benötigte Python-3.12-Umgebung für die App ein.

macOS mit Homebrew:

```bash
brew install uv
```

Windows:

```powershell
winget install --id=astral-sh.uv -e
```

### 2. Release herunterladen

Lade unter **GitHub → Releases** die aktuelle Datei

```text
ladies-explorer-vX.Y.Z.zip
```

herunter und entpacke sie.

### 3. Einmalig installieren

Im entpackten Ordner:

**macOS / Linux**

```bash
./packaging/install.command
```

**Windows**

```cmd
packaging\install.cmd
```

Der Installer zeigt dir anschließend den fertigen Installationsordner an.

### 4. Ab jetzt nur noch den Launcher benutzen

Öffne im fertigen `LadiesExplorer`-Ordner:

**macOS / Linux**

```bash
./start.command
```

**Windows**

```cmd
start.cmd
```

Dadurch öffnet sich automatisch der **Ladies Explorer Manager** im Standardbrowser.

Im Manager kannst du:

- die App **starten** und **stoppen**
- die App im Browser **öffnen**
- nach **Updates suchen**
- Updates **installieren**
- Version, Status und Logs ansehen

Nach einem erfolgreichen Start öffnet sich Ladies Explorer automatisch im Browser.

---

## Updates

Updates bitte **immer über den Manager** ausführen.

Vor einem Update wird die Datenbank gesichert. Danach werden neue Programmversion, Python-Umgebung und nötige Datenbankmigrationen automatisch eingerichtet. Schlägt ein Update fehl, versucht der Manager auf die vorherige funktionierende Version zurückzugehen.

Du musst Releases also nicht von Hand überschreiben oder Dateien zwischen Versionen kopieren.

---

## Wo sind meine Daten?

Die portable Installation trennt Programm und persönliche Daten:

```text
LadiesExplorer/
├── start.command / start.cmd   ← hier starten
├── current                     ← aktive Version
├── releases/                   ← Programmversionen
├── .state/                     ← Manager, Logs, Backups
└── data/                       ← deine dauerhaften Daten
    ├── ladies.sqlite3
    ├── ladies_data/
    └── logs/
```

### Wichtig

Wenn du ein eigenes Backup machen möchtest, sichere vor allem den kompletten Ordner:

```text
data/
```

Dort liegen die Datenbank sowie archivierte Medien und weitere persistente App-Daten.

**Nicht manuell verändern:** `current`, `releases/`, `.state/`, `.runtime/` oder die enthaltenen virtuellen Python-Umgebungen. Der Manager verwaltet diese Bereiche selbst.

---

## Smartphone im Heimnetz

Die App startet so, dass sie auch von anderen Geräten im selben lokalen Netzwerk erreichbar sein kann. Beim ersten Zugriff eines neuen Geräts wird die dafür vorgesehene Pairing-/Session-Logik verwendet.

Der **Launcher Manager selbst** bleibt dagegen ausschließlich lokal auf dem Rechner unter `127.0.0.1` erreichbar.

---

## Wenn etwas nicht funktioniert

1. `start.command` bzw. `start.cmd` erneut öffnen.
2. Im Manager Status und Aktivitätsanzeige prüfen.
3. Bei Bedarf die Logs ansehen:
   - `.state/manager.log`
   - `.state/app-console.log`
   - `data/logs/ladies_explorer.log`
4. Bei Update-Problemen **nicht** selbst Dateien zwischen `releases/` verschieben.

Die ausführliche technische Beschreibung des Installations-, Update- und Recovery-Systems findest du in [`docs/portable-updater.md`](docs/portable-updater.md).

---

## Für Entwickler

Wer direkt aus dem Repository arbeitet, kann die Anwendung auch ohne portable Installation starten:

```bash
uv run python ladies_explorer.py --host 0.0.0.0
```

Tests:

```bash
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Die normale Nutzung sollte jedoch über den **portable Release + Launcher Manager** erfolgen.

---

## Hinweis

Ladies Explorer verarbeitet lokal gespeicherte Profil-, Medien- und Kommunikationsdaten. Verwende das Projekt verantwortungsvoll und beachte beim Umgang mit solchen Daten die für dich geltenden Datenschutz-, Plattform- und Nutzungsregeln.
