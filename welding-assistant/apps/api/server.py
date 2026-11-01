"""HTTP API for the web page, on http://127.0.0.1:8765 (this computer only). Routes only: all logic lives in `welding_ai`.

    GET  /                 the pages (index.html, login.html, formulas.html, admin/*.html, css/, js/)                     GET  /health          liveness + counts
    GET  /api/status       models, counts                GET  /api/faqs        the verified FAQs
    GET  /api/library      every document (name, number of sections)
    GET  /api/doc          document preview (?name=&chunk=)   GET /docs/<name>  download the .docx
    POST /api/chat         one question -> NDJSON events (see welding_ai.core.events)
    POST /api/warm         load the model into memory
    /api/auth/*  /api/users/*   sign-in, password recovery and user administration (auth_routes.py); every /api route needs a session except sign-in and recovery
    /api/formulas/*             the formula registry, calculator and formula agent (formula_routes.py)
    /api/history /api/feedback /api/audit   per-user history, answer feedback, the admin's audit log (activity_routes.py)
    /api/admin/models/*         model names, notes and the active model (model_routes.py)
    /api/admin/documents/*      add documents, check for new ones, re-index in the background (document_routes.py)
    /api/admin/qa/* /api/refresh/*   review the generated question-and-answer pairs; check for new documents and retrain, cancel (retrain_routes.py)
    /api/agents/wps/<token>     the WPS draft (.docx) the chat offered (agent_routes.py)

    python apps/api/server.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))

import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import training_routes                                       # registers its routes with the gateway
import my_pairs_routes                                       # registers its routes with the gateway
import activity_routes                                    # registers its routes with the gateway
import agent_routes
import auth_routes
import gateway
import document_routes
import form_routes
import hand_routes
import formula_routes
import model_routes
import ask_templates
import manual_routes
import model_check_routes
import package_routes
import retrain_routes
import static_files
import system_routes
from welding_ai import paths
from welding_ai.accounts import levels, permissions, rules
from welding_ai.accounts import mailer
from welding_ai.accounts.admin_file import AdminFile
from welding_ai.accounts.recovery import Recovery, ttl_from_env
from welding_ai.accounts.service import AccountService
from welding_ai.agents import options as agent_options
from welding_ai.agents.chat import AgentChat, AgentTools
from welding_ai.agents.drafts import DraftStore
from welding_ai.core import events
from welding_ai.forms.lookup import DraftLookup
from welding_ai.forms.store import FormStore
from welding_ai.activity.service import ActivityLog
from welding_ai.bootstrap import build_harness
from welding_ai.formulas import extract as formula_extract
from welding_ai.formulas.chat import FormulaChat
from welding_ai.formulas.store import FormulaStore
from welding_ai.core.log import get_logger
from welding_ai.documents import DocumentService
from welding_ai.ingestion import rebuild
from welding_ai.ingestion.job import IndexJob
from welding_ai.ingestion.manifest import Manifest
from welding_ai.llm.install import Installer
from welding_ai.llm.models import installed_models
from welding_ai.llm.registry import ModelRegistry
from welding_ai.llm.ollama import warm
from welding_ai.orchestration.harness import Config
from welding_ai.retrain import eta
from welding_ai.answering.facts import FactAnswerer
from welding_ai.knowledge.catalogue import capabilities
from welding_ai.knowledge.embed import TextVectors
from welding_ai.knowledge import passage_types
from welding_ai.knowledge.learned_lookup import LearnedLookup
from welding_ai.knowledge.manual_passages import ManualPassages
from welding_ai.retrieval.similarity import PassageSimilarity
from welding_ai.retrain import ollama_admin
from welding_ai.retrain.control import Retrain
from welding_ai.retrain.state import JobState
from welding_ai.training import qa
from welding_ai.answering.manual import ManualAnswers
from welding_ai.training import learned
from welding_ai.training.queue import QaQueue

HOST = os.environ.get("WELD_HOST", "127.0.0.1")      # localhost only unless WELD_HOST=0.0.0.0 (put HTTPS in front before sharing on a network)
PORT = int(os.environ.get("WELD_PORT", 8765))
MAX_JSON = 2 * 1024 * 1024                           # the biggest JSON request taken (a file upload has its own limit)
BUILD = "2.3 · accounts, sign-in and the formula registry"

log = get_logger("api")
H = build_harness()
DOCS = DocumentService(H.sources, H.index.chunks)
ADMIN_FILE = AdminFile(paths.ADMIN_FILE)
ACCOUNTS = AccountService(paths.DB_PATH, vault=ADMIN_FILE)
FORMULAS = FormulaStore(paths.DB_PATH)
ACTIVITY = ActivityLog(paths.DB_PATH)
gateway.app.activity = ACTIVITY
gateway.app.queue = None                         # set below, with the pair queue                  # the services the registered routes use (gateway.py)
rules.current().ensure_file()                 # data/settings/account-rules.json and permissions.xml: written once so there is something to edit, read again when they change
permissions.current().ensure_file()
MODELS = ModelRegistry(paths.DB_PATH)
INSTALLER = Installer(paths.MODEL_DIR / "runs", paths.MODEL_DIR / "pipeline", MODELS, remove=ollama_admin.remove)
IMPORTS = package_routes.Imports(paths.RETRAIN_DIR / "imports")
DRAFTS = DraftStore()
FORMS = FormStore(paths.DB_PATH)                 # templates and drafts (the editable tables)
DRAFT_LOOKUP = DraftLookup(FORMS)                         # chat answers from the filled cells of a draft it is asked about, and says when a draft holds something about any question
TEMPLATE_ASK = ask_templates.TemplateAsk(FORMS)         # questions about templates, answered from the saved templates
RECOVERY = Recovery(paths.DB_PATH, ACCOUNTS, mailer.from_env(paths.DATA / "outbox"), ttl=ttl_from_env(), vault=ADMIN_FILE)
MANIFEST = Manifest(paths.DB_PATH, paths.DOCS_DIR)
QAQUEUE = QaQueue(paths.DB_PATH)
PAIR_CHECKS = model_check_routes.PairChecks(paths.DB_PATH)
MANUAL = ManualAnswers(paths.DB_PATH)
PASSAGES = ManualPassages(paths.MANUAL_PASSAGES)


# A pair a person sent becomes an ordinary one when an administrator approves it: its own passage (if it has one) is added to the knowledge base only now, never before.
def promote_suggested():
    added = False
    for pair in QAQUEUE.by_kind("suggested"):
        if pair["status"] != "approved":
            continue
        if pair["doc"] == "Added by hand":
            try:
                PASSAGES.add(pair["section"], pair["evidence"], pair["added_by"])
                added = True
            except Exception:
                log.exception("the passage of pair %s could not be added", pair["id"])
                continue
        QAQUEUE.set_kind(pair["id"], "manual")
    if added:
        on_passage()


def sync_pairs():
    """After any change to a pair: rewrite the two lists (the training set and the learned pairs)."""
    try:
        learned.write_both(QAQUEUE, paths.LEARNED_PAIRS, paths.TRAINING_PAIRS)
    except Exception:
        log.exception("could not rewrite the training and learned pairs")


QAQUEUE.listeners.insert(0, sync_pairs)


def refresh_pair_areas():
    """Which agent each saved pair (added by a person) is filed under, for the chat to say so when it is asked on another one."""
    try:
        areas = PAIR_CHECKS.areas()
        H.pair_areas = {" ".join(re.findall(r"[a-z0-9]+", p["question"].lower())): areas.get(p["uid"], p.get("area") or "") for p in QAQUEUE.by_kind("manual") if p["status"] == "approved"}
    except Exception:
        log.exception("could not read where the saved pairs are filed")


LOOKUP = LearnedLookup(TextVectors(paths.INDEX_DIR / "learned"))                 # approved pairs added by people, found by the meaning of their question (no training)


def refresh_lookup():
    """Re-reads the approved pairs into the lookup, in the background (it embeds a new question once; nothing else is slow)."""
    threading.Thread(target=lambda: LOOKUP.refresh(QAQUEUE.approved()), daemon=True, name="learned-lookup").start()


QAQUEUE.listeners.append(refresh_lookup)
QAQUEUE.listeners.append(promote_suggested)
QAQUEUE.listeners.append(refresh_pair_areas)
gateway.app.queue = QAQUEUE
gateway.app.sync = sync_pairs
gateway.app.chunks = lambda: H.index.chunks
gateway.app.forms = FORMS


AGENT_OPTIONS = paths.INDEX_DIR / "agent_options.json"


def refresh_agent_options():
    """The Agents page dropdowns, made from the documents just indexed (agents/options.py); the answering side reads the same documents, so both follow a reindex."""
    try:
        tables = json.loads((paths.INDEX_DIR / "tables.json").read_text(encoding="utf-8"))
        agent_options.save(agent_options.build(H.index.chunks, tables), AGENT_OPTIONS)
    except Exception:
        log.exception("could not make the agent dropdown options")


def read_agent_options():
    if agent_options.load(AGENT_OPTIONS) is None:
        refresh_agent_options()
    return agent_options.load(AGENT_OPTIONS) or {}


gateway.app.agent_options = read_agent_options
SIMILARITY = PassageSimilarity(paths.INDEX_DIR / "passages")           # how close each passage is to a question, by meaning; filled in the background
RETRAIN = Retrain(JobState(paths.RETRAIN_DIR / "job.json"), eta.History(paths.RETRAIN_DIR / "history.json"), QAQUEUE, base_rows=3000, registry=MODELS)
PASSAGE_CACHE = {}


# The chunk of a document that holds a quoted passage (so a formula's source opens at the right place in the viewer).
def locate_passage(document, anchor):
    key = (document, anchor)
    if key not in PASSAGE_CACHE:
        squash = lambda t: re.sub(r"\s+", " ", t)
        PASSAGE_CACHE[key] = next((i for i, c in enumerate(H.index.chunks) if c["source"] == document and squash(anchor) in squash(c["text"])), None)
    return PASSAGE_CACHE[key]


# Make the registry's "documents" formulas match the documents as they are now: the hand-checked file (kept while its quoted line is still in the documents) plus every formula
# read from the passages. A bad entry is reported, never half-loaded. Runs at start-up and after every reindex.
def load_document_formulas(chunks=None):
    chunks = chunks if chunks is not None else H.index.chunks
    seed = json.loads(paths.FORMULAS_SEED.read_text(encoding="utf-8")) if paths.FORMULAS_SEED.is_file() else []
    entries, found = formula_extract.from_documents(chunks, seed)
    report = FORMULAS.sync_documents(entries)
    log.info("formulas from the documents: %d read from the passages, %d added, %d updated, %d retired", len(found["found"]), len(report["added"]), len(report["updated"]), len(report["retired"]))
    for formula_id, problems in report["invalid"].items():
        log.warning("formula %s was not loaded: %s", formula_id, "; ".join(problems))


# Sorts the passages by what they are and the documents by the agent they feed (knowledge/passage_types.py), on every reindex; the agents prefer their own passages.
def sort_passages(h):
    try:
        sorted_docs = passage_types.sort_documents(h.index.chunks)
        passage_types.write(sorted_docs, paths.INDEX_DIR / "agents.json")
        h.passage_types = sorted_docs["passages"]
        log.info("passages sorted: %s", {a: len(rows) for a, rows in sorted_docs["agents"].items()})
    except Exception:
        log.exception("could not sort the passages by agent; the agents search every passage")
        h.passage_types = {}


# The model an administrator chose is used again after a restart (if it is still installed).
def apply_active_model(h):
    chosen, installed = MODELS.active(), installed_models(h.cfg.default_model)
    if chosen and installed is not None:
        if any(m["id"] == chosen for m in installed):
            h.cfg.default_model = chosen
        else:
            log.warning("the active model %s is not installed in Ollama; using %s", chosen, h.cfg.default_model)


# The facts of the documents (answering/facts.py), built from the index on disk. Embedding the facet labels takes a few seconds the first time (never again for a label already
# seen) and needs the embedding model, so it runs in the background; until it is done, and whenever the model is not running, facets are matched by words.
FACT_VECTORS = TextVectors(paths.INDEX_DIR)
CAPABILITIES = {}


def build_facts():
    try:
        facts = FactAnswerer.build(vectors=FACT_VECTORS)
    except Exception:
        log.exception("could not read the facts of the documents; questions are answered without them")
        return None

    def learn():
        try:
            if not RETRAIN.running:                                      # the GPU belongs to the training
                done = facts.prepare()
                log.info("facts: %d facts, %d facet labels (%s)", len(facts.units), len(facts.facets.labels), "matched by meaning" if facts.facets.semantic else "matched by words")
                if done:
                    log.info("facts: %d new labels embedded", done)
            CAPABILITIES.clear()
            CAPABILITIES.update(capabilities(facts))
        except Exception:
            log.exception("facts: could not prepare the facet vocabulary")

    threading.Thread(target=learn, daemon=True).start()
    return facts


# Gives a freshly built pipeline what the server adds to it.
def wire(h):
    apply_active_model(h)
    h.learned = LOOKUP                                                          # approved pairs are used at once, by the meaning of their question
    h.manual = MANUAL                                                           # an answer a person wrote where the documents have none
    agents = AgentChat(FORMULAS.usable, DRAFTS)                                 # a described situation, a spot-welding value or a WPS: the agents
    if h.cfg.grounded:
        h.similarity = SIMILARITY                                               # the passages closest to a question in meaning go to the model
        SIMILARITY.prepare_in_background(h.index.chunks)
    return h.attach(formulas=FormulaChat(FORMULAS.usable, locate_passage),      # a calculation request: the calculator
                    agents=agents,
                    facts=build_facts(),                                        # what each document says about each thing, by meaning (also used to tell which agent a question belongs to)
                    tools=AgentTools(agents))                                   # a WPS draft or spot-welding values: the tools among the agents


# Builds the pipeline again from the index on disk and switches to it. Requests already running finish on the old one.
def reload_pipeline():
    global H, DOCS
    fresh = build_harness()
    fresh.cache.clear()                  # cached answers hold chunk numbers, which move when documents are added
    PASSAGE_CACHE.clear()
    fresh.cfg.default_model = H.cfg.default_model
    H, DOCS = wire(fresh), DocumentService(fresh.sources, fresh.index.chunks)
    sort_passages(H)
    load_document_formulas()
    refresh_pair_areas()
    refresh_agent_options()
    log.info("pipeline reloaded: %d passages, %d documents", H.index.n, len(H.sources))


# How many question pairs written by hand a reindex would delete.
def hand_count():
    return 0                                                                      # nothing is deleted by a reindex or a retrain any more


# The document names in the library, as the file check of a model file wants them.
def library_names():
    return [p.name for p in paths.DOCS_DIR.glob("*.docx")]


# A passage was written by hand: build the search index again from what is on disk, so a question can find it. The documents are not read again.
def on_passage():
    threading.Thread(target=reload_pipeline, daemon=True).start()


# Picks up a model an administrator activated, or a retrain promoted (that runs in its own process), so everyone gets it without a restart.
def sync_active_model():
    chosen = MODELS.active()
    if chosen and chosen != H.cfg.default_model:
        apply_active_model(H)
        if H.cfg.default_model == chosen:
            threading.Thread(target=warm, args=(chosen,), daemon=True).start()
    if len(ollama_admin.loaded_models()) > 1 or ollama_admin.partly_on_processor():       # one answering model at a time: the others are unloaded, and a model that spills onto the processor is not shared
        threading.Thread(target=ollama_admin.keep_only, args=(H.cfg.default_model,), daemon=True).start()


# The background re-index: rebuild, switch to the new index (back to the old one if that fails), then remember what it was built from.
def index_run(progress, cancel):
    seen = MANIFEST.detect()
    report = rebuild.rebuild(paths.DOCS_DIR, paths.INDEX_DIR, progress=progress, cancel=cancel)
    progress("Loading the new index")
    try:
        reload_pipeline()
    except Exception as e:
        rebuild.rollback(paths.INDEX_DIR)
        reload_pipeline()
        raise rebuild.RebuildError(f"The new index could not be loaded ({type(e).__name__}), so the previous one is back in use.")
    MANIFEST.mark_current()
    progress("Making question-and-answer pairs")
    hand = 0                                                                      # pairs written by hand are learned pairs now: a reindex leaves them (only a pair's own document changing retires it)
    retired = QAQUEUE.retire(seen["changed"] + seen["removed"])                  # what they said before may no longer be true
    made = QAQUEUE.add_many(qa.generate(H.tables.t, H.index.chunks, seen["new"] + seen["changed"], H.index.focus)) if H.tables else {"added": 0, "skipped": 0, "status": ""}
    progress("Checking the question pairs against the new documents")
    lost = check_pairs()
    refresh_lookup()
    learned.write_both(QAQUEUE, paths.LEARNED_PAIRS, paths.TRAINING_PAIRS)        # the lists follow the documents; the model is not retrained by a reindex
    return {**report, "pairs": made["added"], "pairs_status": made["status"], "pairs_retired": retired, "hand_pairs_deleted": hand,
            "retrain": f"{lost} approved pair{'s' if lost != 1 else ''} not found in the documents any more." if lost else ""}


def check_pairs():
    """Checks every pair against the documents as they are now and keeps the result (the table's Not found tag). -> how many approved pairs are not found."""
    from model_check_routes import all_pairs, document_texts, judge
    pairs = all_pairs(QAQUEUE)
    texts = document_texts(H.index.chunks)
    results = {p["uid"]: judge(p, texts) for p in pairs}
    PAIR_CHECKS.save(results)
    return sum(1 for p in pairs if p["status"] == "approved" and results[p["uid"]] != "found")


# After a batch of documents is indexed: one short retrain on everything waiting, unless an administrator turned that off. -> what happened, in words.
def auto_retrain():
    if QAQUEUE.setting(AUTO_RETRAIN, "0") != "1":
        return "Automatic retraining is off."
    if RETRAIN.running:
        return "A retrain is already running; the new pairs join the next one."
    try:
        ok, result = RETRAIN.start("short")
    except Exception as e:
        log.warning("automatic retrain could not start: %s", e)
        return f"The retrain could not start ({type(e).__name__})."
    if not ok:
        return result
    sync_active_model()
    log.info("automatic retrain started after indexing")
    return "A retrain started."


# Indexing is asked for after an upload or a delete. It starts at once, or as soon as a running retrain or index has finished: the files are already saved.
_index_wanted = threading.Event()


def reply_text(question):
    """The answer the app gives to a question now, from the documents alone (no model is used): the Question pairs "Test the answers" check."""
    return H.run(question, mode="verbatim")["answer"]


def system_facts():
    """What the system check needs from the running server: the retrain folder, how many check questions there are, and whether Ollama answers."""
    dev = paths.MODEL_DIR / "v2" / "dev.jsonl"
    questions = sum(1 for line in open(dev, encoding="utf-8") if line.strip()) if dev.exists() else 0
    return paths.RETRAIN_DIR, questions, installed_models(H.cfg.default_model) is not None


def request_index():
    _index_wanted.set()
    return try_index()


def try_index():
    if not _index_wanted.is_set():
        return False
    if RETRAIN.running or INDEXJOB.running:
        return False
    if INDEXJOB.start():
        _index_wanted.clear()
        return True
    return False


def watch_index():
    while True:
        time.sleep(3)
        try:
            try_index()
        except Exception:
            log.exception("could not start the waiting index")


INDEXJOB = IndexJob(index_run)
AUTO_RETRAIN = "auto_retrain"
MANIFEST.seed_if_empty()
wire(H)
refresh_pair_areas()
refresh_lookup()
sync_pairs()
sort_passages(H)
load_document_formulas()


# What a person was shown for one question, collected from the events, for the audit log.
class Seen:
    def __init__(self):
        self.parts, self.ai, self.match, self.documents = [], "", "", []

    def add(self, ev):
        t = ev.get("type")
        if t == "sources":
            self.match = ev.get("match", "")
            self.documents = [s.get("source", "") for s in ev.get("sources", [])[:6] if s.get("source")]
        elif t == "token":
            self.parts.append(ev.get("t", ""))
        elif t == "exact":
            self.parts.append(ev.get("text", "") + "\n\nAI summary: ")
        elif t == "table":
            rows = [ev.get("header", [])] + ev.get("rows", [])
            self.parts.append("\n".join(" | ".join(map(str, r)) for r in rows) + "\n")
        elif t == "replace":
            self.parts, self.ai = [ev.get("text", "")], ""
        elif t == "ai":
            self.ai += ev.get("t", "")
        elif t == "ai_drop":
            self.ai = ""

    def text(self):
        text = "".join(self.parts).strip()
        return text + ("\n\nAI summary: " + self.ai.strip() if self.ai.strip() else "")


def _area(done, body):
    """What answered, as the Activity page shows it: General (the ordinary chat), the agent the question was sent to, or Formulas."""
    via = str(body.get("via") or "")
    if via in ("formula", "calculate") or str(done.get("route", "")).startswith("formula"):
        return "Formulas"
    return {"fault-finder": "Fault Finder", "procedure-advisor": "Procedure Advisor", "troubleshoot": "Fault Finder", "scenario": "Fault Finder", "wps": "WPS Builder",
            "standards": "Procedure Advisor"}.get(via, "General")


class Handler(BaseHTTPRequestHandler):
    server_version = "WeldRef/3.1"
    user = None                                      # the signed-in user, set by auth_routes.handle

    def log_message(self, fmt, *args):
        pass

    def send_bytes(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def emit(self, obj):
        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        self.wfile.flush()

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        u = urlparse(self.path)
        path = gateway.versioned(unquote(u.path))                  # /api/v1/x is /api/x
        if auth_routes.handle(self, ACCOUNTS, "GET", path, None, recovery=RECOVERY) or gateway.dispatch(self, "GET", path, u.query, None):
            return                                                  # signed in? then the registered routes (gateway.py); the older handlers follow
        if (formula_routes.handle(self, FORMULAS, locate_passage, "GET", path, None)
                or form_routes.handle(self, FORMS, "GET", path, parse_qs(u.query), None)
                or package_routes.handle(self, MODELS, QAQUEUE, INSTALLER, IMPORTS, library_names, lambda: RETRAIN.running, "GET", path, None)
                or model_routes.handle(self, MODELS, H.cfg, "GET", path, None, specs=lambda: system_routes.saved(QAQUEUE)[0]) or agent_routes.handle(self, DRAFTS, "GET", path)
                or document_routes.handle(self, paths.DOCS_DIR, paths.TRASH_DIR, MANIFEST, INDEXJOB, "GET", path, None, request_index, retraining=lambda: RETRAIN.running, hand_pairs=hand_count)
                or manual_routes.handle(self, MANUAL, "GET", path, None)
                or hand_routes.handle(self, QAQUEUE, PAIR_CHECKS, PASSAGES, "GET", path, None, on_passage)
                or system_routes.handle(self, QAQUEUE, paths.RETRAIN_DIR, "GET", path, None)
                or model_check_routes.handle(self, QAQUEUE, PAIR_CHECKS, lambda: H.index.chunks, "GET", path, None, system_facts, reply_text)
                or retrain_routes.handle(self, QAQUEUE, RETRAIN, MANIFEST, INDEXJOB, "GET", path, None, on_change=sync_active_model)):
            return
        page = static_files.static_file(paths.STATIC_DIR, path)
        if page is not None and page.is_file():
            return self.send_bytes(200, page.read_bytes(), static_files.TYPES[page.suffix])
        if path == "/health":
            return self.send_json({"status": "ok", "passages": H.index.n, "documents": len(H.sources), "mode": H.cfg.mode})
        if path == "/api/status":
            sync_active_model()
            models = installed_models(H.cfg.default_model)
            described = MODELS.describe(models, H.cfg.default_model) if models else []
            named = [{k: m[k] for k in ("id", "label", "note")} | {"kind": m.get("kind", "")} for m in described if not m["hidden"] or m["active"]]
            now = next((m for m in described if m["active"]), None)
            return self.send_json({"ollama": models is not None, "models": named, "default": H.cfg.default_model,
                                   "active": {"id": now["id"], "kind": now.get("kind", ""), "label": now["label"]} if now else None, "loaded": ollama_admin.loaded_models(), "offload": ollama_admin.partly_on_processor(),
                                   "passages": H.index.n, "documents": len(H.sources), "faqs": len(H.faqs), "build": BUILD, "retraining": RETRAIN.running})
        if path == "/api/library":
            per = {}
            for c in H.index.chunks:
                if not c.get("manual"):                          # a passage written by hand is not a document of the library
                    per.setdefault(c["source"], set()).add(c["heading"])
            return self.send_json([{"source": k, "sections": len(v)} for k, v in sorted(per.items())])
        if path == "/api/capabilities":
            return self.send_json(CAPABILITIES or {"tasks": [], "units": 0, "documents": 0, "semantic": False, "loading": True})
        if path == "/api/faqs":
            return self.send_json([{"id": f["id"], "question": f["question"]} for f in H.faqs])
        if path == "/api/doc":
            q = parse_qs(u.query)
            name = (q.get("name") or [""])[0]
            try:
                return self.send_json(DOCS.preview(name, (q.get("chunk") or [None])[0]))
            except KeyError:
                return self.send_json({"error": "unknown document"}, 404)
            except Exception as e:
                return self.send_json({"error": f"could not read the document ({type(e).__name__})"}, 500)
        if path.startswith("/docs/"):
            name = path[len("/docs/"):]
            if DOCS.has(name):
                return self.send_bytes(200, DOCS.raw(name), "application/octet-stream", {"Content-Disposition": f'attachment; filename="{name}"'})
        self.send_bytes(404, b"not found", "text/plain")

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        u = urlparse(self.path)
        path = gateway.versioned(u.path)
        self.raw_body = b""
        try:
            n = int(self.headers.get("Content-Length") or 0)
            upload = self.headers.get("Content-Type", "").startswith("multipart/form-data") and path in ("/api/admin/documents/upload", "/api/templates/upload")
            upload = upload or path == "/api/admin/models/package/check"          # a model file: the bytes themselves
            if n > (package_routes.MAX_FILE if path == "/api/admin/models/package/check" else (form_routes.MAX_UPLOAD if path == "/api/templates/upload" else document_routes.MAX_REQUEST) if upload else MAX_JSON):
                self.close_connection = True
                return self.send_json({"error": "That request is too large."}, 413)
            raw = self.rfile.read(n)
            if upload:
                body, self.raw_body = {}, raw                    # a file upload is read by its route, not as JSON
            else:
                body = json.loads(raw or b"{}")
        except Exception:
            return self.send_json({"error": "bad request"}, 400)
        if auth_routes.handle(self, ACCOUNTS, "POST", path, body, recovery=RECOVERY) or gateway.dispatch(self, "POST", path, u.query, body):
            return
        if (formula_routes.handle(self, FORMULAS, locate_passage, "POST", path, body)
                or form_routes.handle(self, FORMS, "POST", path, None, body)
                or agent_routes.handle(self, DRAFTS, "POST", path, body)
                or package_routes.handle(self, MODELS, QAQUEUE, INSTALLER, IMPORTS, library_names, lambda: RETRAIN.running, "POST", path, body)
                or model_routes.handle(self, MODELS, H.cfg, "POST", path, body, on_activate=lambda m: threading.Thread(target=warm, args=(m,), daemon=True).start(), remove=ollama_admin.remove, specs=lambda: system_routes.saved(QAQUEUE)[0])
                or document_routes.handle(self, paths.DOCS_DIR, paths.TRASH_DIR, MANIFEST, INDEXJOB, "POST", path, body, request_index, retraining=lambda: RETRAIN.running, hand_pairs=hand_count)
                or ask_templates.handle(self, FORMS, "POST", path, body)
                or manual_routes.handle(self, MANUAL, "POST", path, body)
                or hand_routes.handle(self, QAQUEUE, PAIR_CHECKS, PASSAGES, "POST", path, body, on_passage)
                or system_routes.handle(self, QAQUEUE, paths.RETRAIN_DIR, "POST", path, body)
                or model_check_routes.handle(self, QAQUEUE, PAIR_CHECKS, lambda: H.index.chunks, "POST", path, body, system_facts, reply_text)
                or retrain_routes.handle(self, QAQUEUE, RETRAIN, MANIFEST, INDEXJOB, "POST", path, body, on_change=sync_active_model)):
            return
        if path == "/api/warm":
            if RETRAIN.running:
                return self.send_json({"ok": False, "reason": "A retrain is using the GPU."})
            threading.Thread(target=warm, args=(str(body.get("model") or H.cfg.default_model),), daemon=True).start()
            return self.send_json({"ok": True})
        if path == "/api/chat":
            return self.chat(body)
        self.send_bytes(404, b"not found", "text/plain")

    # Writes the question and what the person was shown to the audit log. Never lets a logging problem break an answer.
    def record(self, body, seen, done, t0):
        try:
            prov = done.get("provenance") or {}
            return ACTIVITY.record(self.user["username"], body.get("q", ""), seen.text(), route=done.get("route", ""), label=prov.get("label", ""),
                                   email=self.user["email"], area=_area(done, body),
                                   mode="Summary" if body.get("mode", "answer") == "answer" else "Exact", model=body.get("model") or H.cfg.default_model,
                                   match=seen.match, documents=seen.documents, ms=(time.time() - t0) * 1000)
        except Exception:
            log.exception("could not record the answer in the audit log")
            return None

    def chat(self, body):
        rid, t0, route = uuid.uuid4().hex[:8], time.time(), "?"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-Id", rid)
        self.end_headers()
        seen = Seen()
        if H.cfg.grounded:
            body["mode"] = "search" if body.get("mode") == "search" else "answer"          # every answer is written by the model, for every person
        elif not levels.allowed(self.user["level"], "summary") and body.get("mode", "answer") == "answer":
            body["mode"] = "verbatim"                    # a Viewer gets answers straight from the documents
        if RETRAIN.running and body.get("mode", "answer") == "answer":
            body["mode"] = "verbatim"                    # the GPU belongs to the training: the closest passages are shown until it is done
        try:
            q = str(body.get("q", ""))
            drafts_ok = not body.get("via") and levels.allowed(self.user["level"], "draft_view")
            stream = None if body.get("via") else TEMPLATE_ASK.claim(q, levels.allowed(self.user["level"], "template_ask"))      # a question about templates
            if stream is None and drafts_ok:
                stream = DRAFT_LOOKUP.claim(q)                                                                                  # ...or about a draft
            advice = DRAFT_LOOKUP.advice(q) if drafts_ok and stream is None else ""
            for ev in stream or H.stream(str(body.get("q", "")), history=body.get("history") or [], model=body.get("model") or None,
                                         mode=body.get("mode", "answer"), k=int(body.get("k") or 5), via=str(body.get("via") or "")[:20]):
                if advice and ev.get("type") == "done":
                    self.emit(events.token(advice))                            # "you also have N drafts with this entered", just before the answer ends
                    seen.add(events.token(advice))
                seen.add(ev)
                if ev.get("type") == "done":
                    route = ev.get("route", "?")
                    ev["log_id"] = self.record(body, seen, ev, t0)          # the page sends this back with its feedback
                self.emit(ev)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            if route == "?":
                self.record(body, seen, {"route": "stopped"}, t0)           # the person stopped it: the question is still on record
        except Exception as e:      # never leave the page hanging
            log.exception("chat rid=%s failed", rid)
            try:
                self.emit({"type": "error", "message": f"Unexpected error ({type(e).__name__})."})
                self.emit({"type": "done", "route": "server_error", "grounded": False, "timings": {}})
            except Exception:
                pass
        log.info("chat rid=%s route=%s %.0f ms q=%s", rid, route, (time.time() - t0) * 1000, re.sub(r"\s+", " ", str(body.get("q", "")))[:80])


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=watch_index, daemon=True).start()
    if not RETRAIN.running:
        threading.Thread(target=warm, args=(H.cfg.default_model,), daemon=True).start()
    if RECOVERY.mailer.mode == "outbox":
        log.warning("password recovery: no mail server is set up (WELD_SMTP_HOST), so recovery emails are written to %s", paths.DATA / "outbox")
    if not ACCOUNTS.has_users():
        log.warning("No accounts yet: run  python scripts/create_admin.py  to create the first administrator.")
    log.info("http://%s:%d  (Ctrl+C to stop)", HOST, PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
