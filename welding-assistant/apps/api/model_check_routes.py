"""Checks for the Model management page, kept apart from the training code (nothing here changes how a model is trained).

    GET  /api/admin/system-check          is this computer ready to train: GPU and its memory, free disk, Ollama, training libraries, pairs and check questions
    GET  /api/admin/qa?...                the pairs as retrain_routes gives them, each with `in_document` (found | changed | missing | "" when never checked);
                                          `document=found|changed|missing|unchecked` filters on that
    POST /api/admin/qa/check              compare every pair's answer with the current document text (a text comparison, no model); -> counts
    POST /api/admin/qa/decide             as retrain_routes, but a pair that is not found in its document cannot be approved
    POST /api/admin/qa/add  {question, answer, doc, area}   a pair written by hand: the answer must be in the document; kept as kind "manual"
    POST /api/admin/qa/edit {id, question, answer, area}    change a pair; its answer must still be in the document
    POST /api/admin/qa/only-found {on}    approve only pairs found in their document (on by default); GET /api/admin/qa/summary also says so
    POST /api/admin/qa/test               ask a fixed sample of approved pairs' questions and report the ones whose stored answer is not in the reply (nothing is changed)

Results of the check live in the app database (`qa_checks`), not in the pairs' own table.
"""
import importlib.util
import re
import shutil
import sqlite3
import subprocess
import json
import threading
import time
from urllib.parse import parse_qs, quote, urlparse

from welding_ai.accounts import levels
from welding_ai.training.queue import QaError, QaQueue

GPU_NEEDED_MB = 3000                 # a 1B model in 4-bit with its training state
DISK_NEEDED_GB = 10
LIBRARIES = ("torch", "unsloth", "trl", "transformers", "datasets")
SCHEMA = ("CREATE TABLE IF NOT EXISTS qa_checks (uid TEXT PRIMARY KEY, state TEXT NOT NULL, checked REAL NOT NULL);"
          "CREATE TABLE IF NOT EXISTS qa_areas (uid TEXT PRIMARY KEY, area TEXT NOT NULL)")
AREAS = ("General", "Fault Finder", "Procedure Advisor", "Formulas")
ONLY_FOUND = "only_found"            # approve only pairs that are found in their document (on unless an Admin turns it off)
TEST_SAMPLE = 30
STATES = ("found", "changed", "missing")
_SQUASH = re.compile(r"\s+")


def squash(text):
    return _SQUASH.sub(" ", text.replace("|", "/")).strip().lower()


class PairChecks:
    def __init__(self, db_path, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=15)
        self._db.executescript(SCHEMA)

    def states(self):
        with self._lock:
            return dict(self._db.execute("SELECT uid, state FROM qa_checks").fetchall())

    def save(self, results):
        with self._lock:
            now = self.clock()
            self._db.executemany("INSERT INTO qa_checks (uid, state, checked) VALUES (?, ?, ?) ON CONFLICT (uid) DO UPDATE SET state = excluded.state, checked = excluded.checked",
                                 [(uid, state, now) for uid, state in results.items()])
            self._db.commit()

    def areas(self):
        with self._lock:
            return dict(self._db.execute("SELECT uid, area FROM qa_areas").fetchall())

    def set_area(self, uid, area):
        with self._lock:
            self._db.execute("INSERT INTO qa_areas (uid, area) VALUES (?, ?) ON CONFLICT (uid) DO UPDATE SET area = excluded.area", (uid, area))
            self._db.commit()

    def last_checked(self):
        with self._lock:
            row = self._db.execute("SELECT MAX(checked) FROM qa_checks").fetchone()
        return row[0]


def document_texts(chunks):
    """-> {document: its text squashed and lower-cased} from the index's passages."""
    parts = {}
    for chunk in chunks:
        parts.setdefault(chunk["source"], []).append(chunk["text"])
    return {doc: squash(" ".join(texts)) for doc, texts in parts.items()}


