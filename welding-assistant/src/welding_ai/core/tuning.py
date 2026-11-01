"""Thresholds chosen by the evaluation (evaluation/eval_out/thresholds.json), applied the same way for the server, the tests and the
evaluation, so every measurement is of the configuration that is actually deployed."""
import json

from welding_ai import paths

_applied = False


# Read evaluation/eval_out/thresholds.json (if present) and patch it into the answering module and Config.
def apply_tuned_thresholds(config_cls=None):
    global _applied
    from welding_ai.answering import answering
    from welding_ai.core.config import Config
    config_cls = config_cls or Config
    try:
        t = json.loads((paths.EVAL_OUT / "thresholds.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    answering.EXTRACT_MIN_COVER, answering.REQUIRE_RAREST = t["min_cover"], t["rarest"]
    answering.VERIFY_SUPPORT, answering.VERIFY_COHERENCE = t["support"], t["coherence"]
    answering.MIN_MATCHED = t.get("min_matched", 2)
    config_cls.hard_min = t.get("hard_min", config_cls.hard_min)
    if "faq_threshold" in t:
        config_cls.faq_threshold, config_cls.faq_qcov = t["faq_threshold"], t["faq_qcov"]
        config_cls.faq_vcov, config_cls.faq_margin = t["faq_vcov"], t["faq_margin"]
        config_cls.faq_rare_idf = 5.0 if t["faq_rare"] else 1e9
    _applied = True
    return t
