@echo off
setlocal
set "ROOT=%~dp0"
call "%ROOT%start.cmd" %*
exit /b %ERRORLEVEL%
