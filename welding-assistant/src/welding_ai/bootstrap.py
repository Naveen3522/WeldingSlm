"""Composition root: builds the pipeline from configuration. The only place that decides which adapters are used."""
import os

from welding_ai.core.config import Config
from welding_ai.core.log import get_logger
from welding_ai.core.tuning import apply_tuned_thresholds
from welding_ai.orchestration.harness import Harness

log = get_logger("bootstrap")


# Load config, apply any tuned thresholds, and construct the Harness pipeline.
def build_harness(cfg=None):
    if cfg is None:
        cfg = Config.load()
        if os.environ.get("WELD_GROUNDED") is None and not cfg.grounded:
            cfg.grounded = True                          # the application always has the model write the answer; the evaluation builds its own Harness
    tuned = apply_tuned_thresholds()
    h = Harness(cfg=cfg)
    log.info("%d passages / %d documents / %d FAQs, mode=%s%s", h.index.n, len(h.sources), len(h.faqs), cfg.mode, ", tuned thresholds" if tuned else "")
    return h
