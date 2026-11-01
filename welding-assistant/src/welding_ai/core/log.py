"""Structured-ish logging with a request id, to stderr. One call: `get_logger("name")`."""
import logging
import os
import sys

_configured = False


# Return a "welding.<name>" logger, configuring stderr logging once on first use.
def get_logger(name):
    global _configured
    if not _configured:
        logging.basicConfig(stream=sys.stderr, level=os.environ.get("WELD_LOG_LEVEL", "INFO").upper(),
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        _configured = True
    return logging.getLogger("welding." + name)
