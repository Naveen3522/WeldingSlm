"""Ask works with templates: it lists them, explains one, makes a Word file from one, and creates a new one from rows the person names.

    TemplateAsk.claim(question, allowed)  -> the events of the answer when the question is about templates, else None (the ordinary pipeline answers it)
    handle(handler, forms, method, path, body)
        POST /api/ask/table.docx {name, heads, rows, template_id?}   a checked table as a Word file (right 1.4); with template_id the rows must still be that template's

What a question can ask (nothing is invented, no model is used):
    "which templates are there"                                   the list
    "what is the Spot welding WPS template"                       the table and a line about it
    "make a Word file from the Spot welding WPS template"         asks how to fill it, or with: "with defaults" (as saved), "blank" (field names only),
                                                                  "ask me each row" (the page asks row by row, columns of a row separated by semicolons)
    "create a template with rows Electrode force, Weld time"      a new Field / Value table, checked, offered as Word (it is not saved: adding it to the Library is a separate step)
A template's first column holds the field names; a cell with text is fixed and an empty cell is what a person fills in.
"""
import re

from welding_ai.accounts import levels
from welding_ai.core import events
from welding_ai.forms.docx_table import write_docx
from welding_ai.forms.store import FormError, check_grid
from welding_ai.orchestration import provenance

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GENERATE = re.compile(r"\b(generate|make|create|prepare|produce|build|give me|download|fill(?: in| out)?)\b", re.I)
FILE = re.compile(r"\b(word|docx|document|file|form|sheet|copy)\b", re.I)
EXPLAIN = re.compile(r"\b(what|explain|describe|show|tell|fields?|rows?|contain|contains|about|look like)\b", re.I)
LISTING = re.compile(r"\b(which|what|list|show|available|are there|have)\b", re.I)
CREATE = re.compile(r"\b(create|make|build|design|new)\b.*\btemplate\b|\btemplate\b.*\b(create|make|build|design)\b", re.I)
ROWS_AFTER = re.compile(r"\b(?:with|containing|rows?|fields?)\b[:\s]+(.+)$|:\s*(.+)$", re.I | re.S)
MODES = (("blank", re.compile(r"\b(blank|empty)\b", re.I)), ("defaults", re.compile(r"\bdefaults?\b", re.I)),
         ("ask", re.compile(r"\b(ask me|step by step|one by one|each (?:row|field|column)|every (?:row|field)|row by row|questions?)\b", re.I)))
NOTE = "Filled in from the chat. Check every value before you use it."
MAX_OPTIONS = 6


def _answer(text, *extra, route="template"):
    """The events of one reply: text, whatever else, and the done event with the "From a template" label."""
    done = events.done({"total_s": 0.0}, route=route, grounded=True, confidence="high", match="good", answer_source="template")
    done["provenance"] = provenance.describe("template")
    return [events.no_sources(route, [], match="none"), events.token(text), *extra, done]


def _fillable(rows):
    return sum(1 for row in rows for cell in row[1:] if not cell.strip())


