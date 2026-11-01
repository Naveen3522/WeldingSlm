"""The gateway: one table of routes, and one place that finds the route for a request, checks the person's rights and calls it.

A route is a function with a decorator:

    @route("GET", "/api/activity", right="activity", doc="Everyone's questions and answers, newest first.")
    def activity(req):
        offset, limit = req.page()                     # ?offset=0&limit=50  (limit is capped)
        return app.activity.audit_page(offset=offset, limit=limit, user=req.arg("user"), text=req.arg("q"))

    @route("POST", "/api/formulas/{id}/calculate")     # {id} arrives in req.params["id"]
    def calculate(req): ...

What the gateway does for every registered route, so a route function does not repeat it:
  * `/api/v1/...` and `/api/...` are the same route (v1 is the address to give to anyone who builds on the API);
  * the person must be signed in (auth_routes.handle runs first and refuses everyone else), and `right=` (a name in accounts/permissions.py) must be allowed;
  * the query string, the JSON body and the path parameters are on `req`; the return value is sent as JSON (200), or raise `ApiError(code, message)`;
  * `GET /api/v1/routes` lists every registered route with its right and its doc, so the API documents itself.

Routes that are not registered yet are still answered by the older `*_routes.handle` functions in server.py; move one over by giving it a decorator.
"""
import re
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import parse_qs

from welding_ai.accounts import levels

ROUTES = []
app = SimpleNamespace()                       # the services the routes use (app.activity, ...), filled in by server.py


class ApiError(Exception):
    def __init__(self, status, message, code=""):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


@dataclass
class Route:
    method: str
    pattern: str
    regex: re.Pattern
    fn: object
    right: str
    doc: str
    query: str


class Request:
    """What a route function gets: the handler (for sending files), the person, the path parameters, the query string and the JSON body."""

    def __init__(self, handler, method, path, params, query, body):
        self.handler, self.method, self.path, self.params, self.body = handler, method, path, params, body or {}
        self.user = handler.user
        self._query = query

    def arg(self, name, default=""):
        values = self._query.get(name)
        return values[0].strip() if values else default

    def int(self, name, default, low=0, high=None):
        raw = self.arg(name, "")
        try:
            value = int(raw) if raw != "" else default
        except ValueError:
            raise ApiError(400, f"{name} must be a number.", "bad_number")
        value = max(low, value)
        return min(high, value) if high is not None else value

    def page(self, default=50, high=200):
        """-> (offset, limit) from ?offset=&limit=; the limit never exceeds `high`."""
        return self.int("offset", 0), self.int("limit", default, low=1, high=high)

    def send_bytes(self, code, data, ctype, extra=None):
        self.handler.send_bytes(code, data, ctype, extra)
        return None


def route(method, pattern, right=None, doc="", query=""):
    """Registers the function under `method` and `pattern` ({name} stands for one path segment). `query` names the query parameters, for the route list."""
    regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def register(fn):
        text = doc or ((fn.__doc__ or "").strip().splitlines() or [""])[0]
        ROUTES.append(Route(method.upper(), pattern, regex, fn, right or "", text, query))
        return fn
    return register


def versioned(path):
    """/api/v1/x -> /api/x: both addresses reach the same route."""
    return "/api/" + path[len("/api/v1/"):] if path.startswith("/api/v1/") else path


def find(method, path):
    for r in ROUTES:
        m = r.regex.match(path)
        if m and r.method == method:
            return r, m.groupdict()
    return None


def dispatch(handler, method, path, query_string, body):
    """Answers the request if a registered route matches it (-> True); otherwise -> False and the older handlers get their turn."""
    found = find(method, path)
    if found is None:
        return False
    r, params = found
    user = handler.user
    if user is None:
        handler.send_json({"error": "Sign in to continue."}, 401)
        return True
    if r.right and not levels.allowed(user["level"], r.right):
        handler.send_json({"error": "You do not have access to this.", "code": "forbidden"}, 403)
        return True
    try:
        result = r.fn(Request(handler, method, path, params, parse_qs(query_string or ""), body))
    except ApiError as e:
        handler.send_json({"error": e.message, **({"code": e.code} if e.code else {})}, e.status)
        return True
    if result is not None:
        handler.send_json(result)
    return True


@route("GET", "/api/agents/options", right="agents", doc="The choices in the Agents page dropdowns, read from the documents at the last reindex.")
def agent_options(req):
    return app.agent_options()


@route("GET", "/api/routes", doc="Every registered route: method, address, the right it needs and what it does.")
def routes(req):
    return {"routes": [{"method": r.method, "path": "/api/v1" + r.pattern[len("/api"):], "right": r.right, "query": r.query, "doc": r.doc} for r in sorted(ROUTES, key=lambda r: (r.pattern, r.method))]}
