"""Reads a welding situation in plain words into its parts: process, sheets (thickness and material), coating, symptom and what is being asked.

Every part carries how it was read ("DP600 -> advanced high-strength steel (dual-phase)") so the answer can say what it assumed. Nothing is
guessed beyond the small word lists below; a part that is not recognised is left empty and asked for, never filled in.
"""
import re
from dataclasses import dataclass, field

INCH = 25.4

# canonical key -> (words in a question, how the documents name it)
PROCESSES = {
    "rsw": (r"\b(?:resistance\s+)?spot[\s-]*weld\w*|\bRSW\b", "resistance spot welding"),
    "gmaw": (r"\bMIG\b|\bMAG\b|\bGMAW\b|gas\s+metal\s+arc", "gas metal arc welding (GMAW)"),
    "gtaw": (r"\bTIG\b|\bGTAW\b|gas\s+tungsten", "gas tungsten arc welding (GTAW)"),
    "fcaw": (r"flux[\s-]*cored|\bFCAW\b", "flux-cored arc welding (FCAW)"),
    "smaw": (r"\bSMAW\b|stick\s+(?:electrode|welding)|manual\s+metal\s+arc|\bMMA\b", "shielded metal arc welding (SMAW)"),
    "saw": (r"submerged\s+arc|(?-i:\bSAW\b)", "submerged arc welding (SAW)"),
    "laser": (r"\blaser\b", "laser welding"),
    "fsw": (r"friction\s+stir|\bFSW\b", "friction stir welding"),
    "plasma": (r"\bplasma\b", "plasma arc welding"),
}

# material family key -> (words, how the documents name the family, the note shown when a grade was read as a family)
MATERIALS = {
    "ahss": (r"\bDP\s?\d{3,4}\b|dual[\s-]*phase|(?-i:\bTRIP\b)|martensit\w*|press[\s-]*hardened|22MnB5|\busibor\b|\bAHSS\b|\bUHSS\b|complex[\s-]*phase|\bCP\d{3,4}\b|advanced\s+high[\s-]*strength",
             "AHSS / UHSS", "{word} read as advanced high-strength steel (the documents list dual-phase, TRIP, martensitic and press-hardened under AHSS / UHSS)"),
    "hsla": (r"\bHSLA\b|high[\s-]*strength\s+low[\s-]*alloy", "HSLA steel", ""),
    "mild": (r"mild\s+steel|low[\s-]*carbon\s+steel|\bDC0[1-6]\b|carbon\s+steel", "mild / low-carbon steel", ""),
    "stainless": (r"stainless|\b30[34]L?\b(?!\s*(?:mm|in))|\b316L?\b(?!\s*(?:mm|in))|duplex|austenitic|ferritic", "stainless steel", ""),
    "aluminium": (r"alumin(?:i)?um|\bAl\s?\d{4}\b|\b[56]\d{3}\s*(?:alloy|series)\b", "aluminium", ""),
    "titanium": (r"titanium|\bTi[\s-]?6Al", "titanium", ""),
    "nickel": (r"\bnickel\b|inconel|hastelloy", "nickel alloy", ""),
    "copper": (r"\bcopper\b|\bbrass\b|\bbronze\b", "copper alloy", ""),
    "cast_iron": (r"cast\s+iron", "cast iron", ""),
}
VAGUE_STRENGTH = re.compile(r"high[\s-]*strength\s+steel", re.I)       # "high strength steel": which family is not said

COATINGS = re.compile(r"galvanni?ealed|galvani[sz]ed|\bzinc\b|hot[\s-]*dip|electro[\s-]*galvani[sz]ed|(?-i:\bGI\b|\bGA\b)|aluzinc|\bAl[\s-]?Si\b|coated", re.I)

