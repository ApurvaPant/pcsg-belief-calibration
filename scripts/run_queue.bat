@echo off
REM run_queue.bat - launch the PCSG priority queue on built-in Windows PowerShell 5.1.
REM Usage:
REM   scripts\run_queue.bat                 (60 personas, new run id)
REM   scripts\run_queue.bat 60              (60 personas)
REM   scripts\run_queue.bat 60 main_2026..  (resume that run id)
REM   scripts\run_queue.bat 60 "" 108 opt   (include optional stages 6-7)
setlocal
set "PERSONAS=%~1"
if "%PERSONAS%"=="" set "PERSONAS=60"
set "ARGS=-Personas %PERSONAS%"
if not "%~2"=="" set "ARGS=%ARGS% -RunId %~2"
if not "%~3"=="" set "ARGS=%ARGS% -Lme %~3"
if /I "%~4"=="opt" set "ARGS=%ARGS% -IncludeOptional"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_queue.ps1" %ARGS%
endlocal
