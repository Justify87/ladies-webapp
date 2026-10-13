@echo off
setlocal
set "ROOT=%~dp0"
set /p CURRENT=<"%ROOT%current"
python "%ROOT%releases\%CURRENT%\tools\portable_manager.py" start --root "%ROOT%" %*
exit /b %ERRORLEVEL%