# colloquial symptom -> (the imperfection as the documents name it, note). Matched in this order.
SYMPTOM_WORDS = [
    (r"tiny\s+holes|pin[\s-]*holes?|blow[\s-]*holes?|\bpores?\b|porosity", "porosity", ""),
    (r"expulsion|expelled|splash(?:ing)?\b|spitting|molten\s+metal\s+(?:is\s+)?(?:ejected|thrown|flying|spit)", "expulsion", ""),
    (r"spatter|splatter", "spatter", ""),
    (r"hydrogen\s+crack|cold\s+crack|delayed\s+crack|cracks?\s+(?:appear|found|show)\w*\s+(?:hours|days|later)", "hydrogen cracking", ""),
    (r"hot\s+crack|solidification\s+crack|centre[\s-]*line\s+crack", "solidification cracking", ""),
    (r"\bcracks?\b|cracking", "cracking", ""),
    (r"lack\s+of\s+fusion|cold\s+lap|not\s+fusing", "lack of fusion", ""),
    (r"lack\s+of\s+penetration|incomplete\s+penetration|poor\s+penetration|insufficient\s+penetration", "lack of penetration", ""),
    (r"undercut", "undercut", ""),
    (r"slag\s+inclusion|\bslag\b", "slag inclusion", ""),
    (r"burn[\s-]*through|blow[\s-]*through|\bholes?\s+in\s+the\s+(?:sheet|plate|part)", "burn-through", ""),
    (r"distortion|warp(?:ing|age)?", "distortion", ""),
    (r"undersize\w*\s+nugget|small\s+nugget|nugget\s+(?:is\s+)?too\s+small|small\s+welds?", "undersize nugget", ""),
    (r"no\s+nugget|stick\s+weld|stuck\s+weld|cold\s+weld", "no nugget / stick weld", ""),
    (r"indentation", "excessive indentation", ""),
    (r"interfacial|button\s+does\s+not\s+pull|peels?\s+(?:apart|off)\s+at\s+the\s+interface", "interfacial fracture", ""),
    (r"mushroom\w*|electrode\s+wear|worn\s+(?:electrodes?|tips?)|tip\s+wear", "electrode wear / mushrooming", ""),
    (r"shunting", "shunting", ""),
    (r"misalign\w*", "electrode misalignment", ""),
    (r"weak\s+welds?|welds?\s+(?:are|is|seem|seems|were)\s+weak|low\s+strength|welds?\s+(?:pull|tear|break)\w*\s+apart|fails?\s+(?:the\s+)?peel", "undersize nugget",
     "read a weak weld as an undersize nugget (the documents' first listed cause); the peel test tells it apart"),
]

FIRST_PERSON = re.compile(r"\b(?:I\s+am|I'?m|I\s+have|I'?ve|I\s+get|I\s+got|we\s+are|we'?re|we\s+have|we\s+get|my|our|I\s+keep)\b", re.I)
ASKS = {
    "fix": re.compile(r"what\s+should\s+i\s+do|what\s+can\s+i\s+(?:do|check|change)|how\s+(?:do|can|should)\s+(?:i|we)\s+(?:fix|stop|avoid|prevent|solve|reduce|get\s+rid)|how\s+to\s+(?:fix|stop|avoid|prevent|solve)|"
                      r"what\s+(?:is|are)\s+(?:the\s+)?(?:cause|reason)|why\b|troubleshoot|fix\b|help\b|what\s+to\s+check|what\s+to\s+do|problem|issue", re.I),
    "process": re.compile(r"which\s+(?:welding\s+|joining\s+)?process|what\s+(?:welding\s+|joining\s+)?process|best\s+(?:way|process|method)\s+to\s+(?:weld|join)|how\s+should\s+(?:i|we)\s+(?:weld|join)|which\s+(?:welding\s+)?method", re.I),
    "parameters": re.compile(r"parameters?|settings?|schedule|starting\s+(?:values?|point)|what\s+(?:current|force|time)", re.I),
}

THICKNESS = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(mm|millimet(?:er|re)s?|inch(?:es)?|in\b|\")", re.I)
STACK = re.compile(r"(?<![\w.])(\d)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(mm|millimet(?:er|re)s?|inch(?:es)?|in\b|\")", re.I)


@dataclass
class Sheet:
    thickness_mm: float
    material: str = ""            # a MATERIALS key, or "" when not said