def judge(pair, texts):
    """found: the answer is in the document's current text; changed: the document is there but no longer says it; missing: the document is gone."""
    if pair.get("refs"):                                              # highlights: each one must still be in its own document
        refs = json.loads(pair["refs"])
        if any(texts.get(r["doc"]) is None for r in refs):
            return "missing"
        return "found" if all(squash(r["text"]) in texts[r["doc"]] for r in refs) else "changed"
    text = texts.get(pair["doc"])
    if text is None:
        return "missing"
    if pair.get("kind") == "manual" and pair["doc"] == "Added by hand":          # written by hand: the answer is the writer's own words, so the passage it stands on is what is checked
        return "found" if squash(pair["evidence"]) in text else "changed"
    return "found" if squash(pair["answer"]) in text else "changed"


EXPORT_COLUMNS = ["Question", "Answer", "Documents", "Source", "Draft name", "Template", "Field", "Column", "Area", "Status", "Added by", "Goes to"]


def documents_cell(pair):
    """The documents a pair refers to in one cell: each document's name as a heading, then its quoted chunks. A pair made from a draft shows the draft's row, one 'column: value' per line."""
    if pair.get("draft"):
        return "\n".join(part.strip() for part in pair["evidence"].split("; ") if part.strip())
    refs = json.loads(pair["refs"]) if pair.get("refs") else ([{"doc": pair["doc"], "text": pair["answer"]}] if pair["doc"].endswith(".docx") else [])
    if not refs:
        return f"{pair['section']}\n  \u2022 {pair['evidence']}"
    by_doc = {}
    for ref in refs:
        by_doc.setdefault(ref["doc"], []).append(ref["text"])
    return "\n".join(f"{doc.replace('.docx', '').replace('_', ' ')}\n" + "\n".join(f"  \u2022 {t}" for t in texts) for doc, texts in by_doc.items())


def draft_cells(pair):
    """The draft cell a pair was made from, one cell each for the Excel file: [draft name, template, field, column] (empty for a pair that is not from a draft)."""
    try:
        d = json.loads(pair.get("draft") or "{}")
    except ValueError:
        d = {}
    return [d.get("name", ""), d.get("template", ""), d.get("field", ""), d.get("column", "")]


def export_row(pair, areas):
    manual = pair["kind"] in ("manual", "suggested")
    source = "Generated" if not manual else ("From a draft" if pair.get("draft") else "Manually referenced from document" if pair.get("refs") or pair["doc"].endswith(".docx") else "Manually added")
    return [pair["question"], pair["answer"], documents_cell(pair), source, *draft_cells(pair), areas.get(pair["uid"], "") or pair.get("area", ""), pair["status"].capitalize(),
            pair.get("added_by") or ("" if manual else "System"), {"training": "Training pairs", "learned": "Learned pairs", "test": "Test needed"}.get(QaQueue.goes_to(pair), "")]


def pair_from_documents(question, body, chunks):
    """A pair whose answer is highlighted in documents. `body["refs"]` is [{doc, text}] (or the older doc and answer). Each highlight must be in its document word for word.
    -> (the pair for the queue, None) or (None, why)."""
    refs = body.get("refs")
    if not refs and body.get("doc") and body.get("answer"):
        refs = [{"doc": body["doc"], "text": body["answer"]}]
    if not 5 <= len(question) <= 300:
        return None, "Write the question (5 to 300 characters)."
    if not isinstance(refs, list) or not refs:
        return None, "Highlight the answer in a document first."
    clean, evidence, first = [], "", None
    for ref in refs[:12]:
        doc, text = str(ref.get("doc", "")).strip(), str(ref.get("text", "")).strip()
        if not doc or not text:
            return None, "Each highlight needs its document and its text."
        found = next((c for c in chunks if c["source"] == doc and squash(text) in squash(c["text"])), None)
        if found is None:
            return None, f"That is not in the document {doc.replace('.docx', '')}, word for word. Select it in the document text."
        first = first or (doc, found)
        evidence = evidence or found["text"][:850]
        clean.append({"doc": doc, "text": text})
    answer = " ".join(r["text"] for r in clean)
    return {"doc": first[0], "section": first[1]["heading"].split(" > ")[-1], "question": question, "answer": answer, "evidence": evidence, "refs": clean}, None


