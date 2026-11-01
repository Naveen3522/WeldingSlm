"""What the documents can be asked, worked out from the documents: for each kind of task, how many facts there are, in which documents, and example questions that really get an answer.

Nothing is listed by hand. Add a chapter with a "Cause | Corrective action" table and Troubleshooting grows by that chapter and offers it in its examples; remove it and it is gone.

    capabilities(answerer) -> {"tasks": [{"id", "name", "about", "facts", "documents": [{"source", "topic", "facts"}], "examples": [question, ...]}], "units", "documents", "semantic"}
"""
import hashlib
from collections import Counter, defaultdict

from welding_ai.knowledge.facets import CONCEPTS, TASK_NAMES

ABOUT = {
    "troubleshoot": "Causes, detection, corrections and consequences of defects and faults.",
    "parameters": "What each parameter controls and what goes wrong when it is off.",
    "equipment": "What each component does and what its failure causes.",
    "standards": "Which standards and requirements apply, what they cover and how they are used.",
    "selection": "When a process or method fits, and what to choose instead.",
    "procedure": "Steps and sequences of work.",
    "safety": "Hazards and how they are controlled.",
}
# the facets a task's example question asks for, and how the question is worded around the entity
EXAMPLES = {
    "troubleshoot": (("cause", "correction"), "What causes {e} and how is it corrected?"),
    "parameters": (("controls", "consequence"), "What does {e} control and what happens if it is wrong?"),
    "equipment": (("function", "consequence"), "What is the function of {e} and what is the consequence if it fails?"),
    "standards": (("authority",), "What does {e} cover and how is it used?"),
    "selection": (("selection",), "When should I choose something other than {e}?"),
}
PER_TASK = 3
MAX_ENTITY = 34


def _pick(seed, items):
    return sorted(items, key=lambda x: hashlib.sha1((seed + x[0]).encode()).hexdigest())


def capabilities(answerer):
    facets, units, topics = answerer.facets, answerer.store.units, answerer.topics
    per_task = defaultdict(Counter)
    for u in units:
        for label, _v in u.attrs:
            task = facets.task_of(label)
            if task != "reference":
                per_task[task][u.source] += 1

    candidates = defaultdict(dict)                      # task -> {entity: unit}: entities that carry every facet the task's example asks for
    for u in units:
        if u.kind == "item" or not 3 <= len(u.entity) <= MAX_ENTITY:
            continue
        concepts = {facets.concept_of(label) for label, _v in u.attrs}
        for task, (needed, _t) in EXAMPLES.items():
            if all(c in concepts for c in needed) and CONCEPTS[needed[0]][0] == task:
                candidates[task].setdefault(u.entity.lower(), u)

    out = []
    for task, docs in sorted(per_task.items(), key=lambda kv: -sum(kv[1].values())):
        examples = []
        if task in EXAMPLES:
            template = EXAMPLES[task][1]
            used = set()
            for _key, u in _pick(task, candidates.get(task, {}).items()):
                if u.source in used:
                    continue
                question = template.format(e=u.entity.rstrip("?"))
                if len(examples) < PER_TASK and _answerable(answerer, question):
                    used.add(u.source)
                    examples.append(question)
        out.append({"id": task, "name": TASK_NAMES[task], "about": ABOUT.get(task, ""), "facts": sum(docs.values()),
                    "documents": [{"source": s, "topic": topics.name(s), "facts": n} for s, n in docs.most_common()], "examples": examples})
    return {"tasks": out, "units": len(units), "documents": len({u.source for u in units}), "semantic": facets.semantic}


def _answerable(answerer, question):
    """An example is only offered when the facts really answer it."""
    seen = answerer.understand(question)
    return any(lk.matches for group in seen.looked for lk in group)
