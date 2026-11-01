"""The answering step: every question about the documents gets an answer WRITTEN BY THE LANGUAGE MODEL from the passages most like the question.

    question -> candidate passages (keyword search + closeness in meaning) -> choose(): only passages close enough, no repeats, no contents pages, at most a few
             -> the model writes the answer from exactly those passages -> checked against them -> shown, with the passages it was written from

The model never sees more than it needs, and never anything unrelated to the question:
  * a passage must be close to the question in meaning (and share its key words) to be used at all; if none is, the model is not asked and the reply says the documents do not cover it;
  * after the best passage, only passages nearly as close are kept, never more than `MAX_PASSAGES`, never two that say the same thing, never a contents list;
  * each passage is cut down to the part about the question and the whole context stays inside a fixed size;
  * the prompt tells the model to answer only the question, only from those passages, and to say so when they do not answer it;
  * the answer is then checked: numbers, terms and each clause must be found in the passages; an answer that fails is replaced by the closest passage sentences, and says so.
No other model is involved: the one that answers is the local model the administrator made active, and nothing is sent to any other service.
"""
import re
import time

from welding_ai.answering import answering
from welding_ai.answering.answering import NOT_FOUND
from welding_ai.core import events
from welding_ai.llm.ollama import LLMError
from welding_ai.orchestration import steps
from welding_ai.orchestration.prompts import MODEL_SOURCE_LINE, NO_DIRECT, out_of_scope
from welding_ai.orchestration.steps import Step
from welding_ai.knowledge import references
from welding_ai.retrieval import rag
from welding_ai.understanding import planner

SIM_LOW, SIM_HIGH = 0.58, 0.85          # cosine similarity mapped to 0..1 between these (nomic-embed-text on these documents: on-topic questions' best passages 0.68-0.86)
MIN_SIM = 0.62                          # a passage less like the question than this is never used when similarity is available (off-topic questions' best passages: 0.51-0.575)
MIN_RELEVANCE = 0.30                    # ... or scoring less than this overall
RELATIVE = 0.70                         # after the best passage, only passages scoring at least this share of it
AGENT_BOOST, AGENT_OTHER = 1.2, 0.85    # a question sent to an agent: that agent's own passages count more, the others less (knowledge/passage_types.py)
MAX_PARTS, PART_PASSAGES, MULTI_PASSAGES, MULTI_BUDGET = 3, 3, 6, 6200   # a question that asks several things: parts, passages per part, passages in all, characters of context
UNRELATED = 0.35                        # an answer sharing less than this with the passages is not about them
GOOD = 0.60                             # an overall score from here on is a good match; below it a weak one
MAX_PASSAGES = 4
TOPIC_BOOST, TOPIC_OTHER = 1.25, 0.75   # the question names a document's topic: that document's passages count more, the others less
TOPIC_PASSAGES = 3                      # at least this many of the named documents' best passages are always considered
CANDIDATES = 12                        # from the keyword search, and again from the similarity ranking
DUPLICATE = 0.85                        # two passages whose words overlap this much are the same passage
W_SIM = 0.70                          # share of the score that comes from closeness in meaning (the rest is the keyword coverage)


def relevance(candidate, with_similarity):
    cover = candidate.get("coverage", 0.0)
    if with_similarity and candidate.get("similarity") is not None:
        near = min(1.0, max(0.0, (candidate["similarity"] - SIM_LOW) / (SIM_HIGH - SIM_LOW)))
        return W_SIM * near + (1 - W_SIM) * cover
    return cover


def _words(candidate):
    return frozenset(rag.tokens(candidate["heading"] + " " + candidate["text"][:400]))


