"""Input guard: sanitising, prompt-injection handling, small-talk routing, language hints.

Pure standard library. Everything here runs before any search or model call, in well under a millisecond.
"""
import re
import unicodedata

MAX_QUESTION = 1500

_HTML_BLOCK = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1\s*>")
_HTML_TAG = re.compile(r"<[^>]{1,200}>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Phrases that try to change the assistant's behaviour or extract its instructions.
_INJECTION = re.compile(
    r"(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+|any\s+|the\s+|your\s+|my\s+)?(?:previous|prior|above|earlier|preceding|system|safety)?\s*"
    r"(?:instructions?|prompts?|rules|guidelines|restrictions|messages)"
    r"|(?:reveal|show|print|repeat|display|leak|output|tell\s+me|give\s+me|what\s+(?:is|are))\s+(?:me\s+)?(?:the\s+|your\s+)?"
    r"(?:system|hidden|initial|original|secret|developer)\s+(?:prompt|instructions?|rules|message)"
    r"|(?:reveal|show|print|repeat|display|leak)\s+(?:me\s+)?your\s+(?:prompt|instructions?|rules)"
    r"|system\s+prompt|jailbreak|developer\s+mode|\bDAN\b|do\s+anything\s+now"
    r"|you\s+are\s+now\s+(?:a|an|the)?\b|pretend\s+(?:to\s+be|you\s+are)|act\s+as\s+(?:if|a|an|the)\b(?!\s+welding)",
    re.I)

_SPLIT_SENT = re.compile(r"(?<=[.!?;])\s+|\n+")

# ------------------------------------------------------------------------------------------ language hint
_LANG_WORDS = {
    "Spanish": set("cuál cual qué que es la el los las de del para por una un cómo como está donde dónde soldadura corriente".split()),
    "French": set("quel quelle est le la les des pour une comment où dans soudure courant".split()),
    "German": set("der die das und ist wie welche für schweißen strom nicht".split()),
    "Portuguese": set("qual quais é o a os as de para uma um como soldagem corrente não".split()),
    "Italian": set("quale è il la le dei per una come dove saldatura corrente".split()),
}


# Name of a non-English language if the text looks like one, else None.
def language_hint(text):
    """Name of a non-English language if the text looks like one, else None."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    if sum(ord(c) > 0x2fff for c in letters) / len(letters) > 0.3:      # CJK / Hangul / etc.
        return "a non-English language"
    if sum(ord(c) > 0x24f and ord(c) <= 0x2fff for c in letters) / len(letters) > 0.3:   # Cyrillic, Devanagari, Arabic...
        return "a non-English language"
    words = re.findall(r"[a-záéíóúñüçàâêîôûäöß]+", text.lower())
    for lang, ws in _LANG_WORDS.items():
        if sum(w in ws for w in words) >= 2:
            return lang
    return None


# ------------------------------------------------------------------------------------------ sanitising
# Strip control chars/HTML, cap length, and strip or reject prompt-injection attempts from a raw question.
def sanitize(text, content_tokens=None):
    """Clean a raw question. Returns dict(text, notes, blocked, changed).

    blocked is None, "empty" or "injection". `content_tokens` (callable) counts meaningful words so that a
    question that is only an injection attempt can be rejected while "ignore that. What is GMAW?" survives.
    """
    notes, raw = [], text or ""
    t = unicodedata.normalize("NFKC", raw)
    t = _CONTROL.sub(" ", t)
    if _HTML_BLOCK.search(t) or _HTML_TAG.search(t):
        t = _HTML_TAG.sub(" ", _HTML_BLOCK.sub(" ", t))
        notes.append("Removed HTML markup from the question.")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > MAX_QUESTION:
        t = t[:MAX_QUESTION]
        notes.append(f"Your question was longer than {MAX_QUESTION} characters, so only the first part was used.")
    if not t:
        return {"text": "", "notes": notes, "blocked": "empty", "changed": bool(raw.strip())}
    if _INJECTION.search(t):
        kept = [s for s in _SPLIT_SENT.split(t) if s.strip() and not _INJECTION.search(s)]
        # also try dropping only the injected clause inside a single sentence ("..., and what is GMAW?")
        if not kept:
            clauses = re.split(r"\s*(?:,|;|\band\b|\bthen\b|\balso\b)\s*", t, flags=re.I)
            kept = [c for c in clauses if c.strip() and not _INJECTION.search(c)]
        rest = " ".join(kept).strip()
        meaningful = len(content_tokens(rest)) if content_tokens else len(rest.split())
        if meaningful < 1:
            return {"text": "", "notes": notes, "blocked": "injection", "changed": True}
        t = rest
        notes.append("Ignored an instruction to change how I work; answering the rest of the question.")
    return {"text": t, "notes": notes, "blocked": None, "changed": t != raw.strip()}


# ------------------------------------------------------------------------------------------ small talk
_INTENTS = [
    ("greeting", re.compile(r"^\s*(?:hi|hello|hey|hiya|yo|namaste|good\s+(?:morning|afternoon|evening|day))\b[\s,!.]*(?:there|team|assistant|bot|everyone)?[\s!.]*$", re.I)),
    ("thanks", re.compile(r"^\s*(?:thanks|thank\s+you|thx|ty|cheers|much\s+appreciated|great,?\s+thanks|ok(?:ay)?,?\s+thanks)\b[\s!.a-z]{0,30}$", re.I)),
    ("bye", re.compile(r"^\s*(?:bye|goodbye|see\s+you|cya|good\s+night|that'?s\s+all)\b[\s!.a-z]{0,30}$", re.I)),
    ("about", re.compile(r"^\s*(?:who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|what\s+do\s+you\s+do|help(?:\s+me)?|how\s+do\s+you\s+work|"
                         r"what\s+is\s+this(?:\s+(?:tool|app|assistant|site))?|are\s+you\s+(?:an?\s+)?(?:ai|bot|human|real)|what\s+model\s+are\s+you|"
                         r"how\s+can\s+you\s+help(?:\s+me)?|what\s+questions\s+can\s+i\s+ask)\b[\s?!.]*$", re.I)),
]


# Classify small-talk (greeting/thanks/bye/about), or None if it's a real question.
def detect_intent(text):
    for name, rx in _INTENTS:
        if rx.match(text):
            return name
    return None


# Canned reply text for a detected small-talk intent.
def intent_reply(name, examples):
    ex = "".join(f"\n- {e}" for e in examples[:3])
    if name == "greeting":
        return "Hello! I answer questions about your welding documents and show exactly which passages each answer comes from. Try one of these:" + ex
    if name == "thanks":
        return "You're welcome. Ask another question whenever you like."
    if name == "bye":
        return "Goodbye."
    return ("I am a local documentation assistant. I search your 79 welding documents, answer from the passages I find, "
            "and show the sources so you can check every value. I do not use the internet or outside knowledge, and my answers "
            "can be wrong, so verify safety-critical values in the cited document. Try one of these:" + ex)


# ------------------------------------------------------------------------------------------ safety-critical requests
_SAFETY = re.compile(
    r"\b(?:bypass|by-pass|defeat|disable|override|jumper|jump|circumvent|get\s+around|turn\s+off|switch\s+off|remove|cheat|trick|ignore)\b[^.?!]{0,60}"
    r"\b(?:interlocks?|light\s+curtains?|safety\s+(?:relays?|circuits?|systems?|gates?|guards?|devices?|switch(?:es)?|mats?|inputs?)|e-?stops?|emergency\s+stops?|"
    r"guards?|guarding|lock-?outs?|tag-?outs?|loto|safety)\b"
    r"|\b(?:interlocks?|light\s+curtains?|guards?)\b[^.?!]{0,40}\b(?:bypass|defeat|disable|jumper(?:ed)?|override)\b", re.I)

SAFETY_NOTE = "The documents give no way to bypass or defeat a safety device; below is what they say about the safeguard."
SAFETY_REPLY = ("I can't help with bypassing, defeating or disabling safety interlocks, light curtains, guards, e-stops or lockout. "
                "Those protections must stay in place. If one is faulty or blocking production, isolate and lock out the equipment and have "
                "the responsible safety engineer repair or formally assess it. The documents' guidance on safety integration is listed below; "
                "verify anything safety-critical against the controlled document.")


_MEDICAL = re.compile(
    r"\b(?:treat|treating|cure|heal|soothe|bandage|dress)\b(?:\W+\w+){0,4}?\W+(?:a\s+|my\s+|the\s+)?"
    r"(?:burns?(?![\s-]?(?:through|back|off|in\b))|wounds?|cuts?|injur\w+|scalds?|blisters?)\b"
    r"|\b(?:first[\s-]?aid|doctor|hospital|ambulance|ointment|bandages?|medications?|medicine)\b", re.I)


_BRAND = re.compile(r"\b(?:which|what|best|top|recommend\w*|fastest|cheapest)\b[^.?!]{0,50}\b(?:brands?|manufacturers?|vendors?|companies|company|makes? of)\b(?![-\w])", re.I)


# A request to name a brand, maker or vendor: the documents contain no product or vendor names.
def is_brand_request(text):
    """A request to name a brand, maker or vendor: the documents contain no product or vendor names."""
    return bool(_BRAND.search(text))


# A question about treating an injury (not about preventing it): the welding documents do not cover it.
def is_medical_treatment(text):
    """A question about treating an injury (not about preventing it): the welding documents do not cover it."""
    return bool(_MEDICAL.search(text))


# A request to bypass, defeat or disable a safety device.
def is_safety_bypass(text):
    return bool(_SAFETY.search(text))


# ------------------------------------------------------------------------------------------ input that is not a question
_SQLISH = re.compile(r"\b(?:drop|delete|insert|update|select|truncate)\b.{0,30}\b(?:table|from|into|where)\b", re.I)


# Empty-looking, keyboard-mash or code-like input: a question cannot be read from it.
_VALUES = re.compile(r"(?:\s*[A-Za-z][A-Za-z0-9_]{0,3}\s*[=:]\s*[-+]?\d+(?:[.,]\d+)?\s*[A-Za-z/%°]{0,6}\s*[,;]?)+")


def is_odd_input(text):
    """Empty-looking, keyboard-mash or code-like input: a question cannot be read from it."""
    t = text.strip()
    if _SQLISH.search(t):
        return True
    if _VALUES.fullmatch(t):                                      # "v = 350" or "C = 0.2, Mn = 1.2": the numbers a calculation asked for
        return False
    letters = [c for c in t if c.isalpha()]                       # any script: Hindi or Chinese is a language, not noise
    if len(letters) < 2:
        return True
    words = re.findall(r"[A-Za-z]{4,}", t)
    if len(words) >= 2:
        vowelless = [w for w in words if not re.search(r"[aeiouy]", w.lower())]
        if len(vowelless) / len(words) >= 0.6:
            return True
    if words and len(words) <= 2 and any(re.search(r"(.)\1{4,}", w) for w in words):
        return True
    return False


ODD_REPLY = "I couldn't read a question in that. Ask about a welding process, parameter, standard, defect or procedure in the documents."
