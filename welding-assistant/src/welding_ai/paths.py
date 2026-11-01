"""Where everything lives. Every module gets its paths from here."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]                       # welding-assistant/
DATA = ROOT / "data"
INDEX_DIR = Path(os.environ.get("WELD_INDEX_DIR", DATA / "index"))                                     # chunks / sections / tables / faq (built by welding_ai.ingestion)
FORMULAS_SEED = DATA / "formulas" / "documents.json"                                                    # formulas transcribed from the documents (each with the quoted passage it came from)
DB_PATH = Path(os.environ.get("WELD_DB", DATA / "app.db"))                                             # accounts and sessions (SQLite)
ADMIN_FILE = Path(os.environ.get("WELD_ADMIN_FILE", DATA / "admin-credentials.txt"))                    # each administrator's username, email, password and recovery key, kept current
MANUAL_PASSAGES = Path(os.environ.get("WELD_MANUAL_PASSAGES", DATA / "manual_passages.json"))                  # passages written by hand: kept apart from the index so a rebuild never removes them
SETTINGS_DIR =Path(os.environ.get("WELD_SETTINGS_DIR", DATA / "settings"))                              # hand-editable settings: account-rules.json, permissions.xml
STATIC_DIR = ROOT / "apps" / "web"
EVAL_DIR = ROOT / "evaluation"
EVAL_OUT = EVAL_DIR / "eval_out"
TRAINING_DIR = ROOT / "training"
RETRAIN_DIR = Path(os.environ.get("WELD_RETRAIN_DIR", DATA / "retrain"))                               # the retrain job: its state file, speed history and log

# the source documents and the fine-tuned model's training data stay where the training notebook expects them
LEARNED_PAIRS = Path(os.environ.get("WELD_LEARNED_PAIRS", DATA / "training" / "learned_pairs.json"))        # approved pairs whose answer is not in the documents: never trained on (training/learned.py)
TRAINING_PAIRS = Path(os.environ.get("WELD_TRAINING_PAIRS", DATA / "training" / "training_pairs.json"))     # approved pairs whose answer is in the documents: what a retrain takes
TRASH_DIR = Path(os.environ.get("WELD_TRASH_DIR", DATA / "deleted_documents"))                          # documents deleted from the library wait here, not erased
DOCS_DIR = Path(os.environ.get("WELD_DOCS_DIR", ROOT.parent / "KnowledgeBase"))
MODEL_DIR = Path(os.environ.get("WELD_MODEL_DIR", ROOT.parent / "welding_slm"))
EVAL_ROWS = MODEL_DIR / "04_eval.jsonl"
