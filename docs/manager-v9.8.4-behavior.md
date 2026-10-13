# Launcher Manager V9.8.4

Der Launcher Manager behandelt Start, Stop und Update als zentrale Lifecycle-Aktionen.

- Ein getrackter oder als Ladies Explorer erkannter Listener auf Port 8765 gilt als laufende App.
- Ein ungetrackter Ladies-Explorer-Listener wird in `.state/app.pid` übernommen.
- Stop sendet zuerst einen normalen Terminate-Impuls und wartet auf Prozessende **und** freien Port.
- Erst nach Timeout wird gezielt erzwungen beendet.
- Ein unbekannter/fremder Port-Besitzer wird nicht automatisch beendet.
- Update stoppt eine laufende App vollständig, erstellt automatisch einen DB-Sicherungspunkt, installiert und prüft den Release, bereitet die Runtime vor, migriert die DB und startet die App bei Bedarf wieder.
- Der laufende Manager-Prozess wird nach einem Update nicht neu geöffnet; die aktualisierten Manager-Dateien gelten beim nächsten Manager-Start.
- Release Notes werden in der Manager-UI in einem lokalen Inline-Dialog gerendert.
