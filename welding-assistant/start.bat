@echo off
rem Starts Weld Assist and opens it in the browser. Close this window to stop the server.
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
set PY=python
if exist "%~dp0..\.venv\Scripts\python.exe" set PY="%~dp0..\.venv\Scripts\python.exe"
start "" http://127.0.0.1:8765/
%PY% apps\api\server.py
pause
