"""Plain-English rewrite of an engine answer, in the shape the question asked for (see AI_HARNESS.md, appendix).

Reads the plan (intent / format / length) and the Answer the engine built from the retrieved chunks; never touches retrieval.
`compose()` is the rule-based rewrite (no model). `validate()` checks any rewrite, rule-based or model, against the chunks:
every number and acronym must be in them, the words must be supported, the asked shape must be present, and the question's
own words must be covered. `parse_markdown()` turns a model's markdown reply into the same block form.
"""
import re

from welding_ai.answering import answering
from welding_ai.answering.evidence import clean, sentences
from welding_ai.retrieval import rag

_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")
_LABEL = re.compile(r"^(.{2,60}?)\s*(?::|\s[–—-]\s)\s*(.+)$")
_CITE = re.compile(r"\s*\[\s*S?\d+(?:\s*[,;]\s*S?\d+)*\s*\]")
_HEADING = re.compile(r"^\s*(?:#+\s|\*\*[^*]+\*\*(?:\s*·.*)?\s*$|\*[^*]+\*\s*$|\*{4}\s*$)")
_SEQ_HDR =re.compile(r"step|stage|order|sequence|phase|action", re.I)
CAP, CAP_DETAIL, CAP_BRIEF = 8, 14, 3
# phrases the composer adds itself; not checked against the chunks
SCAFFOLD = ("Follow these steps.", "Here is the list from the documents.", "Here is the table from the documents.",
            "Here is the answer as a table.", "Check these in order:",
            "The documents give these as separate items with details, not as a sequence of steps:")
_ACTION = set("""switch turn remove fit set check clean wait make open close press start stop select apply place insert adjust measure verify
replace connect disconnect install use keep allow record confirm inspect dress load run reduce increase then next first after finally before
begin ensure lower raise tighten loosen mount unmount align test compare review identify determine calculate choose pick add drop reset
repeat continue move position clamp release cool heat weigh note wear read write enter save cut grind polish""".split())


# True when a sentence reads like an instruction: it starts with an action word or a sequence word.
def _actionish(x):
    m = re.match(r"\W*([A-Za-z]+)", x)
    return bool(m) and m.group(1).lower() in _ACTION

# formal wording -> everyday wording; each pair keeps the meaning and every number, unit and name
_PLAIN = [
    (r"in order to", "to"), (r"prior to", "before"), (r"in the event (?:of|that)", "if"), (r"a number of", "several"),
    (r"shall", "must"), (r"approximately", "about"), (r"subsequently", "then"), (r"sufficient", "enough"),
    (r"additional", "extra"), (r"therefore", "so"), (r"whereas", "while"), (r"in accordance with", "following"),
    (r"with regard to", "about"),
]
_PLAIN = [(re.compile(r"\b" + p + r"\b", re.I), r) for p, r in _PLAIN]


# Everyday wording for a sentence; capitalisation of the replaced word is kept.
def simplify(s):
    s = _CITE.sub("", clean(s))
    s = re.sub(r"\s\u2014\s([^\u2014]{1,60}?)\s\u2014\s", r", \1, ", s)  # aside between em dashes -> commas
    for pat, rep in _PLAIN:
        s = pat.sub(lambda m, rep=rep: rep.capitalize() if m.group(0)[0].isupper() else rep, s)
    return " ".join(s.split())


# A sentence over 30 words is cut at its semicolons into shorter sentences.
def _short(s):
    if len(s.split()) <= 30 or "; " not in s:
        return [s]
    parts = [p.strip().rstrip(";") for p in s.split("; ") if p.strip()]
    return [p[0].upper() + p[1:] + ("" if p.endswith((".", "?", "!")) else ".") for p in parts]


_BOLD = re.compile(r"^\s*\*\*([^*]+?)\*\*\s*$")
_FIELD = re.compile(r"^\s*[-*•]\s+\*\*([^*]+?)\*\*:?\s*(.*\S)\s*$")


