"""Where an answer's wording came from, for the label shown under it.

A reader should be able to tell a quotation from a model's own wording. The label is decided here, in one place, from the route the
pipeline took; the page only displays it.

    calculated  the program calculated it from a formula in the documents, no model
    assembled   sections quoted or calculated from the documents, chosen by the program for the situation described
    quoted      built from the documents' own sentences and tables, no model        verified    a prepared answer, checked against the documents
    ai          the model wrote the wording (checked, but it can still join statements wrongly)
    ai_failed   the model's wording did not pass the checks, so the document text is shown
    ai_down     the model could not be reached, so the document text is shown        not_covered the model found nothing in the excerpts that answers
"""

_LABELS = {
    "calculated": ("Calculated", "Calculated by the program from a formula in the documents. No AI wrote it. Check the numbers it read from your question.", "ok"),
    "assembled": ("Assembled from the documents", "Every section is quoted or calculated from the documents; the program only chose which sections apply and how it read "
                  "your description. Check the assumptions listed first. No AI wrote it.", "ok"),
    "quoted": ("Quoted from the documents", "Built from the documents' own sentences and tables. No AI wrote it.", "ok"),
    "verified": ("Verified answer", "A prepared answer, checked against the documents. No AI wrote it.", "ok"),
    "ai": ("AI answer", "Written by the AI model from the passages most like your question, which are listed with it, and checked for numbers and wording. It can still join "
           "statements wrongly, so check the passages before you rely on it.", "warn"),
    "ai_failed": ("Document text", "The AI summary did not match the documents closely enough, so the document text is shown instead.", "info"),
    "ai_down": ("Document text", "The AI model could not be reached, so the document text is shown instead.", "info"),
    "template": ("From a template", "Built from a template saved in the app. No AI wrote it.", "ok"),
    "draft": ("From a draft", "A person's own entry in a draft, shown as it was entered. It is not from the documents and may not be correct.", "warn"),
    "manual": ("Added by hand", "Written by a person where the documents have no answer. It is not from the documents and may not be correct.", "warn"),
    "not_covered": ("Not covered", "The AI model found nothing in the passages that answers this question.", "info"),
}
NEEDED_NO_AI = "Summary is on, but this answer needed no AI."


def _kind(route, answer_source):
    # a cached answer keeps the route it was first given in `answer_source`
    route = answer_source if route == "cache" else route
    return {"llm": "ai", "llm_refusal": "not_covered", "fallback_extractive": "ai_failed", "extractive_degraded": "ai_down",
            "faq": "verified", "composed": "quoted", "extractive": "quoted", "facts": "quoted", "formula": "calculated", "parameters": "calculated",
            "scenario": "assembled", "wps": "assembled", "manual": "manual", "template": "template", "template_fill": "template", "drafts": "draft"}.get(route)


def describe(route, answer_source=None, ai_requested=False):
    """-> {"kind", "label", "detail", "tone", "note"} for the answer's label, or None when the answer needs none (a refusal, small talk)."""
    kind = _kind(route, answer_source)
    if kind is None:
        return None
    label, detail, tone = _LABELS[kind]
    return {"kind": kind, "label": label, "detail": detail, "tone": tone,
            "note": NEEDED_NO_AI if ai_requested and kind in ("quoted", "verified", "calculated", "assembled") else ""}
