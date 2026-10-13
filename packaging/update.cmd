@echo off
setlocal
set "ROOT=%~dp0"

if not exist "%ROOT%current" (
  echo Fehler: Portable Installation ist noch nicht eingerichtet. Bitte zuerst packaging\install.cmd ausfuehren. 1>&2
  exit /b 1
)

set /p CURRENT=<"%ROOT%current"
set "RELEASE=%ROOT%releases\%CURRENT%"
set "MANAGER=%RELEASE%\tools\portable_manager.py"
set "VENV_PYTHON=%RELEASE%\.venv\Scripts\python.exe"

if not exist "%MANAGER%" (
  echo Fehler: Aktiver Release %CURRENT% ist unvollstaendig: %MANAGER% fehlt. 1>&2
  exit /b 1
)

if exist "%VENV_PYTHON%" (
  "%VENV_PYTHON%" "%MANAGER%" update --root "%ROOT%" %*
  exit /b %ERRORLEVEL%
)

where uv >nul 2>nul
if errorlevel 1 (
  echo Fehler: Die app-eigene Python-Runtime fehlt und uv ist nicht verfuegbar. 1>&2
  echo Bitte uv installieren oder die Portable-Installation erneut einrichten. 1>&2
  exit /b 1
)

set "UV_PYTHON_INSTALL_DIR=%ROOT%.runtime\python"
set "UV_PROJECT_ENVIRONMENT=%RELEASE%\.venv"
uv python install 3.12 >nul
if errorlevel 1 exit /b %ERRORLEVEL%
for /f "delims=" %%P in ('uv python find 3.12') do set "PYTHON=%%P"
uv run --locked --project "%RELEASE%" --python "%PYTHON%" python "%MANAGER%" update --root "%ROOT%" %*
exit /b %ERRORLEVEL%
