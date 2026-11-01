"""Understanding service: question -> plan (intent, layout, parts, entity).   python -m welding_ai.services.understanding --port 8801"""
import argparse

from welding_ai.services import rpc
from welding_ai.understanding import planner

ROUTES = {"analyze": lambda body: planner.analyze(body["question"])}


# Start the standalone understanding service (distributed mode).
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8801)
    a = ap.parse_args()
    srv = rpc.make_server("understanding", ROUTES, a.host, a.port)
    print(f"[understanding] http://{a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
