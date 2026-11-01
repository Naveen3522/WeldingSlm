"""Minimal JSON-over-HTTP plumbing for the optional separate services (standard library only).

A service is a dict of routes (name -> function(dict) -> JSON-able); every service also answers GET /health.
"""
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from welding_ai.core.log import get_logger

log = get_logger("rpc")


class RpcError(Exception):
    pass


# Build a ThreadingHTTPServer that dispatches POST /<route> to routes[route] and serves GET /health.
def make_server(name, routes, host="127.0.0.1", port=0):
    class Handler(BaseHTTPRequestHandler):
        # Silence the default per-request stderr logging (we log via the `log` logger instead).
        def log_message(self, fmt, *args):
            pass

        # Write a JSON response body with the right headers.
        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # GET /health -> service name, status and available routes.
        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"service": name, "status": "ok", "routes": sorted(routes)})
            self._send(404, {"error": "not found"})

        # POST /<route> -> call the matching route function with the JSON body, log timing, return its result.
        def do_POST(self):
            fn = routes.get(self.path.strip("/"))
            if fn is None:
                return self._send(404, {"error": "unknown route"})
            rid = self.headers.get("X-Request-Id") or uuid.uuid4().hex[:8]
            t0 = time.time()
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                out = fn(body)
                log.info("%s /%s rid=%s %.1f ms", name, self.path.strip("/"), rid, (time.time() - t0) * 1000)
                self._send(200, {"result": out})
            except Exception as e:
                log.warning("%s /%s rid=%s failed: %s", name, self.path.strip("/"), rid, e)
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


# Start a service's HTTP server on a background daemon thread; returns (server, bound_port).
def start_in_thread(name, routes, host="127.0.0.1", port=0):
    srv = make_server(name, routes, host, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# POST a JSON payload to a remote service route and return its "result" field.
def call(url, route, payload, timeout=30, request_id=None):
    req = urllib.request.Request(url.rstrip("/") + "/" + route, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "X-Request-Id": request_id or uuid.uuid4().hex[:8]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["result"]
    except urllib.error.HTTPError as e:
        raise RpcError(f"{route}: {e.code} {e.read()[:200]!r}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RpcError(f"{route}: service unreachable ({e})") from e


# Call a remote service's /health endpoint.
def health(url, timeout=3):
    with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as r:
        return json.loads(r.read())
