"""What this computer can do: read its graphics card, memory, processor and free disk, and work out which models it can run and retrain.

    scan(folder)              -> {"gpu": {name, total_mb, free_mb} | None, "ram_gb", "ram_free_gb", "threads", "cpu", "disk_free_gb"}
    assess(specs, params_b)   -> {"verdict": fits | slow | too_large, "run_gb", "train_gb", "can_retrain", "lines": [{state, text}]}
    advice(specs)             -> one sentence: the largest model this computer runs and the largest it retrains

The sizes are estimates, not measurements: a model served as 8-bit needs about 0.6 GB + 1.1 GB per billion parameters; retraining it as 4-bit LoRA about 2 GB + 1 GB per billion
(the 1.2-billion-parameter model in use measures 1.3 GB served and trains in about 3 GB). Nothing here changes how a model is trained or served.
"""
import ctypes
import os
import platform
import shutil
import subprocess

RUN_BASE_GB, RUN_PER_B = 0.6, 1.1
TRAIN_BASE_GB, TRAIN_PER_B = 2.0, 1.0
GPU_HEADROOM = 0.92                 # share of the card's memory a model may fill: the rest is the display, the context and the system
RAM_HEADROOM = 0.7                  # share of the free memory a model may fill when it runs on the processor
DISK_NEEDED_GB = 10                 # what a retrain writes (dataset, adapter, merged weights, the converted model)


def run_gb(params_b):
    return RUN_BASE_GB + RUN_PER_B * params_b


def train_gb(params_b):
    return TRAIN_BASE_GB + TRAIN_PER_B * params_b


def _memory():
    """-> (total_gb, free_gb) of the main memory."""
    try:
        import psutil
        m = psutil.virtual_memory()
        return m.total / 1024 ** 3, m.available / 1024 ** 3
    except ImportError:
        pass
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                        ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong), ("virt_total", ctypes.c_ulonglong), ("virt_free", ctypes.c_ulonglong),
                        ("ext", ctypes.c_ulonglong)]
        s = Status()
        s.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        return s.total / 1024 ** 3, s.free / 1024 ** 3
    try:
        info = {k: int(v.split()[0]) for k, v in (line.split(":", 1) for line in open("/proc/meminfo"))}
        return info["MemTotal"] / 1024 ** 2, info.get("MemAvailable", info["MemFree"]) / 1024 ** 2
    except (OSError, KeyError, ValueError):
        return 0.0, 0.0


def _gpu(run=subprocess.run):
    try:
        out = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10, check=True).stdout
        name, total, free = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        return {"name": name, "total_mb": int(total), "free_mb": int(free)}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _cpu():
    name = platform.processor() or platform.machine()
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
        except OSError:
            pass
    return " ".join(name.split())


def scan(folder, run=subprocess.run):
    total, free = _memory()
    where = folder if os.path.exists(folder) else os.path.dirname(os.path.abspath(folder))
    return {"gpu": _gpu(run), "ram_gb": round(total, 1), "ram_free_gb": round(free, 1), "threads": os.cpu_count() or 1, "cpu": _cpu(),
            "disk_free_gb": round(shutil.disk_usage(where).free / 1024 ** 3, 1)}


def assess(specs, params_b):
    """Can a model of `params_b` billion parameters run here, and can it be retrained here?"""
    need_run, need_train = run_gb(params_b), train_gb(params_b)
    gpu = specs.get("gpu")
    card = gpu["total_mb"] / 1024 if gpu else 0.0
    ram_free = specs.get("ram_free_gb", 0.0)
    lines = []
    if gpu and need_run <= card * GPU_HEADROOM:
        verdict = "fits"
        lines.append({"state": "ok", "text": f"Needs about {need_run:.1f} GB of the {card:.0f} GB graphics memory, so it runs on the graphics card."})
    elif need_run <= ram_free * RAM_HEADROOM:
        verdict = "slow"
        why = f"Needs about {need_run:.1f} GB, more than the {card:.0f} GB graphics memory" if gpu else "There is no NVIDIA graphics card"
        lines.append({"state": "warn", "text": why + ", so it runs on the processor and answers slowly."})
    else:
        verdict = "too_large"
        lines.append({"state": "bad", "text": f"Needs about {need_run:.1f} GB, more than this computer can give it ({card:.0f} GB graphics memory, {ram_free:.0f} GB free memory)."})
    can_retrain = bool(gpu) and need_train <= card * GPU_HEADROOM
    if can_retrain:
        lines.append({"state": "ok", "text": f"Can be retrained here: needs about {need_train:.1f} GB of graphics memory."})
    else:
        lines.append({"state": "warn" if verdict != "too_large" else "bad",
                      "text": "Cannot be retrained here: " + (f"retraining needs about {need_train:.1f} GB of graphics memory." if gpu else "retraining needs an NVIDIA graphics card.")
                      + (" It can still be used as it is." if verdict != "too_large" else "")})
    if specs.get("disk_free_gb", 0) < DISK_NEEDED_GB:
        lines.append({"state": "bad" if verdict != "too_large" else "warn", "text": f"Only {specs['disk_free_gb']:.0f} GB of disk is free; about {DISK_NEEDED_GB} GB is needed."})
    return {"verdict": verdict, "run_gb": round(need_run, 1), "train_gb": round(need_train, 1), "can_retrain": can_retrain, "lines": lines,
            "usable": verdict != "too_large" and specs.get("disk_free_gb", 0) >= DISK_NEEDED_GB}


def largest(specs, retrain):
    """The largest model, in billions of parameters, that fits the graphics card (or, for running only, the processor's memory); 0 when none does."""
    gpu = specs.get("gpu")
    card = gpu["total_mb"] / 1024 if gpu else 0.0
    if retrain:
        return max(0.0, (card * GPU_HEADROOM - TRAIN_BASE_GB) / TRAIN_PER_B) if gpu else 0.0
    best = max(card * GPU_HEADROOM, specs.get("ram_free_gb", 0.0) * RAM_HEADROOM if not gpu else 0.0)
    return max(0.0, (best - RUN_BASE_GB) / RUN_PER_B)


def advice(specs):
    run, train = largest(specs, False), largest(specs, True)
    if run < 0.5:
        return "This computer cannot run a model of any useful size."
    where = "on the graphics card" if specs.get("gpu") and run else "on the processor"
    out = f"This computer runs models up to about {run:.0f} billion parameters {where}"
    return out + (f" and retrains up to about {train:.0f} billion." if train >= 0.5 else ", but cannot retrain one: that needs an NVIDIA graphics card with more memory.")