def choose(candidates, with_similarity, hard_min=0.45, weak=0.55, limit=MAX_PASSAGES, topics=None, prefer=None):
    """-> (the passages to give the model, best first, each with `similarity` and `relevance` filled in; "good" | "weak" | "none").
    Without similarity (the embedding model is off) the keyword coverage alone decides, with the usual thresholds.
    `topics`: the documents the question names (MIG -> the GMAW chapter). Their passages are preferred and the rest count less, so "porosity in MIG welding" is not answered
    from the chapter on beam welding; a passage from another document still gets in when it is clearly the better one."""
    scored = []
    for c in candidates:
        score = relevance(c, with_similarity)
        if with_similarity and c.get("similarity") is not None:
            if c["similarity"] < MIN_SIM or score < MIN_RELEVANCE:
                continue
        elif c.get("coverage", 0.0) < hard_min:
            continue
        if topics:
            score *= TOPIC_BOOST if c["source"] in topics else TOPIC_OTHER
        if prefer:
            score *= AGENT_BOOST if c["chunk_id"] in prefer else AGENT_OTHER
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return [], "none"
    best = scored[0][0]
    chosen, seen = [], []
    for score, c in scored:
        if score < best * RELATIVE or len(chosen) >= limit:
            break
        words = _words(c)
        if any(len(words & s) / max(len(words | s), 1) >= DUPLICATE for s in seen):
            continue
        seen.append(words)
        chosen.append(dict(c, relevance=round(score, 3), similarity=None if c.get("similarity") is None else round(c["similarity"], 3)))
    top = chosen[0]
    if with_similarity and top.get("similarity") is not None:
        match = "good" if best >= GOOD else "weak"
    else:
        match = "good" if top.get("coverage", 0.0) >= weak else "weak"
    return chosen, match


_CITE = re.compile(r"\[(\d+)\]")
_PRONOUN = re.compile(r"\b(it|its|this|that|they|them|their|those|these)\b", re.I)
_PIECES = re.compile(r"(\n+|(?<=[.!?])\s+(?!\[))")                     # a sentence ends at a full stop unless a citation follows it, which belongs to that sentence
CITE_GAIN = 0.15                        # a passage must cover this much more of a sentence than the one cited before the number is changed


def repair_citations(answer, passages):
    """The small model sometimes cites the wrong passage for a statement that is right. For each sentence or line, if another passage covers its words clearly better than
    the one it cites, the number is changed to that passage's. Only the numbers change, never the words. -> the answer with the numbers repaired."""
    texts = [frozenset(rag.tokens(p["heading"] + " " + p["text"])) for p in passages]
    out = []
    for piece in _PIECES.split(answer):
        marks = {int(n) for n in _CITE.findall(piece)}
        words = frozenset(rag.tokens(_CITE.sub("", piece))) if marks else frozenset()
        if marks and words and all(1 <= m <= len(texts) for m in marks):
            cover = [len(words & t) / len(words) for t in texts]
            best = max(range(len(texts)), key=lambda i: cover[i])
            if all(cover[best] >= cover[m - 1] + CITE_GAIN for m in marks):
                done = []

                def swap(match):
                    if done:
                        return ""                        # two numbers for one statement become one
                    done.append(1)
                    return f"[{best + 1}]"
                piece = re.sub(r"\s*\[\d+\]", lambda m: (" " if not done else "") + swap(m), piece)
        out.append(piece)
    return "".join(out)


def agent_passages(h, agent):
    """The ids of the passages that feed this agent (sorted on every reindex), or None when the passages are not sorted or no agent was asked for."""
    types = getattr(h, "passage_types", None)
    if not agent or not types:
        return None
    from welding_ai.knowledge.passage_types import AGENT_OF
    return {i for i, kind in types.items() if AGENT_OF.get(kind) == agent} or None


_LEAD = re.compile(r"^\s*(?:and\s+|also\s+|so\s+)?(?:what|why|how|which|who|when|where)(?:\s+(?:is|are|was|were|does|do|did|can|could|should|would|to))?\s+(?:the\s+|a\s+|an\s+)?", re.I)


def follow_up_subject(previous):
    """What the previous question was about, in words ("What is arc energy?" -> "arc energy"; "What causes porosity, and how do I stop it?" -> "porosity"); "" when there was none."""
    if not previous:
        return ""
    first = search_text((parts_of(previous) or [previous])[0])
    entity = planner.analyze(first).get("entity") or ""
    if len(entity.split()) >= 2:
        return entity
    text = _LEAD.sub("", first.strip(" ?.,"))
    text = re.sub(r"^(?:cause|causes|cause of|reason for|reasons for)\s+", "", text, flags=re.I)
    return " ".join(text.split()[:8])


