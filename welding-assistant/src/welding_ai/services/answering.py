"""Answer service: plan -> answer blocks built from the documents.   python -m welding_ai.services.answering --port 8802"""
import argparse

from welding_ai.answering.engine import Engine
from welding_ai.retrieval import retrieval
from welding_ai.services import rpc


# RPC route table exposing the Engine's answer/topic/clarify/related methods over JSON.
def routes(engine):
    # Serialize an Answer (or None) to a JSON-able dict.
    def as_dict(ans):
        return ans.to_dict() if ans else None
    return {
        "answer": lambda b: as_dict(engine.answer(b["plan"])),
        "topic": lambda b: as_dict(engine.topic(b["plan"])),
        "clarify": lambda b: engine.clarify(b["plan"]),
        "related": lambda b: engine.related(b["plan"], b.get("k", 3)),
    }


# Start the standalone answering service (distributed mode).
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8802)
    a = ap.parse_args()
    engine = Engine(retrieval.load())
    srv = rpc.make_server("answering", routes(engine), a.host, a.port)
    print(f"[answering] http://{a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