def answered(expected, got):
    """True when the reply holds the approved answer: word for word, or (the model words it its own way) at least 70% of the answer's words and every number of it, with its
    unit, as often as the answer has it."""
    want, have = squash(expected), squash(got)
    if want in have:
        return True
    figure = re.compile(r"\d[\d.,]*(?:\s?[a-z%]+)?")
    need, got_figures = figure.findall(want), figure.findall(have)
    if any(need.count(x) > got_figures.count(x) for x in set(need)):
        return False
    words = [w for w in re.findall(r"[a-z0-9√.]+", want) if len(w) > 2 or any(ch.isdigit() for ch in w)]
    return bool(words) and sum(1 for w in words if w in have) / len(words) >= 0.7


def all_pairs(queue):
    offset, out = 0, []
    while True:
        page = queue.page(offset=offset, limit=200)
        out += page["rows"]
        offset += 200
        if offset >= page["total"]:
            return out


# ------------------------------------------------------------------ the system check
def _line(label, state, text):
    return {"label": label, "state": state, "text": text}


def gpu_lines(run=subprocess.run):
    try:
        out = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10, check=True).stdout
        name, total, free = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        total, free = int(total), int(free)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return [_line("Graphics card", "bad", "No NVIDIA graphics card was found, so a model cannot be trained here."),
                _line("Free graphics memory", "bad", "Not known.")]
    return [_line("Graphics card", "ok", f"{name}, {total / 1024:.0f} GB."),
            _line("Free graphics memory", "ok" if free >= GPU_NEEDED_MB else "warn", f"{free / 1024:.1f} GB free." + ("" if free >= GPU_NEEDED_MB else " Close other programs that use the card, or training may stop."))]


def system_check(retrain_dir, counts, check_questions, ollama_running, run=subprocess.run, which=importlib.util.find_spec):
    lines = gpu_lines(run)
    folder = retrain_dir if retrain_dir.exists() else retrain_dir.parent
    free = shutil.disk_usage(str(folder)).free / 1024 ** 3
    lines.append(_line("Free disk space", "ok" if free >= DISK_NEEDED_GB else "bad", f"{free:.0f} GB free." + ("" if free >= DISK_NEEDED_GB else f" Training needs about {DISK_NEEDED_GB} GB.")))
    lines.append(_line("Ollama", "ok" if ollama_running else "bad", "Running." if ollama_running else "Not running. Start Ollama first."))
    missing = [name for name in LIBRARIES if which(name) is None]
    lines.append(_line("Training libraries", "bad" if missing else "ok", ("Missing: " + ", ".join(missing) + ".") if missing else "Installed."))
    approved = counts.get("approved", 0)
    lines.append(_line("Question pairs", "ok" if approved else "bad", f"{approved} approved." if approved else "No approved pairs yet."))
    lines.append(_line("Check questions", "ok" if check_questions else "bad", f"{check_questions} held-out questions." if check_questions else "None found, so a new model cannot be checked."))
    return {"lines": lines, "ready": all(x["state"] != "bad" for x in lines)}