def parts_of(q):
    """The separate questions inside one that asks several things ("what causes porosity and how do I stop it"), at most MAX_PARTS; [] for a question with one part."""
    parts = [re.sub(r"\s+(?:and|also)$", "", p.strip(" ,;?."), flags=re.I).strip(" ,;?.") for p in planner.split_parts(q)]
    if len(parts) < 2 or any(len(p.split()) < 3 for p in parts):
        return []
    return parts[:MAX_PARTS]


def search_text(q):
    """The question without the words that say how to answer it ("briefly", "in a table", "step by step"): they describe the answer, not the topic, and would match passages by chance."""
    text = planner._strip_format_words(q)
    for rx in (planner.RE_BRIEF, planner.RE_DETAIL):
        text = rx.sub(" ", text)
    text = re.sub(r"\bstep[- ]by[- ]step\b|\bin (?:a )?(?:bullet(?:ed)? )?(?:list|points?)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,;")
    return text if len(text.split()) >= 2 else q


def style_line(q, parts=(), missing=()):
    """How the user asked for the answer, as one line for the model: the layout (table, steps, list), the length, and the parts to answer in order."""
    plan = planner.analyze(q)
    bits = []
    layout = {"table": "a markdown table whose columns come from the excerpts", "steps": "a numbered list, one action per step, in the order the excerpts give",
              "list": "short bullet points"}.get(plan["format"])
    if not layout:
        layout = {"procedure": "a numbered list, one action per step", "enumerate": "short bullet points", "compare": "a short table or two short groups, one for each thing compared",
                  "diagnose": "the likely causes first, then what to do about each"}.get(plan["intent"])
    if layout:
        bits.append("Layout: " + layout + ".")
    if plan["length"] == "brief":
        bits.append("Length: one to three sentences.")
    elif plan["length"] == "detail":
        bits.append("Length: complete, covering every point the excerpts make.")
    if len(parts) > 1:
        bits.append(f"The question has {len(parts)} parts: " + " | ".join(f"({i}) {p}" for i, p in enumerate(parts, 1)) + ". Answer each part in this order, starting its answer with its number.")
    if missing:
        bits.append("The excerpts have nothing for: " + " | ".join(missing) + '. For those parts write "Not covered in the documents." and nothing else.')
    bits.append("Answer in the plain tone the question is asked in; no greeting, no closing offer.")
    return " ".join(bits)


def with_approved(h, ctx, text, chosen, match):
    """An approved pair added by a person whose question is the question asked (by meaning) goes first: the passages it stands on, with its approved answer written in front of the
    first one, so the model words that answer. -> (the passages, "good" when a pair was used else `match`). Nothing is trained: the pair counts the moment it is approved."""
    lookup = getattr(h, "learned", None)
    found = lookup.find(text) if lookup is not None else None
    if found is None:
        return chosen, match
    pair, cosine = found
    own = lookup.passages_of(pair, h.index.chunks)
    if not own:                                                # the document it stood on changed: not used (the table shows it as Not found)
        return chosen, match
    first = dict(h._hit(own[0]["chunk_id"], 1.0), relevance=1.0, similarity=round(cosine, 3), score=0.0, entity_match=False, units=[])
    first["text"] = f"Approved answer: {pair['answer']}" + chr(10) * 2 + first["text"]
    rest = [dict(h._hit(p["chunk_id"], 1.0), relevance=1.0, similarity=round(cosine, 3), score=0.0, entity_match=False, units=[]) for p in own[1:]]
    taken = {first["chunk_id"], *(p["chunk_id"] for p in rest)}
    label = "Manually referenced from document" if pair.get("refs") or pair["doc"].endswith(".docx") else "Manually added"
    ctx.notes.append(f"Answered from an approved pair ({label}): “{pair['question']}”.")
    return [first, *rest, *(c for c in chosen if c["chunk_id"] not in taken)][:MAX_PASSAGES + 1], "good"


