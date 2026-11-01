"""How long a retrain will take, from what earlier runs measured (a default from the first full training until there is a measurement).

    estimate(rows, mode, history) -> {"seconds", "parts"}      human(seconds) -> "6 h 20 min"      History.record(...) after each run
"""
import json
import math
import tempfile
import os
from pathlib import Path

GRAD_ACCUM = 8                         # the recipe's batch: one optimizer step is 8 rows
DEFAULT_SEC_PER_STEP = 60.0            # the first training on this GTX 1650 managed 38 to 65 s per step (a cold start measured 140 s: see TRAIN_STARTUP_S)
TRAIN_STARTUP_S = 300.0                # loading the training libraries and the base model, and compiling the GPU kernels, before the first step
DEFAULT_PREPARE_S = 120.0              # build the dataset, free the GPU, load the base model
DEFAULT_EXPORT_S = 600.0               # merge, convert to GGUF, register with Ollama, one test question
DEFAULT_GATE_S_PER_ROW = 6.0           # two models answer each row
GATE_ROWS = {"full": 120, "short": 120, "test": 12}
HISTORY_KEEP = 8


def steps_for(rows, epochs=1.0):
    return max(1, math.ceil(rows / GRAD_ACCUM * epochs))


def human(seconds):
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min"
    return f"{seconds} s"


def _median(values, default):
    values = sorted(v for v in values if v and v > 0)
    return values[len(values) // 2] if values else default


class History:
    """The measured speed of earlier runs (a small JSON file), so the next estimate is better than the first."""

    def __init__(self, path):
        self.path = Path(path)

    def _all(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def record(self, **run):
        runs = (self._all() + [run])[-HISTORY_KEEP:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(runs, fh)
        os.replace(temp, self.path)

    def measured(self):
        runs = self._all()
        return {k: _median([r.get(k) for r in runs], d) for k, d in (("sec_per_step", DEFAULT_SEC_PER_STEP), ("prepare_s", DEFAULT_PREPARE_S),
                                                                     ("export_s", DEFAULT_EXPORT_S), ("gate_s_per_row", DEFAULT_GATE_S_PER_ROW))}


def estimate(rows, mode, history=None):
    """-> {"seconds", "steps", "parts": {stage: seconds}, "measured": bool}"""
    m = history.measured() if history else {"sec_per_step": DEFAULT_SEC_PER_STEP, "prepare_s": DEFAULT_PREPARE_S, "export_s": DEFAULT_EXPORT_S,
                                           "gate_s_per_row": DEFAULT_GATE_S_PER_ROW}
    steps = steps_for(rows) if mode != "test" else 2
    parts = {"prepare": m["prepare_s"], "train": TRAIN_STARTUP_S + steps * m["sec_per_step"], "export": m["export_s"], "gate": GATE_ROWS[mode] * m["gate_s_per_row"]}
    return {"seconds": sum(parts.values()), "steps": steps, "parts": parts, "measured": bool(history and history._all())}
