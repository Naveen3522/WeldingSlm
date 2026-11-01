"""The .weldmodel file: one zip that carries a trained model to another computer, or the data to train one there.

    make_model(adapter_dir, name, ...)   -> bytes   the trained adapter (about 60 MB for the 1.2-billion-parameter model), its settings, and optionally its question pairs and check scores
    make_kit(name, pairs, ...)           -> bytes   no model: the question pairs and check scores only, for a computer that trains its own
    read(data)                           -> Package  checked: a readable manifest, only safe file names, a sane size
    Package.extract(dest)                writes the adapter files under dest

A model package holds a LoRA adapter, which only works with the base model it was trained on, so the manifest names that base; the other computer checks it (and that it has
room to run it) before anything is added. A kit has no such tie: its pairs train whatever base the other computer uses. Nothing in a file is run; it is only unzipped into
folders the app chooses.
"""
import io
import json
import time
import zipfile

FORMAT = 1
BASE_ID = "unsloth/llama-3.2-1b-instruct"                 # the base model the training pipeline is built on (welding_slm/pipeline/export.py)
BASE_NAME, BASE_PARAMS_B = "Llama 3.2 1B", 1.24
ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")
MAX_FILES, MAX_UNPACKED = 64, 2 * 1024 ** 3
README = ("A Weld Assist model file. Add it from Model management > Models > Add a model.\n"
          "kind: {kind}\nname: {name}\nbase: {base}\nmade: {made}\n")


class PackageError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    return buffer.getvalue()


def _manifest(kind, name, notes, gate, specs, pairs, clock):
    return {"format": FORMAT, "kind": kind, "name": name, "notes": notes, "made": clock(), "base": {"id": BASE_ID, "name": BASE_NAME, "params_b": BASE_PARAMS_B},
            "gate": gate or None, "made_on": specs or None, "pairs": len(pairs or [])}


def _pair_rows(pairs):
    keep = ("doc", "section", "question", "answer", "evidence", "kind")
    return [{k: p.get(k, "") for k in keep} for p in pairs]


def make_model(adapter_dir, name, notes="", gate=None, specs=None, pairs=None, clock=time.time):
    from pathlib import Path
    folder = Path(adapter_dir)
    missing = [f for f in ADAPTER_FILES if not (folder / f).is_file()]
    if missing:
        raise PackageError("This model has no trained adapter on this computer (" + ", ".join(missing) + " is missing), so it cannot be exported.")
    manifest = _manifest("model", name, notes, gate, specs, pairs, clock)
    files = [("manifest.json", json.dumps(manifest, indent=1)), ("README.txt", README.format(kind="model", name=name, base=BASE_NAME, made=time.strftime("%Y-%m-%d", time.localtime(manifest["made"]))))]
    files += [(f"adapter/{p.name}", p.read_bytes()) for p in sorted(folder.iterdir()) if p.is_file() and p.stat().st_size < 400 * 1024 ** 2]
    if pairs is not None:
        files.append(("pairs.json", json.dumps(_pair_rows(pairs), ensure_ascii=False)))
    return _zip(files)


def make_kit(name, pairs, notes="", gate=None, specs=None, clock=time.time):
    manifest = _manifest("kit", name, notes, gate, specs, pairs, clock)
    return _zip([("manifest.json", json.dumps(manifest, indent=1)),
                 ("README.txt", README.format(kind="training kit", name=name, base="any", made=time.strftime("%Y-%m-%d", time.localtime(manifest["made"])))),
                 ("pairs.json", json.dumps(_pair_rows(pairs), ensure_ascii=False))])


def _safe(name):
    parts = name.replace("\\", "/").split("/")
    return not (name.startswith("/") or ":" in parts[0] or any(p in ("", "..") for p in parts[:-1]) or parts[-1] in ("", ".."))


class Package:
    def __init__(self, data, manifest, names):
        self.data, self.manifest, self.names = data, manifest, names

    @property
    def kind(self):
        return self.manifest["kind"]

    def pairs(self):
        if "pairs.json" not in self.names:
            return []
        with zipfile.ZipFile(io.BytesIO(self.data)) as z:
            rows = json.loads(z.read("pairs.json").decode("utf-8"))
        return [r for r in rows if isinstance(r, dict) and r.get("question") and r.get("answer")]

    def extract(self, dest):
        """Writes the adapter files into `dest` (made if needed). Only files under adapter/ are written, whatever else the zip holds."""
        from pathlib import Path
        out = Path(dest)
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(self.data)) as z:
            for name in self.names:
                if name.startswith("adapter/") and not name.endswith("/"):
                    (out / name.split("/", 1)[1]).write_bytes(z.read(name))
        return out


def read(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("This is not a Weld Assist model file.")
    with z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise PackageError("This file is too large to be a Weld Assist model.")
        if not all(_safe(n) for n in names):
            raise PackageError("This file holds unsafe file names, so it was not opened.")
        if "manifest.json" not in names:
            raise PackageError("This is not a Weld Assist model file: it has no manifest.")
        try:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            assert manifest["format"] == FORMAT and manifest["kind"] in ("model", "kit") and manifest["base"]["id"] and str(manifest["name"]).strip()
        except (ValueError, KeyError, TypeError, AssertionError):
            raise PackageError("This file's manifest is not one this version of Weld Assist understands.")
        if manifest["kind"] == "model" and not all(f"adapter/{f}" in names for f in ADAPTER_FILES):
            raise PackageError("This model file has no trained adapter in it.")
        if manifest["kind"] == "kit" and "pairs.json" not in names:
            raise PackageError("This training kit has no question pairs in it.")
    return Package(data, manifest, names)