# (label, [(field, value), ...], number of lines used after line i) when line i starts a record ("**label**" then "- **Field:** value"), else None.
def _group_at(lines, i):
    m = _BOLD.match(lines[i])
    if not m:
        return None
    fields, j = [], i + 1
    while j < len(lines):
        f = _FIELD.match(lines[j])
        if not f:
            break
        fields.append((f.group(1).strip().rstrip(":"), f.group(2).strip()))
        j += 1
    return (m.group(1).strip(), fields, j - i - 1) if fields else None


def _units(ans):
    """The evidence as ordered units: ('item', text, numbered) / ('sent', text) / ('table', block) / ('group', label, [(field, value)])."""
    out = []
    for b in ans.blocks:
        if b["k"] == "table":
            out.append(("table", b))
            continue
        para = []
        lines = b["md"].split("\n")
        skip = 0
        for li, line in enumerate(lines):
            if skip:
                skip -= 1
                continue
            g = _group_at(lines, li)
            if g:
                out += [("sent", s) for s in sentences(" ".join(para))] if para else []
                para = []
                out.append(("group", g[0], g[1]))
                skip = g[2]
                continue
            m = _ITEM.match(line)
            if _HEADING.match(line) or re.match(r"\s*#\s*\|", line):          # a heading or a table header row is not content
                continue
            if line.count("|") >= 2:                                           # a table row the engine wrote as text
                cells = [c.strip() for c in line.strip().strip("|").split("|") if c.strip()]
                if cells and cells[0].isdigit():
                    cells = cells[1:]
                if cells:
                    out.append(("item", "; ".join(cells), False))
            elif m:
                out += [("sent", s) for s in sentences(" ".join(para))] if para else []
                para = []
                out.append(("item", m.group(1), bool(re.match(r"\s*\d", line))))
            elif line.strip():
                para.append(line.strip())
        if para:
            out += [("sent", s) for s in sentences(" ".join(para))]
    return out


def _chunk_tokens(ans, index):
    return [set(rag.tokens(index.chunks[c["chunk"]]["text"])) for c in ans.cites[:8]]


# " [n]" for the cited chunk that holds most of the sentence's words, else "".
def _tag(text, chunk_toks):
    toks = {t for t in rag.tokens(text) if not t.isdigit()}
    if not toks or not chunk_toks:
        return ""
    best = max(range(len(chunk_toks)), key=lambda i: len(toks & chunk_toks[i]))
    return f" [{best + 1}]" if len(toks & chunk_toks[best]) / len(toks) >= 0.6 else ""


def _row_line(header, row):
    cells = [c.strip() for c in row]
    if len(cells) == 1:
        return cells[0]
    rest = "; ".join(f"{h.strip()} {c}" if h.strip() and h.strip().lower() != "notes" else c for h, c in zip(header[1:], cells[1:]) if c)
    return f"{cells[0]}: {rest}" if rest else cells[0]


def _table_block(header, rows, src=None, title=""):
    b = {"k": "table", "title": title, "header": list(header), "rows": [list(r) for r in rows], "source": "", "chunk": None, "note": ""}
    if src:
        b.update(source=src.get("source", ""), chunk=src.get("chunk"))
    return b


def _cap(plan):
    return CAP_BRIEF if plan.get("length") == "brief" else CAP_DETAIL if plan.get("length") == "detail" else CAP