MAX_FOLLOWED = 2                         # passages added because the best passages point to them ("Chapter 47, Section 4")


def follow_references(h, ctx, question, chosen, similarity=None):
    """The best passages often end with "(Chapter 47, Section 4)": the answer is there, not here. The chapter is found by its number, the section by its heading (a chapter named alone:
    its passage closest to the question), and what it holds is read as well. -> the passages with up to MAX_FOLLOWED more after them."""
    if not chosen:
        return chosen
    chapters = getattr(h, "_chapters", None)
    if chapters is None or chapters.chunks is not h.index.chunks:
        chapters = h._chapters = references.Chapters(h.index.chunks)
    have = {c["chunk_id"] for c in chosen}
    extras = []
    for passage in chosen[:2]:
        for chapter, section in references.find(passage["text"]):
            if len(extras) >= MAX_FOLLOWED or not chapters.has(chapter):
                continue
            if section is None and chapter == references.chapter_of(passage["source"]):
                continue                                                   # the passage only names its own chapter
            ids = chapters._by[chapter]
            sims = similarity.scores(question, ids) if similarity is not None and getattr(similarity, "ready", False) else {}
            score = lambda i: sims.get(i, h.index._coverage(question, i))
            for cid in chapters.resolve(chapter, section, score, limit=1, skip=have | {e["chunk_id"] for e in extras}):
                extras.append(dict(h._hit(cid, 0.5), relevance=0.5, similarity=None, score=0.0, entity_match=False, units=[], via_reference=True))
                ctx.notes.append(f"Followed the reference to Chapter {chapter}{f', Section {section}' if section else ''}.")
    return list(chosen) + extras


def named_topics(h, query):
    """-> {document: score} for the documents whose topic the question names (an alias such as MIG or GMAW), {} when none or when the facts of the documents are not loaded."""
    facts = getattr(h, "facts", None)
    try:
        return facts.topics.detect(query) if facts is not None else {}
    except Exception:
        return {}


def gather(h, query, similarity, topics=None):
    """The passages the keyword search finds and the ones closest in meaning, joined, each with its keyword coverage and (when it can be had) its similarity.
    When the question names a document's topic, that document's best passages are always among them."""
    cfg, index = h.cfg, h.index
    hits = index.search(index.expand(query) if cfg.use_expand else query, CANDIDATES)
    by = {x["chunk_id"]: dict(x, similarity=None) for x in hits}
    nav = getattr(index, "nav", None)
    if topics:
        for x in index.search(index.expand(query) if cfg.use_expand else query, 60):
            if x["source"] in topics and x["chunk_id"] not in by and not (nav is not None and nav[x["chunk_id"]]):
                by[x["chunk_id"]] = dict(x, similarity=None)
    if similarity is not None and similarity.ready:
        ranked = similarity.rank(query, CANDIDATES)
        if topics:
            near = [(c, s) for c, s in similarity.rank(query, 600) if index.chunks[c]["source"] in topics][:TOPIC_PASSAGES]
            ranked = ranked + [x for x in near if x not in ranked]
        for cid, cos in ranked:
            if nav is not None and nav[cid]:
                continue                                                        # a contents list points to content, it is not content
            if cid not in by:
                by[cid] = dict(h._hit(cid, round(index._coverage(query, cid), 3)), score=0.0, entity_match=False, units=[], similarity=cos)
            else:
                by[cid]["similarity"] = cos
        for cid, cos in similarity.scores(query, [c for c, x in by.items() if x["similarity"] is None]).items():
            by[cid]["similarity"] = cos
    return list(by.values())


