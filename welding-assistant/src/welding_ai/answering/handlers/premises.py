"""Questions that assume something ("why does resistance spot welding need shielding gas?"): check the assumption first."""
import re

from welding_ai.answering.evidence import _SKIP_TITLE, Answer, nice, sentences, title_of

_NAV_TITLE = re.compile(r"sources referenced|references|in this (?:chapter|document)|contents|further reading", re.I)
from welding_ai.retrieval import rag

_MADE_OF = re.compile(r"\b(?:made|built|constructed|manufactured|fabricated)\s+(?:of|from|with|in)\b", re.I)
_MATERIAL_SENT = re.compile(r"\b(?:made|constructed|manufactured|fabricated)\s+(?:of|from)\b", re.I)
_NEG = re.compile(r"\b(?:not|no|never|without|neither|nor|unlike|instead\s+of|rather\s+than|does\s+not|do\s+not|non)\b", re.I)


class PremiseHandlers:
    # Check whether the question's assumption ("X needs Y") is supported by the documents; if it is, return None
    # so the ordinary pipeline answers it; if not, say so and show what the documents actually state.
    def premise(self, plan):
        """Answers only when the assumption is NOT supported by the documents. A supported one returns None: the ordinary pipeline answers it."""
        a_text, b_text = plan["premise"]
        a = [t for t in dict.fromkeys(rag.tokens(a_text)) if not t.isdigit()]
        b = [t for t in dict.fromkeys(rag.tokens(b_text)) if not t.isdigit()]
        if not a or not b:
            return None
        key_a = max(a, key=self.idf)                                   # the most specific word of the subject
        made_of = bool(_MADE_OF.search(plan.get("question", "")))      # "must be made of steel": a sentence that merely mentions both is no support
        supporting, opposing = [], []
        for sid in range(len(self.sec)):
            toks = self.stoks[sid]
            if key_a not in toks or not all(t in toks for t in b):
                continue
            if made_of and not all(t in toks for t in a):
                continue                                                # "electrodes" alone also means arc-welding electrodes
            sec = self.sec[sid]
            if _NAV_TITLE.search(sec["title"]):
                continue                                                # reference and contents lists quote titles, they do not state facts
            for blk in sec["blocks"]:
                if blk["t"] not in ("p", "li"):
                    continue
                sents = sentences(blk["text"])
                for st in ([sents[i] + " " + sents[i + 1] for i in range(len(sents) - 1)] if not made_of else []) + sents:     # two neighbouring sentences also count
                    tk = set(rag.tokens(st))
                    if key_a in tk and all(t in tk for t in b):
                        if made_of:                                     # material claims: the sentence must state the material; "rather than" elsewhere in it is irrelevant
                            if self._states_material(st, a_text, b_text):
                                supporting.append((st, sec, blk))
                            continue
                        (opposing if _NEG.search(st) else supporting).append((st, sec, blk))
        if supporting:
            return None                                                 # the documents do say it
        show = lambda x: re.sub(r"\bspot weld\b", "spot welding", x.strip())
        a_text, b_text = show(a_text), show(b_text)
        ans = Answer("premise")
        instead = self._material_instead(key_a, a) if (made_of and not opposing) else None
        if instead:
            st, sec, blk = instead
            ans.text(f"**The documents do not support that.** They say this instead:\n{st}")
            self._cite_block(ans, sec, blk)
        elif opposing:
            st, sec, blk = opposing[0]
            ans.text(f"**The documents do not support that.** They say the opposite:\n{st}")
            self._cite_block(ans, sec, blk)
        else:
            claim = f"{a_text.strip()} are made of {b_text.strip()}" if made_of else f"{a_text.strip()} involves {b_text.strip()}"
            ans.text(f"**The documents do not support that.** Nothing in them says {claim}.")
            for sc, sid, cov in self.search(a, 3):
                s = self.sec[sid]
                if cov >= 0.99 and s["level"] >= 1 and not _SKIP_TITLE.search(s["title"]):
                    self._cite_block(ans, s, next((x for x in s["blocks"] if x["t"] == "p"), s["blocks"][0]))
                    ans.text(f"What the documents do cover about {a_text.strip()}: {title_of(s)} ({nice(s['source'])}).")
                    break
        return ans

    # True when the sentence actually says the subject is made of the material, not just mentions both.
    @staticmethod
    def _states_material(sentence, subject, material):
        """True when the sentence says the subject is made of the material ("made of steel", "steel electrodes"), not just mentions both."""
        st = sentence.lower()
        m = r"[\s-]+".join(re.escape(w) for w in material.strip().lower().split()) + "s?"
        last = re.escape(subject.strip().lower().split()[-1].rstrip("s"))
        return bool(re.search(r"\b(?:made|built|constructed|manufactured|fabricated)\s+(?:of|from|with|in)\s+(?:[\w-]+\s+){0,3}" + m, st)
                    or re.search(r"(?<![\w-])" + m + r"\s+(?:[\w-]+\s+)?" + last, st))

    # A sentence that says what the subject IS actually made of, for when the asked-for material isn't supported.
    def _material_instead(self, key_a, subject_tokens):
        """A sentence that says what the subject IS made of, to show when the asked-for material is not supported."""
        for sid in range(len(self.sec)):
            if not all(t in self.stoks[sid] for t in subject_tokens) or _SKIP_TITLE.search(self.sec[sid]["title"]):
                continue
            for blk in self.sec[sid]["blocks"]:
                if blk["t"] not in ("p", "li"):
                    continue
                for st in sentences(blk["text"]):
                    if key_a in set(rag.tokens(st)) and _MATERIAL_SENT.search(st):
                        return st, self.sec[sid], blk
        return None
