@echo off
setlocal
cd /d "%~dp0"
call "%~dp0START_API_AND_EMULATOR.cmd" %*
set "exit_code=%ERRORLEVEL%"
exit /b %exit_code%