class GroundedStep(Step):
    """Retrieve by similarity, choose, then have the model write the answer. The last step of the chain: it always answers."""
    stage = ("search", "Finding the passages most like your question")

    def run(self, ctx):
        h, cfg = ctx.h, ctx.h.cfg
        q, history = ctx.q, ctx.history
        t1 = time.time()
        q_fixed, fixes = h.index.correct(q) if cfg.use_correct else (q, [])
        fixes = [f for f in fixes if " " not in f[1]]
        if fixes:
            ctx.notes.append("Searched for the corrected spelling: " + ", ".join(f"{a} → {b}" for a, b in fixes) + ".")
        dependent = not ctx.standalone
        if dependent:                                           # a follow-up ("and why does that matter?"): it is read about what the last question was about
            subject = follow_up_subject(ctx.prev_question)
            if subject:
                q_fixed, dependent = f"{q_fixed.rstrip('?. ')} (about {subject})?", False
                ctx.notes.append(f"Understood as a follow-up about {subject}.")
        query = h._retrieval_query(search_text(q_fixed), history, dependent=dependent)
        similarity = getattr(h, "similarity", None)
        with_similarity = bool(similarity is not None and similarity.ready)
        topics = named_topics(h, query)
        prefer = agent_passages(h, ctx.agent)
        ctx.parts = [] if ctx.nested or not ctx.standalone else parts_of(q_fixed)
        missing = []
        if ctx.parts:                                            # several questions in one: each is searched on its own, so none is drowned out by the others
            chosen, match, missing = self._choose_parts(h, ctx, similarity, with_similarity, topics, prefer)
            ctx.missing = missing
        else:
            chosen, match = choose(gather(h, query, similarity, topics), with_similarity, cfg.hard_min, cfg.weak, topics=topics, prefer=prefer)
            if topics:                                           # naming a process favours its chapter, but the answer may be in another one (distortion is not a MIG-only subject): the best passages anywhere join them
                plain, plain_match = choose(gather(h, query, similarity, None), with_similarity, cfg.hard_min, cfg.weak, prefer=prefer)
                extra = [p for p in plain[:2] if all(p["chunk_id"] != x["chunk_id"] for x in chosen)]
                if extra:
                    chosen, match = (chosen + extra)[:MAX_PASSAGES + 2], (match if chosen else plain_match)
            chosen, match = with_approved(h, ctx, q_fixed, chosen, match)
            chosen = follow_references(h, ctx, q_fixed, chosen, similarity)
        if topics and any(c["source"] in topics for c in chosen):
            looked = [h.facts.topics.name(s) for s in sorted(topics, key=topics.get, reverse=True)[:2]]
            ctx.notes.append("The question names " + " and ".join(looked) + ", so that chapter was looked at first.")
        if fixes and match == "weak" and not with_similarity:
            chosen, match = [], "none"                  # a spelling correction may rescue a good match but must never create a weak one
        return self._answer(ctx, q_fixed, query, chosen, match, with_similarity, t1)

    def _choose_parts(self, h, ctx, similarity, with_similarity, topics, prefer):
        """-> (passages for all the parts, the weakest match, the parts nothing was found for). Each part contributes its best passages in turn, so every part is represented."""
        cfg = h.cfg
        per_part, missing, matches = [], [], []
        ctx.per_part = []
        for n, part in enumerate(ctx.parts):
            lookup = part + " " + ctx.parts[0] if n and _PRONOUN.search(part) else part       # "how do I stop it": it is what the first part is about
            found, how = choose(gather(h, lookup, similarity, topics), with_similarity, cfg.hard_min, cfg.weak, limit=PART_PASSAGES, topics=topics, prefer=prefer)
            found, how = with_approved(h, ctx, part, found, how)
            found = follow_references(h, ctx, part, found, similarity)
            ctx.per_part.append(found)
            if found:
                per_part.append(found)
                matches.append(how)
            else:
                missing.append(part)
        chosen, seen = [], set()
        for rank in range(PART_PASSAGES):
            for found in per_part:
                if rank < len(found) and found[rank]["chunk_id"] not in seen and len(chosen) < MULTI_PASSAGES:
                    seen.add(found[rank]["chunk_id"])
                    chosen.append(found[rank])
        if missing and chosen:
            ctx.notes.append("The documents have nothing on: " + "; ".join(missing) + ".")
        return chosen, ("weak" if "weak" in matches else "good" if matches else "none"), missing

    def _answer(self, ctx, q_fixed, query, chosen, match, with_similarity, t1):
        h, cfg = ctx.h, ctx.h.cfg
        context, used = h._build_context(chosen, q_fixed, MULTI_BUDGET if ctx.parts else None) if chosen else ("", 0)
        chosen = chosen[:used]
        comp = answering.compose_extractive(h.index, q_fixed, chosen) if chosen else {"text": "", "units": [], "qtype": None, "top_score": 0}
        key_details = h._key_details(comp, chosen) if comp["units"] else []
        ctx.T["retrieval_ms"] = round((time.time() - t1) * 1000, 2)
        ctx.T["similarity"] = with_similarity
        if chosen and ctx.mode == "verbatim":
            ctx.notes.append("The AI model is busy retraining, so the closest passages are shown instead.")
        yield events.sources(chosen, used, rag.query_words(q_fixed), match, ctx.notes, "retrieval", key_details=key_details, qtype=comp["qtype"])

        if not chosen:                                           # nothing in the documents is close enough: the model is not asked
            yield events.token(out_of_scope(len(h.sources)) + h.responders._related_text(q_fixed))
            yield ctx.done(route="not_found", grounded=False, confidence="n/a", match="none")
            return
        if ctx.mode == "search":
            yield ctx.done(route="search_only", grounded=True, match=match)
            return
        if ctx.mode == "verbatim":                               # the model cannot be used right now (a retrain has the graphics card): the closest passages instead
            yield events.token(comp["text"] or NO_DIRECT)
            yield ctx.done(route="extractive_degraded", grounded=bool(comp["text"]), confidence="low", match=match, answer_source="extractive")
            return
        yield events.stage("write", "Writing the answer")
        if ctx.parts:
            yield from self._write_parts(ctx, chosen, comp, match)
            return
        yield from self._write(ctx, q_fixed, chosen, context, comp, match)

    def _generate(self, ctx, q, context, style):
        """One call to the model: -> (the answer text, the error or None, the model's stats). A refusal is only the sentence; the page shows the real sources."""
        h, cfg, model = ctx.h, ctx.h.cfg, ctx.model
        llm = steps._llm()
        opts = {"temperature": cfg.temperature, "top_p": 0.9, "top_k": cfg.top_k, "num_ctx": cfg.model_ctx.get(model, 3072), "num_predict": cfg.num_predict, "repeat_penalty": cfg.repeat_penalty}
        raw, stats, error, t_llm = "", {}, None, time.time()
        try:
            for kind, value in llm.llm_stream(model, h._messages(q, context, ctx.history, style), opts, cfg.llm_timeout):
                if kind == "stats":
                    stats = value
                    break
                raw += value
                if raw.lstrip().startswith(NOT_FOUND) or MODEL_SOURCE_LINE.search(raw):
                    break
        except LLMError as e:
            error = str(e)
        ctx.T["llm_total_s"] = round(ctx.T.get("llm_total_s", 0) + time.time() - t_llm, 3)
        answer = raw.strip()
        if answer.startswith(NOT_FOUND):
            answer = NOT_FOUND
        else:
            cut = MODEL_SOURCE_LINE.search(answer)
            answer = answer[:cut.start()].rstrip() if cut else answer
        return answer, error, stats

    def _write_parts(self, ctx, chosen, comp, match):
        """A question that asks several things: the model answers each part from that part's own passages (a small model keeps to one short question much better than to
        three), each answer is checked on its own, and what passes is put together in the order asked. A part with no passages, or whose answer fails the check, says so."""
        h, q = ctx.h, ctx.q
        blocks, checks, stats = [], [], {}
        for part, passages in zip(ctx.parts, ctx.per_part):
            title = part[:1].upper() + part[1:].rstrip("?") + "?"
            answer, check = "", None
            if passages:
                context, used = h._build_context(passages, part)
                passages = passages[:used]
                answer, error, stats = self._generate(ctx, part, context, style_line(part))
                if error and not answer:
                    yield events.error(error)
                    yield events.token(comp["text"] or NO_DIRECT)
                    yield ctx.done(route="extractive_degraded", grounded=bool(comp["text"]), confidence="low", match=match, answer_source="extractive")
                    return
                check = answering.verify(answer, context, part, claims=True) if answer else None
                if check is not None and check.get("ok") and not check.get("refusal"):
                    where = {k: chosen.index(p) + 1 for k, p in enumerate(passages, 1) if p in chosen}
                    answer = re.sub(r"\[(\d+)\]", lambda m: f"[{where.get(int(m.group(1)), int(m.group(1)))}]", repair_citations(answer, passages))
                    blocks.append(f"**{title}**" + chr(10) + answer)
                    checks.append(check)
                    continue
            blocks.append(f"**{title}**" + chr(10) + "Not covered in the documents.")
        ctx.T["verify_ok"] = bool(checks)
        if not checks:                                           # no part could be answered from the passages: say so, and keep the passages listed beside it
            yield events.token(out_of_scope(len(h.sources)) + h.responders._related_text(q) if match == "weak" else NO_DIRECT)
            yield ctx.done(route="not_found" if match == "weak" else "no_direct_answer", answer_source="none", grounded=False, confidence="n/a", match="none" if match == "weak" else match)
            return
        yield events.token((chr(10) * 2).join(blocks))
        yield ctx.done(route="llm", answer_source="model", grounded=True, confidence="high" if match == "good" and len(checks) == len(ctx.parts) else "medium", match=match,
                       verification=checks[-1], tok_per_s=(round(stats["eval_count"] / (stats["eval_duration"] / 1e9), 1) if stats.get("eval_count") else None),
                       prompt_tokens=stats.get("prompt_eval_count"))

    def _write(self, ctx, q, chosen, context, comp, match):
        """The model writes; the whole answer is checked against the passages before anything is shown, so a wrong one is never seen and then taken back."""
        h = ctx.h
        answer, error, stats = self._generate(ctx, q, context, style_line(q))
        passages_text = comp["text"] or NO_DIRECT

        if error or not answer:                                  # the model cannot be reached (or said nothing): the closest passages, and the reason
            yield events.error(error or "The model gave no answer.")
            yield events.token(passages_text)
            yield ctx.done(route="extractive_degraded", grounded=bool(comp["text"]), confidence="low", match=match, answer_source="extractive")
            return
        check = answering.verify(answer, context, q, claims=True)
        ctx.T["verify_ok"] = check["ok"]
        if check.get("refusal"):
            yield events.token(answer)
            yield ctx.done(route="llm_refusal", answer_source="model", grounded=False, confidence="n/a", match=match, verification=check)
            return
        if not check["ok"] and match == "weak" and check.get("support", 1.0) < UNRELATED and not check.get("numbers_missing"):      # a weak match whose answer has little to do with the passages: the documents do not cover it
            yield events.token(out_of_scope(len(h.sources)) + h.responders._related_text(q))
            yield ctx.done(route="not_found", answer_source="none", grounded=False, confidence="n/a", match="none", verification=check)
            return
        if not check["ok"] and match == "good" and comp["text"]:    # the model's wording did not hold up, but the passages match well: the document's own sentences answer instead
            yield events.token(comp["text"])
            yield ctx.done(route="fallback_extractive", answer_source="extractive", grounded=True, confidence="medium", match=match, verification=check)
            return
        if not check["ok"]:                                      # not supported by the passages: not shown. The reply says so and the passages stay listed beside it
            yield events.token(NO_DIRECT)
            yield ctx.done(route="no_direct_answer", answer_source="none", grounded=False, confidence="n/a", match=match, verification=check, reasons=check.get("reasons", [])[:3])
            return
        yield events.token(repair_citations(answer, chosen))
        yield ctx.done(route="llm", answer_source="model", grounded=True, confidence="high" if match == "good" else "medium", match=match, verification=check,
                       tok_per_s=(round(stats["eval_count"] / (stats["eval_duration"] / 1e9), 1) if stats.get("eval_count") else None), prompt_tokens=stats.get("prompt_eval_count"))
