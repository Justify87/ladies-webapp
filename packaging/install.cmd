@echo off
setlocal
set "SOURCE=%~dp0.."
set "MANAGER=%SOURCE%\tools\portable_manager.py"
set "VENV_PYTHON=%SOURCE%\.venv\Scripts\python.exe"

if exist "%VENV_PYTHON%" (
  "%VENV_PYTHON%" "%MANAGER%" install --source "%SOURCE%" %*
  exit /b %ERRORLEVEL%
)

where uv >nul 2>nul
if errorlevel 1 (
  echo Fehler: uv ist fuer die Portable-Erstinstallation erforderlich, damit eine app-eigene Python-Runtime eingerichtet werden kann. 1>&2
  exit /b 1
)

set "UV_PROJECT_ENVIRONMENT=%SOURCE%\.venv"
uv run --locked --project "%SOURCE%" --python 3.12 python "%MANAGER%" install --source "%SOURCE%" %*
exit /b %ERRORLEVEL%