# The rule-based rewrite: {"blocks": [...], "how": "template", "fit": bool}, or None when the chunks give nothing to rewrite.
def compose(plan, ans, index):
    """Rewrite the engine's answer in plain English in the asked shape. `fit` is False when the evidence could not take the asked
    shape (steps asked, only options found): the reply then says so in one line and uses the closest shape."""
    units = _units(ans)
    if not units:
        return None
    ctoks = _chunk_tokens(ans, index)
    fmt, intent, cap = plan.get("format"), plan["intent"], _cap(plan)
    tables = [u[1] for u in units if u[0] == "table"]
    items = [u for u in units if u[0] == "item"]
    sents = [x for u in units if u[0] == "sent" for x in _short(simplify(u[1]))]
    bullets = [simplify(u[1]) for u in items]
    numbered = bool(items) and all(u[2] for u in items)
    fit, note, blocks = True, "", []

    def line(t):
        return t if re.search(r"\[\d+\]$", t) else t + _tag(t, ctoks)

    def group_lines(label, fields):
        head = f"**{simplify(label)}**" + _tag(label + " " + " ".join(v for _, v in fields), ctoks)
        return [head] + [f"- {h}: {simplify(v)}" for h, v in fields if v]

    def text(lines, lead=""):
        md = "\n".join(([lead] if lead else []) + lines)
        return [{"k": "text", "md": md}] if md else []

    def table_lines(t, n=cap):
        return [line(_row_line(t["header"], r)) for r in t["rows"][:n]]

    groups = [(u[1], u[2]) for u in units if u[0] == "group"]
    wide = [t for t in tables if len(t["header"]) >= 3]
    if not groups and wide and fmt != "table" and intent not in ("compare", "conflict"):
        t = wide[0]                                        # a table of records (Problem / Meaning / Action): one labelled entry per row
        groups = [(r[0], [(h, c) for h, c in zip(t["header"][1:], r[1:]) if c.strip()]) for r in t["rows"]]
    want_table = fmt == "table" or (fmt is None and intent in ("compare", "conflict") and tables)
    if groups and not want_table:
        body = [ln for g in groups[:cap] for ln in group_lines(*g)]
        steps_asked = fmt == "steps"
        note = "The documents give these as separate items with details, not as a sequence of steps:" if steps_asked else ""
        lead = next((x for x in sents if not x.endswith(":")), "")
        return {"blocks": text(body, note or lead), "how": "template", "fit": not steps_asked}
    if want_table and groups and not tables:
        heads = list(dict.fromkeys(h for _, fl in groups for h, _v in fl))
        rows = [[simplify(lb)] + [simplify(dict(fl).get(h, "")) for h in heads] for lb, fl in groups[:cap * 2]]
        return {"blocks": text([], "Here is the answer as a table.") + [_table_block(["Item"] + heads, rows)], "how": "template", "fit": True}
    want_steps = fmt == "steps" or (fmt is None and intent == "procedure")
    want_list = fmt == "list" or (fmt is None and intent == "enumerate")

    if want_table:
        if tables:
            t = tables[0]
            blocks = text([], sents[0] if sents else "Here is the table from the documents.")
            blocks.append(_table_block(t["header"], t["rows"][:cap * 2], t, t.get("title", "")))
        else:
            pairs = [_LABEL.match(x) for x in (bullets or sents)]
            if len(pairs) >= 2 and all(pairs):
                rows = [[simplify(m.group(1)), simplify(m.group(2)) + _tag(m.group(0), ctoks)] for m in pairs[:cap * 2]]
                blocks = text([], "Here is the answer as a table.") + [_table_block(["Item", "Detail"], rows)]
            else:
                fit, note = False, "The documents give this as text, not as a table with columns:"
                blocks = text([f"- {line(x)}" for x in (bullets or sents)[:cap]], note)
    elif want_steps:
        seq = bullets if bullets else sents
        if tables and not seq:
            t = tables[0]
            if any(_SEQ_HDR.search(h) for h in t["header"]):
                seq = [_row_line(t["header"], r) for r in t["rows"]]
            else:
                fit = False
        acts = sum(_actionish(x) for x in seq) >= max(1, len(seq) / 2)
        if fit and (numbered or (acts if fmt == "steps" else bool(seq)) and (intent == "procedure" or len(seq) > 1)):
            steps = [f"{i}. {line(x)}" for i, x in enumerate(seq[:cap], 1)]
            blocks = text(steps, "Follow these steps.")
        else:
            fit = False
            body = seq or (table_lines(tables[0]) if tables else [])
            blocks = text([f"- {line(x)}" for x in body[:cap]], "The documents give these as options or facts, not as a sequence of steps:")
    elif want_list:
        body = bullets or [x for x in sents[1:]] or (table_lines(tables[0]) if tables else [])
        head = sents[0] if sents and bullets else ""
        if not body and sents:
            body, head = sents, ""
        lead = head or ("Here is the list from the documents." if len(body) > 1 else "")
        blocks = text([f"- {line(x)}" for x in body[:cap]], lead)
    else:
        lead = sents[0] if sents else (bullets[0] if bullets else (table_lines(tables[0], 1)[0] if tables else ""))
        rest = (bullets[1:] if not sents and bullets else bullets) + sents[1:]
        if tables and plan["intent"] in ("lookup", "define", "topic", "explain", "diagnose", "situation", "comply"):
            rest += table_lines(tables[0])[1 if not sents else 0:]
        if intent == "diagnose" and len(rest) > 1:
            lead = (lead + " " if lead else "") + "Check these in order:"
            rest = [f"{i}. {line(x)}" for i, x in enumerate(rest[:cap], 1)]
        else:
            rest = [f"- {line(x)}" for x in rest[:(0 if plan.get("length") == "brief" else cap)]]
            if plan.get("length") == "brief" and len(sents) > 1:
                lead = lead + " " + sents[1]
        blocks = text(rest, line(lead) if lead else "")
    return {"blocks": blocks, "how": "template", "fit": fit} if blocks else None