@dataclass
class Scenario:
    text: str
    process: str = ""
    sheets: list = field(default_factory=list)          # [Sheet]
    materials: list = field(default_factory=list)       # families named, in order, without duplicates
    coating: bool = False
    coating_word: str = ""
    symptom: str = ""
    symptom_word: str = ""
    asks: list = field(default_factory=list)            # "fix", "process", "parameters"
    first_person: bool = False
    readings: list = field(default_factory=list)        # how each part was read: [(what was written, how it was read, is_assumption)]
    unclear: list = field(default_factory=list)         # parts the situation needs that were not said or not understood

    def parts(self):
        """How many different kinds of detail the situation gives (process, material, coating, thickness, symptom)."""
        return sum(bool(x) for x in (self.process, self.materials, self.coating, self.sheets, self.symptom))

    @property
    def thinnest(self):
        return min(s.thickness_mm for s in self.sheets) if self.sheets else None

    @property
    def stack_mm(self):
        return sum(s.thickness_mm for s in self.sheets) if self.sheets else None


def _num(text):
    return float(text.replace(",", "."))


def _mm(value, unit):
    return value * INCH if unit.lower().startswith(("in", '"')) else value


def read(text):
    s = Scenario(text=text.strip())
    t = s.text

    found = [(m.start(), k, m.group(0)) for k, (rx, _n) in PROCESSES.items() for m in [re.search(rx, t, re.I)] if m]
    if found:
        pick = min(found, key=lambda f: f[0])                         # the first process named
        s.process = pick[1]
        s.readings.append((pick[2], PROCESSES[pick[1]][1], False))

    for m in STACK.finditer(t):                                       # "2 x 1.2 mm"
        for _ in range(int(m.group(1))):
            s.sheets.append(Sheet(_mm(_num(m.group(2)), m.group(3))))
    stacked = [m.span() for m in STACK.finditer(t)]
    for m in THICKNESS.finditer(t):
        if any(a <= m.start() < b for a, b in stacked):
            continue
        mm = _mm(_num(m.group(1)), m.group(2))
        if 0 < mm < 200:
            s.sheets.append(Sheet(mm))

    names = sorted((m.start(), key, m.group(0).strip()) for key, (rx, _f, _n) in MATERIALS.items() for m in re.finditer(rx, t, re.I))
    for _pos, key, word in names:
        if key not in s.materials:
            s.materials.append(key)
            note = MATERIALS[key][2]
            s.readings.append((word, note.format(word=word) if note else MATERIALS[key][1], bool(note)))
    if VAGUE_STRENGTH.search(t) and not {"ahss", "hsla"} & set(s.materials):
        s.unclear.append("which high-strength steel it is: HSLA, or an advanced high-strength grade such as dual-phase (the documents treat them differently)")
    if s.sheets and len(s.sheets) == len(s.materials):
        for sheet, key in zip(s.sheets, s.materials):
            sheet.material = key
    elif s.sheets and len(s.materials) == 1:
        for sheet in s.sheets:
            sheet.material = s.materials[0]
        if len(s.sheets) > 1:
            s.readings.append(("one material for several sheets", f"every sheet read as {MATERIALS[s.materials[0]][1]}", True))
    elif s.sheets and s.materials:
        s.readings.append(("the sheets and the materials", "not paired: the text does not say which sheet is which material", True))

    c = COATINGS.search(t)
    if c:
        s.coating, s.coating_word = True, c.group(0)
        zinc = re.search(r"zinc|galv|hot|\bG[IA]\b", c.group(0), re.I)
        s.readings.append((c.group(0), "zinc-coated steel (hot-dip or galvannealed)" if zinc else "a coated sheet", False))

    for rx, canonical, note in SYMPTOM_WORDS:
        m = re.search(rx, t, re.I)
        if m:
            s.symptom, s.symptom_word = canonical, m.group(0)
            s.readings.append((m.group(0), canonical + (": " + note if note else ""), bool(note)))
            break

    s.first_person = bool(FIRST_PERSON.search(t))
    s.asks = [k for k, rx in ASKS.items() if rx.search(t)]
    return s
