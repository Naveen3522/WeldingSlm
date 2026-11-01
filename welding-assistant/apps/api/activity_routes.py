"""History, feedback and the audit log, as registered routes (see gateway.py). Every one needs a signed-in person; the audit routes also need the "activity" right.

    GET  /api/v1/history?area=                 your recent questions (the History panel), optionally only those one agent answered
    POST /api/v1/history/clear                 hide your questions from the menu (the audit log keeps them)
    POST /api/v1/feedback                      {log_id, score: 1 | -1, comment} about one of your own answers
    GET  /api/v1/audit?user&q&verdict&area&offset&limit     everyone's questions and answers, newest first, a page at a time
    GET  /api/v1/audit/export.xlsx?user&q&verdict&area      the same, every matching row, as an Excel file
    GET  /api/v1/audit/export.csv?user&q&verdict&area       the same as a CSV file
    POST /api/v1/audit/clear                   {days | user, q, area, verdict | everything, dry_run}  delete entries (Admin only); dry_run says how many it would delete
    GET  /api/v1/activity/summary              totals for the header line
"""
from welding_ai.accounts import levels
from welding_ai.activity.service import ActivityError, to_csv, to_xlsx

from welding_ai.core.log import get_logger

from gateway import ApiError, app, route

log = get_logger("api.activity")

STATUS = {"not_found": 404, "bad_score": 400}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FILTERS = "user, q, verdict, area"


def _filters(req):
    return {"user": req.arg("user"), "text": req.arg("q"), "verdict": req.arg("verdict"), "area": req.arg("area")}


@route("GET", "/api/history", query="area", doc="Your recent questions, optionally only those one agent answered.")
def history(req):
    if not levels.allowed(req.user["level"], "history"):
        return {"questions": []}                                  # a Viewer has no History list
    items = app.activity.history_items(req.user["username"], req.user["email"], req.arg("area"))
    return {"questions": [item["question"] for item in items], "items": items}


@route("POST", "/api/history/clear", doc="Hide your questions from the menu (the audit log keeps them).")
def clear_history(req):
    if levels.allowed(req.user["level"], "history"):
        app.activity.clear_history(req.user["username"], req.user["email"])
    return {"ok": True}


@route("POST", "/api/feedback", doc="Rate one of your own answers: log_id, score (1 or -1), comment.")
def feedback(req):
    try:
        log_id, score = int(req.body.get("log_id")), int(req.body.get("score"))
    except (TypeError, ValueError):
        raise ApiError(400, "Send log_id and score.")
    try:
        app.activity.add_feedback(req.user["username"], log_id, score, str(req.body.get("comment") or ""), req.user["email"])
    except ActivityError as e:
        raise ApiError(STATUS.get(e.code, 400), e.message, e.code)
    return {"ok": True}


@route("GET", "/api/audit", right="activity", query=FILTERS + ", offset, limit", doc="Everyone's questions and answers, newest first, a page at a time.")
def audit(req):
    offset, limit = req.page()
    return app.activity.audit_page(offset=offset, limit=limit, **_filters(req))


@route("GET", "/api/audit/export.xlsx", right="activity", query=FILTERS, doc="Every matching row as an Excel file.")
def export_xlsx(req):
    return req.send_bytes(200, to_xlsx(app.activity.export_rows(**_filters(req))), XLSX, {"Content-Disposition": 'attachment; filename="weld-assist-activity.xlsx"'})


@route("GET", "/api/audit/export.csv", right="activity", query=FILTERS, doc="Every matching row as a CSV file.")
def export_csv(req):
    return req.send_bytes(200, to_csv(app.activity.export_rows(**_filters(req))).encode("utf-8"), "text/csv; charset=utf-8",
                          {"Content-Disposition": 'attachment; filename="weld-assist-audit.csv"'})


@route("POST", "/api/audit/clear", right="clear_activity", doc="Delete audit entries older than some days, or those the filters match, or all (Admin only). dry_run only counts.")
def clear(req):
    days = req.body.get("days")
    try:
        days = None if days in (None, "") else float(days)
    except (TypeError, ValueError):
        raise ApiError(400, "days must be a number.")
    filters = {"user": str(req.body.get("user", "")).strip(), "text": str(req.body.get("q", "")).strip(), "verdict": str(req.body.get("verdict", "")).strip(), "area": str(req.body.get("area", "")).strip()}
    everything = req.body.get("everything") is True
    dry = req.body.get("dry_run") is True
    if days is None and not any(filters.values()) and not everything:
        raise ApiError(400, "Say what to delete: entries older than some days, those a search matches, or everything.")
    count = app.activity.clear(older_than_days=days, everything=everything, dry_run=dry, **filters)
    if not dry:
        log.info("activity cleared by %s: %d entries (days=%s, filters=%s, everything=%s)", req.user["username"], count, days, {k: v for k, v in filters.items() if v}, everything)
    return {"deleted": 0 if dry else count, "would_delete": count if dry else 0}


@route("GET", "/api/activity/summary", right="activity", doc="Totals for the Activity header line.")
def summary(req):
    return app.activity.usage()