def flat(blocks):
    out = []
    for b in blocks:
        if b["k"] == "text":
            out.append(b["md"])
        else:
            out += [" ".join(b["header"])] + [" ".join(r) for r in b["rows"]]
    return "\n".join(out)


# The chunk text an answer's cites point to plus the engine's own composed text: what a rewrite is allowed to say.
def evidence_text(ans, index):
    return "\n".join([index.chunks[c["chunk"]]["text"] for c in ans.cites[:8]] + [ans.plain()])


def validate(plan, blocks, ans, index, fit=True, evidence=None, claims=False):
    """(ok, reason). A rewrite is used only when it is in the chunks and answers the question asked."""
    text = flat(blocks)
    if not text.strip():
        return False, "empty"
    body = text
    for ph in SCAFFOLD:
        body = body.replace(ph, "")
    if not body.strip():
        return False, "empty"
    body = re.sub(r"(?m)^\s*\d+[.)]\s+", "", body)                # step numbers are ours, not facts from the chunks
    ev = evidence if evidence is not None else evidence_text(ans, index)
    v = answering.verify(body, ev, plan["question"], claims=claims)
    if v["refusal"]:
        return False, "refusal"
    if v["numbers_missing"] or v["acronyms_missing"]:
        return False, "numbers or terms not in the chunks"
    if v["support"] < answering.VERIFY_SUPPORT:
        return False, f"only {v['support']:.0%} of its words are in the chunks"
    bad = [r for r in v["reasons"] if r.startswith(("not supported", "mixes"))]
    if claims and bad:
        return False, bad[0]
    if fit:
        fmt = plan.get("format")
        has_table = any(b["k"] == "table" for b in blocks)
        if fmt == "table" and not has_table:
            return False, "table asked, none given"
        if fmt == "steps" and len(re.findall(r"(?m)^\s*\d+[.)]\s", text)) < 2:
            return False, "steps asked, fewer than 2 given"
        if fmt == "list" and len(re.findall(r"(?m)^\s*[-*•]\s", text)) < 2:
            return False, "list asked, fewer than 2 bullets"
    terms = [t for t in plan["terms"] if not t.isdigit() and not t.startswith(("diagnos", "troubleshoot", "procedur", "explain", "describ", "overview", "summar"))]
    if terms:
        heads = " ".join(c.get("heading", "") for c in ans.cites[:8])
        have = set(rag.tokens(text + " " + heads))
        if sum(t in have for t in terms) / len(terms) < 0.4:
            return False, "does not cover the question's words"
    return True, ""


# A model's markdown reply as blocks: pipe tables become table blocks, the rest stays text.
def parse_markdown(md):
    blocks, buf, rows = [], [], []

    def flush_text():
        if any(x.strip() for x in buf):
            blocks.append({"k": "text", "md": "\n".join(buf).strip()})
        buf.clear()

    def flush_table():
        rs = [r for r in rows if not re.fullmatch(r"[\s|:\-]+", "|".join(r))]
        if len(rs) >= 2:
            blocks.append(_table_block(rs[0], rs[1:]))
        rows.clear()

    for ln in md.strip().split("\n"):
        if ln.strip().startswith("|") and ln.strip().count("|") >= 2:
            flush_text()
            rows.append([c.strip() for c in ln.strip().strip("|").split("|")])
        else:
            flush_table()
            buf.append(ln)
    flush_table()
    flush_text()
    return blocks