# ------------------------------------------------------------------ routes
def handle(handler, queue, checks, index_chunks, method, path, body, system, reply=lambda question: ""):
    """index_chunks() -> the index's passages now. system() -> (retrain_dir, check_question_count, ollama_running). reply(question) -> the answer text the app gives now."""
    if not (path.startswith("/api/admin/qa") or path == "/api/admin/system-check"):
        return False
    if not levels.allowed(handler.user["level"], "training_pairs"):
        handler.send_json({"error": "Weld Engineers and Admins only."}, 403)
        return True
    body = body or {}
    needs = {"/api/admin/qa/add": "pair_add", "/api/admin/qa/check": "pair_check", "/api/admin/qa/test": "pair_check"}.get(path) if method == "POST" else None
    if needs and not levels.allowed(handler.user["level"], needs):
        handler.send_json({"error": "You are not allowed to do that."}, 403)
        return True

    if path == "/api/admin/system-check" and method == "GET":
        retrain_dir, questions, running = system()
        handler.send_json(system_check(retrain_dir, queue.counts(), questions, running))
        return True

    if path == "/api/admin/qa/summary" and method == "GET":
        handler.send_json({**queue.counts(), "documents": queue.documents(), "only_found": queue.setting(ONLY_FOUND, "1") == "1", "areas": list(AREAS)})
        return True

    if path == "/api/admin/qa/only-found" and method == "POST":
        queue.set_setting(ONLY_FOUND, "1" if body.get("on") else "0")
        handler.send_json({"only_found": queue.setting(ONLY_FOUND, "1") == "1"})
        return True

    if path == "/api/admin/qa" and method == "GET":
        query = parse_qs(urlparse(handler.path).query)
        one = lambda key: (query.get(key) or [""])[0].strip()
        try:
            offset, limit = max(0, int(one("offset") or 0)), max(1, min(int(one("limit") or 50), 200))
        except ValueError:
            handler.send_json({"error": "offset and limit must be numbers."}, 400)
            return True
        states = checks.states()
        wanted = one("document")
        if wanted:                                    # filtering on the check: read every match first, then cut the page
            text = one("q").lower()
            rows = [r for r in all_pairs(queue) if one("status") in ("", r["status"]) and (not one("doc") or r["doc"] == one("doc")) and (not one("origin") or (r["kind"] in ("manual", "suggested")) == (one("origin") == "hand"))
                    and (not text or text in (r["question"] + " " + r["answer"]).lower()) and (states.get(r["uid"]) or "unchecked") == wanted]
            rows.sort(key=lambda r: -r["id"])
            page = {"rows": rows[offset:offset + limit], "total": len(rows)}
        else:
            page = queue.page(status=one("status"), doc=one("doc"), text=one("q"), offset=offset, limit=limit, origin=one("origin"), goes=one("goes"))
        areas = checks.areas()
        for row in page["rows"]:
            row["goes"] = queue.goes_to(row)
            row["in_document"] = states.get(row["uid"], "")
            row["area"] = areas.get(row["uid"], AREAS[0])
        handler.send_json(page)
        return True

    if path == "/api/admin/qa/check" and method == "POST":
        texts = document_texts(index_chunks())
        results = {p["uid"]: judge(p, texts) for p in all_pairs(queue)}
        checks.save(results)
        counts = {s: sum(1 for v in results.values() if v == s) for s in STATES}
        handler.send_json({**counts, "total": len(results)})
        return True

    if path == "/api/admin/qa/decide" and method == "POST" and body.get("status") == "approved":
        states = checks.states()
        try:
            ids = {int(i) for i in (body.get("ids") or [])}
        except (TypeError, ValueError):
            handler.send_json({"error": "Send ids or a document, and a status."}, 400)
            return True
        if body.get("doc"):                           # a whole document: approve only what is still found in it
            pool = [p for p in all_pairs(queue) if p["doc"] == body["doc"] and (p["status"] == "pending" or not body.get("only_pending"))]
        else:
            pool = [p for p in all_pairs(queue) if p["id"] in ids]
        strict = queue.setting(ONLY_FOUND, "1") == "1"
        blocked = {p["id"] for p in pool if strict and states.get(p["uid"]) in ("changed", "missing")}
        if blocked and not body.get("doc"):
            handler.send_json({"error": f"{len(blocked)} of these pairs are not found in the documents, so they cannot be approved."}, 409)
            return True
        try:
            changed = queue.decide([p["id"] for p in pool if p["id"] not in blocked], "approved", handler.user["username"])
        except QaError as e:
            handler.send_json({"error": e.message}, 400)
            return True
        handler.send_json({"changed": changed, "left_out": len(blocked), **queue.counts()})
        return True

    if path == "/api/admin/qa/export.xlsx" and method == "GET":
        from welding_ai.core import xlsx
        ids = {int(i) for i in (parse_qs(urlparse(handler.path).query).get("ids") or [""])[0].split(",") if i.isdigit()}
        query = parse_qs(urlparse(handler.path).query)
        one = lambda key: (query.get(key) or [""])[0].strip()
        text = one("q").lower()
        everything = all_pairs(queue)
        if ids:                                                           # the ticked pairs, in any state
            chosen = [p for p in everything if p["id"] in ids]
        else:                                                             # nothing ticked: the whole list as the page shows it (its status, origin and search)
            chosen = [p for p in everything if one("status") in ("", p["status"]) and (not one("doc") or p["doc"] == one("doc"))
                      and (not one("origin") or (p["kind"] in ("manual", "suggested")) == (one("origin") == "hand")) and (not text or text in (p["question"] + " " + p["answer"]).lower())]
        chosen.sort(key=lambda p: -p["id"])
        drafts = {draft_cells(p)[0] for p in chosen}
        name = f"{drafts.pop()} question-pairs.xlsx" if len(drafts) == 1 and "" not in drafts else "question-pairs.xlsx"        # pairs from one draft are saved under its name
        data = xlsx.write([EXPORT_COLUMNS] + [export_row(p, checks.areas()) for p in chosen], widths=[44, 50, 60, 22, 24, 22, 16, 16, 16, 12, 16, 16])
        handler.send_bytes(200, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})
        return True

    if path == "/api/admin/qa/add" and method == "POST":
        question, area = str(body.get("question", "")).strip(), str(body.get("area") or AREAS[0])
        if area not in AREAS and area != "Chat":
            handler.send_json({"error": "Choose one of: " + ", ".join(AREAS) + "."}, 400)
            return True
        pair, problem = pair_from_documents(question, body, index_chunks())
        if problem:
            handler.send_json({"error": problem}, 400)
            return True
        pair["kind"], pair["area"] = "manual", area
        doc, answer = pair["doc"], pair["answer"]
        made = queue.add_many([pair], user=handler.user["username"])
        if made["added"]:
            ids = [p["id"] for p in all_pairs(queue) if (p["doc"], p["question"], p["answer"]) == (doc, question, answer)]
            queue.decide(ids, "approved", handler.user["username"])                  # a person chose it from the document: no review needed
            mine = [p["uid"] for p in all_pairs(queue) if (p["doc"], p["question"], p["answer"]) == (doc, question, answer)]
            checks.save({uid: "found" for uid in mine})
            for uid in mine:
                checks.set_area(uid, area)
        handler.send_json({**made, **queue.counts()}, 200 if made["added"] else 409)
        return True

    if path == "/api/admin/qa/edit" and method == "POST":
        try:
            pair_id = int(body.get("id"))
        except (TypeError, ValueError):
            handler.send_json({"error": "Choose the pair to change."}, 400)
            return True
        pair = next((p for p in all_pairs(queue) if p["id"] == pair_id), None)
        question, answer = str(body.get("question", "")).strip(), str(body.get("answer", "")).strip()
        area = str(body.get("area") or checks.areas().get(pair["uid"] if pair else "", AREAS[0]))
        if pair is None:
            handler.send_json({"error": "That pair no longer exists."}, 404)
        elif not (question and answer):
            handler.send_json({"error": "Write the question and the answer."}, 400)
        elif area not in AREAS:
            handler.send_json({"error": "Choose one of: " + ", ".join(AREAS) + "."}, 400)
        else:
            found = next((c for c in index_chunks() if c["source"] == pair["doc"] and squash(answer) in squash(c["text"])), None)
            if found is None:
                handler.send_json({"error": "That answer is not in the document, word for word. Copy it from the document."}, 400)
            else:
                queue.edit(pair_id, question, answer, found["text"][:850])
                checks.save({pair["uid"]: "found"})
                checks.set_area(pair["uid"], area)
                handler.send_json({"ok": True})
        return True

    if path == "/api/admin/qa/test" and method == "POST":
        chosen = body.get("ids")
        if chosen is not None:                                                        # the ticked pairs
            try:
                approved = queue.approved(ids=[int(i) for i in chosen])[:100]
            except (TypeError, ValueError):
                handler.send_json({"error": "Send the ids of the pairs."}, 400)
                return True
            if not approved:
                handler.send_json({"error": "None of the ticked pairs is approved."}, 409)
                return True
        else:
            approved = sorted(queue.approved(), key=lambda p: p["uid"])[:TEST_SAMPLE]       # the same pairs each time, so two runs can be compared
        differ = []
        for p in approved:
            got = reply(p["question"])
            ok = answered(p["answer"], got)
            if p["kind"] in ("manual", "suggested"):
                queue.set_tested(p["id"], ok)                                         # tested: passed or failed, it is no longer waiting to be tested
            if not ok:
                differ.append({"id": p["id"], "question": p["question"], "expected": p["answer"], "got": got[:300]})
        handler.send_json({"tested": len(approved), "matched": len(approved) - len(differ), "differ": differ[:20]})
        return True
    return False
