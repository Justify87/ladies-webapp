@echo off
setlocal
set "SOURCE=%~dp0.."
python "%SOURCE%\tools\portable_manager.py" install --source "%SOURCE%" %*
exit /b %ERRORLEVEL%
