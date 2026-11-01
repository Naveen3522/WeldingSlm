"""The choices in the Agents page dropdowns, read from the documents on every reindex (data/index/agent_options.json), so a new chapter or a new defect table shows up without a code change.

    options = build(chunks, tables)       -> {"processes": [...], "materials": [...], "problems": [...], "industries": [...]}
    save(options, path) / load(path)      the file the page reads (GET /api/agents/options); load() is None when there is none yet, and the page then keeps its built-in lists

  processes   the chapter titles that name a joining process (an alias in brackets, such as "GMAW / MIG / MAG", gives the short name a person would pick)
  materials   the chapters on weldability: "Weldability IV: Nickel Alloys, Titanium, Copper Alloys and Cast Iron" gives four materials
  problems    the defects named in the first column of the defect and symptom tables of at least two documents
  industries  the industry words that at least MIN_DOCS documents use
"""
import json
import re
from collections import defaultdict
from pathlib import Path

CHAPTER = re.compile(r"^\s*chapter\s+\d+\s*[^\w(]+\s*", re.I)
NOT_A_PROCESS = re.compile(r"\b(defects?|troubleshooting|selection|comparison|equipment|power|glossary|index|safety|qualification|digital|economics|standards|testing|quality|joint|positions|"
                           r"framework|taxonomy|classification|physics of fusion|special|environment|automated|robotic|orbital|additive|surfacing|contents|gaps)\b", re.I)
PROCESS_WORD = re.compile(r"\b(welding|brazing|soldering|cutting)\b", re.I)
NOT_A_PROBLEM = re.compile(r"^(category|likely causes?|diagnostic steps?|safety note|escalation|defect|symptom|cause|remedy)$", re.I)
INDUSTRIES = ["automotive", "aerospace", "pressure equipment", "structural steel", "rail", "shipbuilding", "pipeline", "nuclear", "construction", "offshore", "oil and gas", "power generation",
              "medical", "electronics", "appliances", "food and beverage"]
MIN_DOCS = 3
MIN_DOCS_FOR_A_PROBLEM = 2


def _title(chunks):
    first = {}
    for c in chunks:
        if c["source"] != "Added by hand" and not c.get("manual"):
            first.setdefault(c["source"], c["heading"].split(" > ")[0])
    return first


def _process_label(title):
    """'Gas Metal Arc Welding (GMAW / MIG / MAG)' -> 'MIG'; 'Shielded Metal Arc Welding (SMAW)' -> 'Shielded Metal Arc (SMAW)'; 'Friction Stir Welding' -> 'Friction Stir'."""
    title = re.sub(r"^Fundamentals and Physics of\s+", "", CHAPTER.sub("", title))
    aliases = [a.strip() for chunk in re.findall(r"\(([^)]*)\)", title) for a in re.split(r"[/,]", chunk) if a.strip()]
    name = re.sub(r"\s*\([^)]*\)", "", title).strip()
    short = re.sub(r"\s+(Welding|Brazing|Soldering)(\s+and\b.*)?$", "", name, flags=re.I).strip()
    if len(aliases) > 1:
        return min(aliases, key=len)
    if aliases:
        return f"{short} ({aliases[0]})"
    return short if short != name or PROCESS_WORD.search(name) else name


def processes(chunks):
    out = []
    for source, title in sorted(_title(chunks).items()):
        clean = CHAPTER.sub("", title)
        if PROCESS_WORD.search(clean) and not NOT_A_PROCESS.search(re.sub(r"\([^)]*\)", "", clean)):
            label = _process_label(title)
            if label and label.lower() not in {o.lower() for o in out}:
                out.append(label)
    return out


def materials(chunks):
    out = []
    for title in _title(chunks).values():
        m = re.search(r"weldability\s+[IVXLC\d]*\s*:\s*(.+)$", CHAPTER.sub("", title), re.I)
        if not m:
            continue
        text = m.group(1).strip()
        parts = [p.strip() for p in text.split(",")]
        if len(parts) > 1:
            parts = parts[:-1] + [x.strip() for x in re.split(r"\s+and\s+", parts[-1])]
        for p in parts:
            p = p.lower()
            if p and p not in out:
                out.append(p)
    return out


def problems(tables):
    found = defaultdict(set)
    for t in tables:
        if str(t["header"][0]).strip().lower() in ("defect", "symptom"):
            for row in t["rows"]:
                name = re.sub(r"\s*\([^)]*\)", "", str(row[0])).strip().lower()
                if name and len(name) <= 36 and not NOT_A_PROBLEM.match(name):
                    found[name].add(t["source"])
    ranked = sorted(((len(v), k) for k, v in found.items() if len(v) >= MIN_DOCS_FOR_A_PROBLEM), key=lambda x: (-x[0], x[1]))
    names = [k for _, k in ranked]
    return [k for k in names if not any(o != k and k.startswith(o + " ") for o in names)]          # "hydrogen cracking in the haz" is "hydrogen cracking"


def industries(chunks):
    docs = defaultdict(set)
    for c in chunks:
        if c["source"] == "Added by hand" or c.get("manual"):
            continue
        low = c["text"].lower()
        for word in INDUSTRIES:
            if re.search(r"\b" + re.escape(word) + r"\b", low):
                docs[word].add(c["source"])
    return [w for w in INDUSTRIES if len(docs[w]) >= MIN_DOCS]


def build(chunks, tables):
    return {"processes": processes(chunks), "materials": materials(chunks), "problems": problems(tables), "industries": industries(chunks)}


def save(options, path):
    Path(path).write_text(json.dumps(options, ensure_ascii=False, indent=1), encoding="utf-8")


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
