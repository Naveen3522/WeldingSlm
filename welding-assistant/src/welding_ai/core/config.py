"""Runtime configuration: one object, defaults here, overridable from a JSON file or the environment."""
import json
import os
from pathlib import Path

from welding_ai import paths


class Config:
    default_model = "welding-slm-q8"        # v1 weights as Q8_0: same answers, 3x faster, fully on the GPU (welding-slm = the f16 original)
    model_ctx = {"welding-slm": 4096, "welding-slm-q8": 4096, "welding-slm-v2": 4096}
    k = 5
    hard_min = 0.45           # top-passage coverage below this: nothing in the documents matches
    weak = 0.55               # below this: answer, but flag a weak match
    excerpt_chars = 850
    context_chars = 4200
    policy = "llm_verified"   # llm_verified | llm_raw | extractive
    agree_min = 0.2           # a model answer must share this much with the best verbatim sentences
    require_support = True    # a model answer needs a passage sentence that matches the question's key terms
    weak_policy = "llm"       # what to do on a weak match: "llm" (verified) or "extractive"
    use_faq = True
    use_engine = True         # understand the question and build the answer (table / list / steps / explanation) from the documents
    legacy_fallback = True    # when the engine has nothing: fall back to the passage-matching pipeline (False: say it is not covered)
    use_simple = True         # show a plain-English rewrite of each composed answer on top of it (answering/composer.py); False: today's output only
    grounded = False          # True: every question about the documents is answered by the model from the passages most like it (orchestration/grounded.py); the server turns it on
    use_cache = True
    use_expand = True
    use_correct = True
    faq_threshold = 0.55
    faq_qcov = 0.80          # share of the question's (rare-word weighted) terms the FAQ variant must cover
    faq_vcov = 0.45          # ... and share of the variant the question must cover
    faq_margin = 0.08
    faq_rare_idf = 5.0
    num_predict = 450
    temperature = 0.0         # factual answers: greedy (report: greedy 13.8% vs sampled 5-7.5% on the same questions)
    top_k = 20
    repeat_penalty = 1.0      # 1.05 can distort numbers and standard IDs
    llm_timeout = 900
    mode = "local"            # "local": everything in one process; "distributed": understanding and answering run as separate services
    understanding_url = "http://127.0.0.1:8801"
    answer_url = "http://127.0.0.1:8802"
    cache_path = paths.INDEX_DIR / "answer_cache.json"
    cache_max = 500

    # Apply keyword overrides on top of the class defaults; rejects unknown option names.
    def __init__(self, **kw):
        for k, v in kw.items():
            if not hasattr(self, k):
                raise AttributeError(k)
            setattr(self, k, v)

    @classmethod
    def load(cls, path=None):
        """Defaults, then the JSON file (WELD_CONFIG or the given path), then WELD_<NAME> environment variables."""
        cfg = cls()
        p = Path(path or os.environ.get("WELD_CONFIG", paths.ROOT / "config.json"))
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        for k in [k for k in dir(cfg) if not k.startswith("_") and not callable(getattr(cfg, k))]:
            env = os.environ.get("WELD_" + k.upper())
            if env is not None:
                cur = getattr(cfg, k)
                setattr(cfg, k, (env.lower() in ("1", "true", "yes")) if isinstance(cur, bool) else type(cur)(env) if isinstance(cur, (int, float, str)) else cur)
        return cfg
