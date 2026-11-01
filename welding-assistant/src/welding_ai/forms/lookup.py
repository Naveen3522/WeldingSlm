"""Chat and drafts: a question is answered from the filled cells of the drafts when it names a draft, and any other answer says when a draft holds something about the question.

    DraftLookup(forms).claim(question)     -> the events of the answer when the question is about drafts or names one, else None (the ordinary pipeline answers it)
    DraftLookup(forms).advice(question)    -> one line for the end of an ordinary answer ("2 drafts have a Gap we leave entered: ..."), or ""

Nothing is invented and no model is used: a cell is quoted as it was entered, with the draft it came from. A draft is a person's own entry, so it is never mixed into an answer from the
documents; it is only offered beside it. A field matches a question when every word of its name (the first column) is in the question and the draft has a value for it.
"""
import re

from welding_ai.core import events
from welding_ai.orchestration import provenance

MAX_DRAFTS = 100                     # the newest this many are searched
MAX_SHOWN = 3
STOP = frozenset("a an the of in on at for to is are was do does did we our my i you your what whats which how much many it this that with and or from any have has there draft drafts".split())
ASKS_LIST = re.compile(r"\b(which|what|list|show|any|my|all|are there|have)\b", re.I)


def _words(text):
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1}


def _answer(text, *extra, route="drafts"):
    done = events.done({"total_s": 0.0}, route=route, grounded=True, confidence="high", match="good", answer_source=route)
    done["provenance"] = provenance.describe(route)
    return [events.no_sources(route, [], match="none"), events.token(text), *extra, done]


class DraftLookup:
    def __init__(self, forms):
        self.forms = forms

    def _all(self):
        return self.forms.list_drafts(0, MAX_DRAFTS)["drafts"]

    @staticmethod
    def _cells(draft, question_words):
        """-> [(row index, field, [(column, value)])]: the rows whose field name is in the question and that hold a value."""
        hits = []
        for i, row in enumerate(draft["rows"]):
            field = str(row[0]).strip()
            mine = _words(field)
            asked = [c for c in range(1, len(draft["heads"])) if _words(draft["heads"][c]) and _words(draft["heads"][c]) <= question_words]       # a column named in the question ("the Remarks for ...") is the only one shown
            values = [(draft["heads"][c] if c < len(draft["heads"]) else "", str(v).strip()) for c, v in enumerate(row) if c > 0 and str(v).strip() and (not asked or c in asked)]
            if mine and mine <= question_words and values:
                hits.append((i, field, values))
        return hits

    def _named(self, question, drafts):
        low = " " + re.sub(r"[^a-z0-9]+", " ", question.lower()).strip() + " "
        found = [d for d in drafts if d["name"].strip() and " " + re.sub(r"[^a-z0-9]+", " ", d["name"].lower()).strip() + " " in low]
        return max(found, key=lambda d: len(d["name"])) if found else None

    def claim(self, question):
        text = question.strip()
        talks = bool(re.search(r"\bdrafts?\b", text, re.I))
        drafts = self._all()
        named = self._named(text, drafts)
        if named is None and not talks:
            return None
        if named is None:
            if not ASKS_LIST.search(text):
                return None
            if not drafts:
                return _answer("There is no draft yet. Start one on the Drafts page.")
            rows = [[d["name"], d["template"] or "", d["by"] or "", f"{d['filled']} of {d['total']}"] for d in drafts[:20]]
            return _answer(f"There {'is' if len(drafts) == 1 else 'are'} {len(drafts)} draft{'' if len(drafts) == 1 else 's'}, the newest first. Ask about one by name.",
                           events.table("Drafts", "", 0, ["Draft", "Template", "By", "Filled"], rows))
        full = self.forms.get_draft(named["id"])
        cells = self._cells(full, _words(text))
        if cells:
            lines = [f"{field}: " + "; ".join(f"{head} {value}" if head else value for head, value in values) for _, field, values in cells]
            return _answer(f"From the draft \"{full['name']}\"" + (f" (template {full['template']})" if full["template"] else "") + ", as it was entered. " + " ".join(lines) + ".",
                           events.table(full["name"], "", 0, full["heads"], full["rows"]))
        return _answer(f"This is the draft \"{full['name']}\"" + (f", made from the template {full['template']}" if full["template"] else "") + f". {full['filled']} of {full['total']} cells are filled in.",
                       events.table(full["name"], "", 0, full["heads"], full["rows"]))

    def advice(self, question):
        words = _words(question)
        if not words:
            return ""
        found = []
        for d in self._all():
            full = self.forms.get_draft(d["id"])
            hits = self._cells(full, words)
            if hits:
                found.append((d["name"], hits[0][1]))
        if not found:
            return ""
        shown = "; ".join(f"\"{name}\" ({field})" for name, field in found[:MAX_SHOWN])
        more = f" and {len(found) - MAX_SHOWN} more" if len(found) > MAX_SHOWN else ""
        return (f"\n\nYou also have {len(found)} draft{'' if len(found) == 1 else 's'} with this entered: {shown}{more}. Draft entries are people's own values, not the documents'. "
                f"Ask \"show the draft <name>\" to see one.")
