# Weld Assist

A welding assistant that answers only from your own Word documents. It runs on your computer: no cloud service, nothing is sent out.

- **Chat** answers a question from the documents and shows the passages it used.
- **Agents** (Troubleshooter, Procedure Advisor) build a question from a few choices and send it to the chat.
- **Formulas** lists every formula found in the documents, with a calculator for each.
- **Library** lists the documents; add, replace or delete them, then press **Reindex**.
- **Drafts** fills in forms (WPS and similar) from templates.
- **Admin pages** (Users, Model management, Activity) are for administrators.

How an answer is made, and the checks before and after it, is in [AI_HARNESS.md](AI_HARNESS.md).

## Run it

1. Install [Ollama](https://ollama.com) and build the model (top-level README, steps 2 and 4).
2. `pip install -r requirements.txt`
3. Create the first administrator once: `python scripts/create_admin.py` (or `--ask` to type your own details).
4. Start: double-click `start.bat`, or `make run`. The app opens at <http://127.0.0.1:8765/>.

## Your documents

Put the `.docx` files in the documents folder (`WELD_DOCS_DIR`, set in `src/welding_ai/paths.py`), or use **Library → Add documents**. Press **Reindex** after any change.
Every reindex starts from the documents as they are now, so nothing has to be edited by hand:

1. The documents are read into passages, tables and sections.
2. Each passage is typed (formula, troubleshooting, safety, procedure, standards, reference) and each document goes to the agent whose passages it holds.
   The result is written to `data/index/agents.json`.
3. Formulas are read from the passages and checked; a formula whose document was deleted or changed leaves the list.
4. The question pairs for training are made again.

## What is where

```
welding-assistant/
├── start.bat              start the app on Windows
├── Makefile               run, test, index, lint shortcuts
├── requirements.txt       Python packages
├── config.example.json    copy to config.json to change settings
├── AI_HARNESS.md          how an answer is made and checked
├── apps/
│   ├── api/               the web server (server.py), the gateway (gateway.py) and one file of routes per area
│   └── web/               the pages: pages/ (HTML), css/, js/, vendor/ (the icon font)
├── src/welding_ai/        the application code
│   ├── orchestration/     the answer pipeline: guards, agents, grounded answering, checks
│   ├── retrieval/         search over the passages (keywords and closeness in meaning)
│   ├── understanding/     reads a question: what is asked, in what layout, in how many parts
│   ├── answering/         builds and checks answers from the passages
│   ├── knowledge/         facts of the documents, passage types, which document feeds which agent
│   ├── ingestion/         reads the documents and rebuilds the index
│   ├── formulas/          the formula registry, the calculator, and reading formulas from documents
│   ├── agents/            Troubleshooter and Procedure Advisor, spot-welding values, WPS drafts
│   ├── accounts/          users, roles, sessions, passwords
│   ├── activity/          chat history, feedback, the audit log
│   ├── forms/             templates and drafts
│   ├── llm/               the model client (Ollama) and model management
│   ├── retrain/ training/ optional fine-tuning of the model on question pairs
│   └── services/ system/  separate-process mode, and the system check
├── data/                  everything the app creates: database, index, settings (not in git)
│   ├── formulas/documents.json   hand-checked formulas; each stays while its quoted line is in the documents
│   └── settings/                 permissions.xml and account rules, editable
├── scripts/create_admin.py       the first administrator
├── evaluation/                   tuned thresholds the app reads
└── tests/                        automated tests: `make test`
```

## Addresses and the API

Pages have plain addresses: `http://localhost:8765/library`, `/formulas`, `/agents`, `/drafts`, `/admin/model`, `/admin/users`, `/admin/activity` (the `.html` forms still work).
The API sits under `/api/v1/` (the plain `/api/` forms reach the same routes):

| Address | What it does |
|---|---|
| `POST /api/v1/chat` | ask a question; the answer streams back as lines of JSON |
| `GET /api/v1/audit?user=&q=&area=&verdict=&offset=0&limit=50` | Activity list: filters are query parameters, `offset` and `limit` page it (the limit is capped at 200) |
| `GET /api/v1/audit/export.xlsx?user=&q=` | the same rows as an Excel file |
| `GET /api/v1/formulas`, `POST /api/v1/formulas/{id}/calculate` | the formulas and their calculator |
| `GET /api/v1/routes` | every registered route with the right it needs and what it does |

Every API call needs a signed-in person; the gateway refuses everyone else with 401, and a person without the route's right with 403.

**Adding a route** takes one function in a `*_routes.py` file in `apps/api/`; nothing in `server.py` changes:

```python
from gateway import ApiError, app, route

@route("GET", "/api/widgets/{id}", right="activity", query="verbose", doc="One widget.")
def widget(req):
    return app.widgets.get(req.params["id"], verbose=req.arg("verbose"))      # returned data is sent as JSON; raise ApiError(404, "No such widget.") for errors
```

`req` carries `params` (from the address), `arg()` and `int()` (the query string), `page()` (offset and limit), `body` (the JSON), and `user`. The Activity routes (`activity_routes.py`) are the worked example.
The other route files still use their older `handle(...)` functions and are being moved over one at a time.

## Tests

`make test` (or `python -m pytest tests`). Nothing in the tests needs the model to be running.
