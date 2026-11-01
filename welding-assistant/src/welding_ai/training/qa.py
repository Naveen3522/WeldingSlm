"""Question-and-answer pairs generated from new documents: every answer is a table cell quoted word for word, so nothing can be invented.

A row of a table ("Expulsion | Molten metal ejected ... | Reduce current ...") becomes one pair per fact: "What is the corrective action for Expulsion?" ->
the cell. The passage the row sits in is kept as the evidence the model will be shown. Questions are plain templates; no model writes anything here.
"""
import re

KEY_MIN, KEY_MAX, VALUE_MIN, VALUE_MAX = 2, 80, 4, 400
EVIDENCE_CHARS = 850
SKIP_KEY_HEADERS = re.compile(r"^(ch\.?|no\.?|nr\.?|#|step|order|code|ref|item|page|pages|id)$", re.I)
SKIP_VALUE_HEADERS = re.compile(r"^(ch\.?|chapter|chapters|page|pages|code|ref|source basis|edition|status)$", re.I)

# (words in the column heading, the question about the row's first cell)
TEMPLATES = [
    (re.compile(r"^(meaning|definition|description|what it means|what it is)$", re.I), "What does {key} mean?"),
    (re.compile(r"corrective|remedy|\baction\b|\bfix\b|countermeasure", re.I), "What is the corrective action for {key}?"),
    (re.compile(r"cause|mechanism", re.I), "What causes {key}?"),
    (re.compile(r"detect|discriminat", re.I), "How is {key} detected or told apart from similar problems?"),
    (re.compile(r"why it matters|^why", re.I), "Why does {key} matter?"),
    (re.compile(r"\brole\b|purpose|how it is used|\buse\b", re.I), "What is the role of {key}?"),
]


def _question(key, column, title):
    for pattern, template in TEMPLATES:
        if pattern.search(column):
            q = template.format(key=key)
            break
    else:
        q = f'What does the documentation list under "{column.strip()}" for {key}?'          # reads correctly whatever the column is called
    return q[:-1] + f" (in {title})?" if len(key) <= 12 and title else q


def _clean(text):
    return re.sub(r"\s+", " ", text).strip()


def _evidence(table, row, chunks):
    """The passage the row sits in: the table's chunk that holds it, cut around the row's key. -> (chunk id, text)."""
    md = "| " + " | ".join(c.replace("|", "/") for c in row) + " |"
    for cid in table.get("chunk_ids") or []:
        if md in chunks[cid]["text"]:
            return cid, chunks[cid]["text"]
    cid = (table.get("chunk_ids") or [None])[0]
    return (cid, chunks[cid]["text"]) if cid is not None else (None, "")


def generate(tables, chunks, documents, focus):
    """Pairs for the tables of the named documents. `focus(text, query, max_chars)` trims a passage around the question (the index's own).
    -> [{"doc", "section", "question", "answer", "evidence", "kind"}], without duplicates."""
    wanted, seen, out = set(documents), set(), []
    for table in tables:
        if table["source"] not in wanted or len(table["header"]) < 2 or SKIP_KEY_HEADERS.match(table["header"][0].strip()):
            continue
        title = table.get("title") or ""
        for row in table["rows"]:
            key = _clean(row[0])
            if not KEY_MIN <= len(key) <= KEY_MAX or key.isdigit() or key in ("-", "—"):
                continue
            cid, passage = _evidence(table, row, chunks)
            if cid is None:
                continue
            for column, cell in zip(table["header"][1:], row[1:]):
                value = _clean(cell)
                if not VALUE_MIN <= len(value) <= VALUE_MAX or SKIP_VALUE_HEADERS.match(column.strip()) or value in ("-", "—"):
                    continue
                question = _question(key, column, title)
                if (question, value) in seen:
                    continue
                evidence = focus(passage, key + " " + value, EVIDENCE_CHARS)
                if value.replace("|", "/").lower() not in _clean(evidence).lower():          # an answer the shown passage does not contain would teach the model to invent
                    continue
                seen.add((question, value))
                out.append({"doc": table["source"], "section": table["heading"].split(" > ")[-1], "question": question, "answer": value, "evidence": evidence, "kind": "table"})
    asked = {}
    for p in out:
        asked.setdefault((p["doc"], p["question"]), set()).add(p["answer"])
    return [p for p in out if len(asked[(p["doc"], p["question"])]) == 1]       # a question with two different answers in one document would teach the model to guess
