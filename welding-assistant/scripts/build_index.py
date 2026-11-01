"""Builds the search index (data/index/) from the documents folder. Run once after cloning, or after changing the documents.

    python scripts/build_index.py

Same stages the Library's Reindex button runs: passages and tables, then sections. Skips nothing; takes about a minute.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
(ROOT / "data" / "index").mkdir(parents=True, exist_ok=True)
for module in ("welding_ai.ingestion.ingest", "welding_ai.ingestion.sections"):
    print("running", module, flush=True)
    if subprocess.run([sys.executable, "-m", module], env=env, cwd=str(ROOT)).returncode:
        sys.exit(f"{module} failed")
print("index built in", ROOT / "data" / "index")
