@echo off
setlocal
set "ROOT=%~dp0"

if not exist "%ROOT%current" (
  echo Fehler: Portable Installation ist noch nicht eingerichtet. Bitte zuerst packaging\install.cmd ausfuehren. 1>&2
  exit /b 1
)

set /p CURRENT=<"%ROOT%current"
set "RELEASE=%ROOT%releases\%CURRENT%"
set "STATE_DIR=%ROOT%.state"
set "MANAGER=%STATE_DIR%\launcher_manager.py"
set "VENV_PYTHON=%RELEASE%\.venv\Scripts\python.exe"

if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
for %%F in (launcher_manager.py launcher_core.py launcher_manager.html) do (
  if exist "%RELEASE%\tools\%%F" copy /Y "%RELEASE%\tools\%%F" "%STATE_DIR%\%%F" >nul
)

if not exist "%MANAGER%" (
  echo Fehler: Launcher Manager ist unvollstaendig. Bitte die Portable-Installation mit einem aktuellen Release reparieren. 1>&2
  exit /b 1
)
if not exist "%STATE_DIR%\launcher_core.py" (
  echo Fehler: Launcher Manager Core fehlt. 1>&2
  exit /b 1
)
if not exist "%STATE_DIR%\launcher_manager.html" (
  echo Fehler: Launcher Manager UI fehlt. 1>&2
  exit /b 1
)

if exist "%VENV_PYTHON%" (
  "%VENV_PYTHON%" "%MANAGER%" launch --root "%ROOT%" %*
  exit /b %ERRORLEVEL%
)

where uv >nul 2>nul
if errorlevel 1 (
  echo Fehler: Die app-eigene Python-Runtime fehlt und uv ist nicht verfuegbar. 1>&2
  echo Bitte uv installieren oder die Portable-Installation erneut einrichten. 1>&2
  exit /b 1
)

set "UV_PYTHON_INSTALL_DIR=%ROOT%.runtime\python"
uv python install 3.12 >nul
if errorlevel 1 exit /b %ERRORLEVEL%
for /f "delims=" %%P in ('uv python find 3.12') do set "PYTHON=%%P"
"%PYTHON%" "%MANAGER%" launch --root "%ROOT%" %*
exit /b %ERRORLEVEL%
