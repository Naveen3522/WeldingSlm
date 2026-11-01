"""Rebuilds the search index from the documents folder, without touching the live index until the new one has been checked.

    passages and tables -> sections -> the verified FAQs (their chunk ids move when documents are added) -> check -> swap in (the old index is kept)

Each stage is the same command that built the current index, run on a scratch folder, so the result is exactly what a manual rebuild gives.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from welding_ai import paths

FILES = ("chunks.json", "tables.json", "sections.json", "faq.json")
SRC = Path(__file__).resolve().parents[2]
STAGES = [("Reading the documents", "welding_ai.ingestion.ingest"), ("Building the sections", "welding_ai.ingestion.sections"), ("Checking the verified FAQs", "welding_ai.ingestion.faq_source")]


class RebuildError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class Cancelled(RebuildError):
    pass


def _run(module, args, env, cancel):
    """Runs one stage; -> its output. Stops it early (and raises Cancelled) if `cancel` is set."""
    out = []
    with subprocess.Popen([sys.executable, "-m", module, *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(SRC.parent)) as proc:
        reader = threading.Thread(target=lambda: out.append(proc.stdout.read()), daemon=True)
        reader.start()
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                reader.join(5)
                raise Cancelled("Cancelled.")
            time.sleep(0.3)
        reader.join(5)
    text = (out[0] if out else b"").decode("utf-8", "replace")
    if proc.returncode:
        raise RebuildError(f"{module.rsplit('.', 1)[-1]} failed: " + (text.strip().splitlines() or ["no output"])[-1][:300])
    return text


def rebuild(docs_dir, index_dir=None, progress=lambda stage: None, cancel=None, faqs=True):
    """-> {"documents", "passages", "tables", "seconds", "warnings"}. Raises RebuildError (the live index is untouched) or Cancelled."""
    index_dir = Path(index_dir or paths.INDEX_DIR)
    t0 = time.time()
    scratch = Path(tempfile.mkdtemp(prefix="weld_index_", dir=str(index_dir.parent)))
    env = dict(os.environ, WELD_INDEX_DIR=str(scratch), WELD_DOCS_DIR=str(docs_dir), PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    warnings = []
    try:
        for label, module in STAGES:
            if module.endswith("faq_source") and not faqs:
                continue
            progress(label)
            text = _run(module, [str(docs_dir)] if module.endswith(".ingest") else [], env, cancel)
            warnings += [line.strip()[len("skipped:"):].strip() for line in text.splitlines() if line.strip().startswith("skipped:")]
            if module.endswith("faq_source") and "All quotes resolved" not in text:
                raise RebuildError("A verified FAQ no longer matches the documents, so the index was not changed: "
                                   + "; ".join(line.strip(" -") for line in text.splitlines() if line.startswith(" - "))[:400])

        progress("Checking the new index")
        chunks = json.loads((scratch / "chunks.json").read_text(encoding="utf-8"))
        if not chunks:
            raise RebuildError("The new index has no passages, so it was not used.")
        names = {c["source"] for c in chunks}
        missing = sorted(p.name for p in Path(docs_dir).glob("*.docx") if not p.name.startswith("~$") and p.name not in names)
        if missing:
            raise RebuildError("These documents produced no searchable text, so the index was not changed: " + ", ".join(missing))
        tables = json.loads((scratch / "tables.json").read_text(encoding="utf-8"))

        progress("Switching to the new index")
        keep = index_dir.parent / (index_dir.name + "_prev")
        shutil.rmtree(keep, ignore_errors=True)
        keep.mkdir(parents=True)
        for name in FILES:
            if (index_dir / name).exists():
                shutil.copy2(index_dir / name, keep / name)
            if (scratch / name).exists():
                os.replace(scratch / name, index_dir / name)
        return {"documents": len(names), "passages": len(chunks), "tables": len(tables), "seconds": round(time.time() - t0, 1), "warnings": warnings}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def rollback(index_dir=None):
    """Puts the previous index back (the one kept by the last rebuild). -> True if there was one."""
    index_dir = Path(index_dir or paths.INDEX_DIR)
    keep = index_dir.parent / (index_dir.name + "_prev")
    if not (keep / "chunks.json").exists():
        return False
    for name in FILES:
        if (keep / name).exists():
            shutil.copy2(keep / name, index_dir / name)
    return True
