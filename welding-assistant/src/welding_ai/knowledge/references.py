"""A passage often points elsewhere: "Standard porosity controls (Chapter 47, Section 4)". The answer to the question is then in that section, not in the passage, so the reference is
followed: the chapter is found by its number (the document's file name starts with it), the section by its heading, and the passage there is read as well.

    refs = find(text)                              -> [(chapter, section or None), ...] in the order written, without repeats
    chapters = Chapters(chunks)                    the passages by chapter number
    chapters.resolve(chapter, section, score)      -> the passage numbers (at most `limit`) the reference points to; `score(chunk_id)` picks the best passage when only a chapter is named

Used when an answer is made (orchestration/grounded.py) and when training rows are made (training/baseset.py), so the model sees, and learns to use, what a reference points to.
"""
import re

REF = re.compile(r"\b(?:Chapter|Ch\.?)\s*(\d{1,2})(?:\s*[,;(]?\s*(?:Section|Sec\.?|§)\s*(\d{1,2}))?", re.I)
NUMBER = re.compile(r"^(\d{2})_")


def find(text):
    seen, out = set(), []
    for m in REF.finditer(text or ""):
        ref = (int(m.group(1)), int(m.group(2)) if m.group(2) else None)
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def chapter_of(source):
    m = NUMBER.match(source or "")
    return int(m.group(1)) if m else None


class Chapters:
    def __init__(self, chunks):
        self.chunks = chunks
        self._by = {}
        for i, c in enumerate(chunks):
            n = chapter_of(c["source"])
            if n is not None and not c.get("manual"):
                self._by.setdefault(n, []).append(i)

    def has(self, chapter):
        return chapter in self._by

    def resolve(self, chapter, section=None, score=None, limit=2, skip=()):
        """The passages a reference points to. With a section: the passages whose heading starts with its number ("4. Common Defects"). With a chapter only: the one passage that `score`
        likes best (or the chapter's opening passage). `skip`: passage numbers already in hand."""
        ids = [i for i in self._by.get(chapter, []) if i not in skip]
        if section is not None:
            hit = [i for i in ids if self.chunks[i]["heading"].split(" > ")[-1].strip().startswith(f"{section}.")]
            return hit[:limit]
        if not ids:
            return []
        if score is None:
            return ids[:1]
        ranked = sorted(ids, key=lambda i: -score(i))
        return ranked[:1]