class TemplateAsk:
    def __init__(self, forms):
        self.forms = forms

    # The template whose whole name is in the question (the longest such name), else None.
    def _named(self, question):
        low = " " + re.sub(r"\s+", " ", question.lower()) + " "
        found = [t for t in self.forms.list_templates() if " " + t["name"].lower() + " " in re.sub(r"[?.!,]", " ", low) + " "]
        return max(found, key=lambda t: len(t["name"])) if found else None

    def claim(self, question, allowed):
        text = question.strip()
        mentions = bool(re.search(r"\btemplates?\b", text, re.I))
        named = self._named(text)
        if not (mentions or (named and GENERATE.search(text))):
            return None
        make = bool(GENERATE.search(text)) and (bool(FILE.search(text)) or mentions)
        if CREATE.search(text) and not named:
            return self._create(text, allowed)
        if make and (named or mentions):
            if not allowed:
                return _answer("You don't have access to make Word files from templates.", route="template_denied")
            return self._generate(text, named)
        if named and EXPLAIN.search(text):
            return self._explain(named)
        if mentions and LISTING.search(text):
            return self._list()
        return None

    # ---- the pieces
    def _list(self):
        templates = self.forms.list_templates()
        if not templates:
            return _answer("There are no templates yet. A Weld Engineer or Admin adds them on the Drafts page.")
        rows = [[t["name"], str(t["fields"])] for t in templates]
        return _answer(f"There {'is' if len(rows) == 1 else 'are'} {len(rows)} template{'' if len(rows) == 1 else 's'}. Ask about one by name, or ask for a Word file from it.",
                       events.table("Templates", "", 0, ["Template", "Rows"], rows))

    def _explain(self, named):
        template = self.forms.get_template(named["id"])
        rows = template["rows"]
        left = _fillable(rows)
        text = (f"The {template['name']} template has {len(rows)} row{'' if len(rows) == 1 else 's'} with the columns {', '.join(template['heads'])}. "
                + (f"{left} cell{'' if left == 1 else 's'} {'is' if left == 1 else 'are'} left for you to fill in." if left else "Every cell already has a value."))
        return _answer(text, events.table(template["name"], "", 0, template["heads"], rows))

    def _generate(self, text, named):
        if named is None:
            templates = self.forms.list_templates()
            if not templates:
                return _answer("There are no templates yet, so there is nothing to make a Word file from.")
            options = [{"label": t["name"], "query": f"Make a Word file from the {t['name']} template"} for t in templates[:MAX_OPTIONS]]
            return _answer("Which template do you want a Word file from?", events.clarify(options))
        mode = next((name for name, pattern in MODES if pattern.search(text)), "")
        if not mode:
            name = named["name"]
            options = [{"label": "With the values it has", "query": f"Make a Word file from the {name} template with defaults"},
                       {"label": "Blank", "query": f"Make a Word file from the {name} template blank"},
                       {"label": "Ask me each row", "query": f"Make a Word file from the {name} template and ask me each row"}]
            return _answer(f"How should the {name} template be filled?", events.clarify(options))
        template = self.forms.get_template(named["id"])
        heads, rows = template["heads"], template["rows"]
        if mode == "ask":
            asked = [r for r in rows if any(not c.strip() for c in r[1:])]
            if not asked:
                mode = "defaults"
            else:
                return _answer(f"I will ask for each empty cell of the {template['name']} template, row by row. Columns of one row go in one answer, separated by semicolons.",
                               {"type": "template_fill", "template_id": template["id"], "name": template["name"], "heads": heads, "rows": rows}, route="template_fill")
        if mode == "blank":
            rows = [[r[0]] + [""] * (len(r) - 1) for r in rows]
        label = "Word file, blank" if mode == "blank" else "Word file, with the values it has"
        return _answer(f"The {template['name']} template is ready as a Word file" + (" with only the field names." if mode == "blank" else ", as it is saved."),
                       {"type": "template_file", "template_id": template["id"], "name": template["name"], "heads": heads, "rows": rows, "label": label})

    def _create(self, text, allowed):
        if not allowed:
            return _answer("You don't have access to make Word files from templates.", route="template_denied")
        m = ROWS_AFTER.search(text)
        raw = re.sub(r"^(?:rows?|fields?|named|called)\s+", "", ((m.group(1) or m.group(2)) if m else "").strip(), flags=re.I)
        names = [re.sub(r"\s+", " ", p).strip(" .?!") for p in re.split(r"[,;]|\band\b", raw) if p.strip(" .?!")]
        if not names:
            return _answer("Which rows should the template have? Write them after \"with\", separated by commas, for example: create a template with rows Electrode force, Weld time.")
        heads, rows = ["Field", "Value"], [[n, ""] for n in names]
        try:
            heads, rows = check_grid(heads, rows)
        except FormError as e:
            return _answer("The template was not made: " + e.message, route="template_invalid")
        title = "New template"
        return _answer(f"A new template with {len(rows)} row{'' if len(rows) == 1 else 's'}, checked: every row has a name and no name is used twice. It is not saved in the app; download it, then add it to the Library or upload it in the template editor.",
                       events.table(title, "", 0, heads, rows), {"type": "template_file", "template_id": None, "name": title, "heads": heads, "rows": rows, "label": "Word file"})


def handle(handler, forms, method, path, body):
    if path != "/api/ask/table.docx":
        return False
    if method != "POST":
        handler.send_bytes(404, b"not found", "text/plain")
        return True
    if not levels.allowed(handler.user["level"], "template_ask"):
        handler.send_json({"error": "You don't have access to make Word files from templates."}, 403)
        return True
    body = body or {}
    try:
        heads, rows = check_grid(body.get("heads"), body.get("rows"))
        if body.get("template_id") is not None:
            template = forms.get_template(int(body["template_id"]))
            same = heads == template["heads"] and len(rows) == len(template["rows"]) and all(
                new[0] == old[0] and all(not o.strip() or n == o for n, o in zip(new[1:], old[1:])) for new, old in zip(rows, template["rows"]))
            if not same:
                raise FormError("not_this_template", "The table does not match the template: its fields and its fixed cells cannot change.")
    except (FormError, TypeError, ValueError) as e:
        handler.send_json({"error": getattr(e, "message", "Send the template and its rows.")}, 400)
        return True
    title = str(body.get("name") or "Template")[:120]
    name = (re.sub(r"[^A-Za-z0-9._ -]+", "", title).strip() or "table")[:80] + ".docx"
    handler.send_bytes(200, write_docx(title, heads, rows, NOTE), DOCX, {"Content-Disposition": f"attachment; filename=\"{name}\""})
    return True
