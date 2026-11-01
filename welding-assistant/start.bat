@echo off
rem Starts Weld Assist and opens it in the browser. Close this window to stop the server.
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
set PY=python
if exist "%~dp0..\.venv\Scripts\python.exe" set PY="%~dp0..\.venv\Scripts\python.exe"
rem The search index is not stored in git: build it from ..\KnowledgeBase on the first start (a few minutes).
if not exist "data\index\chunks.json" (
  echo Building the search index from the documents - first start only...
  %PY% -m welding_ai.ingestion.ingest || goto :fail
  %PY% -m welding_ai.ingestion.sections || goto :fail
  %PY% -m welding_ai.ingestion.faq_source
)
start "" http://127.0.0.1:8765/
%PY% apps\api\server.py
:fail
pause
