"""Source data for the 20 cached FAQs + a builder that verifies them against the documents.

Each FAQ lists `sources` as (document prefix, exact quote from the document). The builder finds the passage
containing each quote (so cited passage ids are always real) and then checks, for every bullet, that each
number, standard code and acronym it states appears in the passages that bullet cites. Run:  python faq_src.py
"""
from welding_ai import paths
import json
import re
import sys

ROOT = paths.ROOT

FAQS = [
 dict(id="scope", tags=["scope"],
  q="What does this reference set cover, and what are its limits?",
  aliases=["what is in this documentation", "what topics do these documents cover", "what can you answer",
           "what are the known gaps of this reference set", "scope of the welding reference", "what is not covered in these documents"],
  answer="It is a working reference of seventy-eight chapters in ten volumes on industrial welding and joining, and it states its own limits plainly.",
  src=[("00_", "seventy-eight chapters in ten volumes"),
       ("78_", "Every chapter in this reference set ends with a Known Gaps section"),
       ("78_", "National deviations to EN ISO adoptions"),
       ("78_", "What current and voltage should I use?")],
  points=["Every chapter ends with a Known Gaps section, and the last chapter collects them in one place. [2]",
          "Specifically not covered: national deviations to EN ISO adoptions, jurisdictional amendments to ASME adoption, occupational health and safety regulation, environmental and emissions regulation, classification society rules, nuclear regulatory requirements and customer-specific specifications. [3]",
          "It says it cannot answer: what current and voltage to use (a qualified WPS answers this), whether a weld is acceptable (needs the governing acceptance criteria and an inspection result), what preheat is needed (calculate it from the actual material, thickness, hydrogen level and restraint), or which brand of consumable to buy (it names no products). [4]"]),

 dict(id="process_selection", tags=["selection"],
  q="How do I choose the right welding process for a job?",
  aliases=["how to select a welding process", "which welding process should I use", "process selection framework",
           "how do I decide between welding processes", "steps to select the joining process", "choosing a weld process"],
  answer="Screen the options in a fixed order: eliminating screens first, optimizing screens last.",
  src=[("73_", "Running them in the wrong order produces the classic failure"),
       ("73_", "Material says A, volume says B"),
       ("73_", "selecting on productivity before feasibility")],
  points=["The order is: joining family, material, thickness and joint geometry, and access and position (all eliminating), then volume and rate (optimizing), quality level and verification (constraining), and finally capability and cost of change (optimizing). [1]",
          "The order matters because a process chosen on productivity and cost can be ruled out later by a material or access constraint discovered during industrialization. [1]",
          "When criteria conflict: material wins over volume; access wins over productivity unless fixturing can create access; quality wins over cost where it is a code or safety requirement; if two candidates survive every screen, decide on cost, then risk, then flexibility. [2]",
          "The most common selection error is choosing on productivity before feasibility. [3]"]),

 dict(id="gmaw_transfer", tags=["gmaw"],
  q="What are the metal transfer modes in GMAW (MIG/MAG)?",
  aliases=["gmaw transfer modes", "mig transfer modes short circuit globular spray pulsed", "types of metal transfer in mig welding",
           "difference between short-circuit and spray transfer", "what is globular transfer", "four metal transfer modes"],
  answer="GMAW has four metal transfer modes: short-circuiting, globular, spray and pulsed spray. They are physical regimes set by current, voltage, wire diameter and shielding gas, not settings on a dial.",
  src=[("39_", "Four modes exist, and they are not settings on a dial"),
       ("39_", "Mechanism. At low current and voltage, the wire feeds forward until it physically touches the pool"),
       ("39_", "globular transfer sits between short-circuit and spray")],
  points=["Short-circuiting: lowest current, any gas including CO₂, all positions, moderate but controllable spatter; used for thin material, open roots and out-of-position work. [1]",
          "Globular: low-to-medium current, flat and horizontal only, the highest spatter, and not deliberately selected. [1]",
          "Spray: high current, needs ≥ ~80 % argon, flat and horizontal, very low spatter; used for thick material and high deposition. [1]",
          "Pulsed spray: low average current with high peaks, ≥ ~80 % argon, all positions, very low spatter; used for all-position work needing spray quality. [1]",
          "Globular transfer sits between short-circuit and spray and is the worst operating region on every count, so run the process deliberately on one side of it. [3]"]),

 dict(id="gmaw_porosity", tags=["gmaw", "defects"],
  q="What causes porosity in GMAW and how is it corrected?",
  aliases=["mig welding porosity causes", "why do i get porosity in gmaw", "gmaw porosity troubleshooting",
           "how to fix porosity in mig welds", "gas shielding porosity in wire welding", "porosity gets worse when i increase gas flow"],
  answer="In GMAW, porosity almost always comes from a compromised gas shield or from contamination of the joint.",
  src=[("39_", "Too low: porosity. Too high: turbulence entrains air"),
       ("39_", "Blocked diffuser disturbs the gas envelope"),
       ("39_", "Rust, zinc coating, oil and moisture generate gas in the pool"),
       ("39_", "Card 3 — Porosity that worsens when gas flow is increased"),
       ("39_", "porosity from a long arc")],
  points=["Gas flow: too low gives porosity, and too high causes turbulence that entrains air, which also gives porosity. [1]",
          "Gas hardware: a blocked diffuser or spatter built up inside the nozzle disturbs the gas envelope; insufficient pre-flow gives start porosity and insufficient post-flow gives crater porosity. [2]",
          "Arc and stick-out: excessive voltage (a long arc) gives porosity, and excessive stick-out gives poor gas coverage. [5]",
          "Contamination: rust, zinc coating, oil and moisture generate gas in the pool, so clean the joint. [3]",
          "If porosity worsens when gas flow is increased, check for flow above the laminar limit for the nozzle, a partly blocked nozzle, excessive stick-out and draught; return flow to the correct value, clean the nozzle and diffuser, screen the work area and leak-check the gas path. [4]"]),

 dict(id="shielding_gas", tags=["gas"],
  q="Which shielding gas should I use for carbon steel, stainless steel and aluminium?",
  aliases=["shielding gas selection", "what gas for mig welding steel", "argon co2 mix for carbon steel", "best gas for tig welding aluminium",
           "gas for stainless steel welding", "what gas do i use for aluminium mig"],
  answer="The gas depends on the process and material: argon-rich mixtures suit spray transfer, and aluminium needs an inert gas.",
  src=[("30_", "GMAW, carbon steel, short-circuit"),
       ("30_", "GTAW, aluminium")],
  points=["GMAW carbon steel, short-circuit: Ar-CO₂ mixtures with moderate CO₂, or CO₂; higher CO₂ gives penetration at the cost of spatter. [1]",
          "GMAW carbon steel, spray: an argon-rich mixture (≥ ~80 % Ar), often with a few percent O₂ or CO₂; pulsed transfer needs the same argon-rich base. [1]",
          "GMAW austenitic stainless: argon with small additions of O₂ or CO₂, often as a tri-mix. [1]",
          "GMAW aluminium: pure argon, or argon-helium for thick sections; inert gases only, no oxidizing component. [1]",
          "GTAW: argon for steel and stainless (argon-helium for more heat; argon-hydrogen for austenitic stainless and nickel); for aluminium, argon on AC and argon-helium for thick sections. [1][2]"]),

 dict(id="filler_designation", tags=["filler"],
  q="How do I read a filler metal designation such as ER70S-6 or E7018?",
  aliases=["what does er70s-6 mean", "decode e7018 electrode", "filler wire classification meaning", "how to select filler metal",
           "why is er70s-6 used for general fabrication", "what does the 70 in e7018 stand for"],
  answer="The letters and digits encode the form of the filler, its minimum tensile strength and its composition or covering.",
  src=[("29_", "Minimum tensile strength of the deposit, 70 ksi"),
       ("29_", "usable on AC or DCEP"),
       ("29_", "This is why ER70S-6 dominates general fabrication")],
  points=["ER70S-6: ER means electrode or rod; 70 is the minimum tensile strength of the deposit, 70 ksi; S means solid wire; 6 is the chemical composition designator. [1]",
          "ER70S-6 has higher manganese and silicon than ER70S-3, so it tolerates more mill scale and light rust and gives a more fluid pool with better wetting; that is why it dominates general fabrication. [3]",
          "E7018: 70 is the minimum tensile strength, 70 ksi; the digit 1 means all positions (2 is flat and horizontal fillet only; 4 is all positions including vertical down); the digit 8 means a low-hydrogen potassium covering with iron powder, usable on AC or DCEP. [2]"]),

 dict(id="preheat", tags=["preheat"],
  q="How is preheat and interpass temperature decided for carbon and low-alloy steel?",
  aliases=["what preheat temperature do i need", "how to calculate preheat", "preheat for carbon steel", "carbon equivalent and preheat",
           "how do i determine preheat", "what is preheat and why is it needed"],
  answer="Preheat is calculated from the actual joint, not looked up from carbon equivalent alone.",
  src=[("34_", "Preheat acts before and during welding, to control the cooling rate"),
       ("23_", "AWS D1.1 Annex B, for instance, does not apply a single carbon equivalent cutoff"),
       ("23_", "A carbon equivalent is a hardenability index, not a preheat prescription"),
       ("34_", "Confusing them produces real errors"),
       ("78_", "What preheat do I need?")],
  points=["Preheat acts before and during welding to control the cooling rate and keep the joint out of the hydrogen cracking window while hydrogen escapes. [1]",
          "A carbon equivalent is a hardenability index, not a preheat prescription: on its own it says nothing about hydrogen level, restraint or thickness. [3]",
          "AWS D1.1 Annex B computes minimum preheat from composition, thickness, hydrogen level and restraint rather than applying a single carbon equivalent cutoff. [2]",
          "Preheat, interpass temperature and post-weld heat treatment are different controls; a specified PWHT does not compensate for inadequate preheat. [4]",
          "The reference set says to calculate preheat from the actual material, thickness, hydrogen level and restraint using the applicable standard's method. [5]"]),

 dict(id="hydrogen_cracking", tags=["cracking", "preheat"],
  q="How is hydrogen cracking prevented when welding steel?",
  aliases=["hydrogen cracking prevention", "cold cracking in steel welds", "low hydrogen electrodes storage", "how to avoid hydrogen induced cracking",
           "why must low hydrogen electrodes be kept in an oven", "delayed cracking after welding"],
  answer="Hydrogen cracking is prevented by controlling hydrogen and the cooling rate: consumable handling, joint cleanliness and preheat.",
  src=[("23_", "It holds the joint above the temperature range in which hydrogen cracking operates"),
       ("34_", "it is why preheat must be maintained rather than merely applied"),
       ("38_", "Low-hydrogen electrodes are hygroscopic"),
       ("38_", "Enforce oven and quiver discipline"),
       ("23_", "The Cheapest Control Is Almost Always Consumable Handling"),
       ("38_", "Observe any specified delay before final non-destructive testing")],
  points=["Preheat holds the joint above the temperature range where hydrogen cracking operates for longer, giving diffusible hydrogen time to escape while the metal is still warm. [1]",
          "Preheat must be maintained rather than merely applied, and maintaining temperature after welding (a hydrogen soak) is a recognized control on thick, restrained, high-strength joints. [2]",
          "Low-hydrogen electrodes are hygroscopic: the covering absorbs moisture from the moment the sealed packet is opened, so enforce oven and quiver discipline and verify the electrode class against the WPS. [3][4]",
          "Consumable exposure and joint surface condition account for a large share of root causes in cracking investigations, so consumable handling is usually the cheapest control. [5]",
          "Where hydrogen cracking is credible, observe any specified delay before final non-destructive testing. [6]"]),

 dict(id="stainless", tags=["stainless"],
  q="How do I avoid sensitization and hot cracking when welding stainless steel?",
  aliases=["stainless steel welding problems", "sensitization in austenitic stainless", "hot cracking in stainless steel welds", "why is ferrite important in stainless welds",
           "welding 304 and 316 stainless", "how to weld stainless steel without carbide precipitation"],
  answer="Austenitic stainless steels fail in two characteristic ways in welding: sensitization and hot cracking.",
  src=[("24_", "Sensitization; hot cracking if ferrite-free; heavy distortion"),
       ("24_", "Heat input control helps all cases, because sensitization is time-at-temperature dependent"),
       ("24_", "Why each stainless family fails differently"),
       ("24_", "Ferrite in Austenitic Weld Metal: The Hot-Cracking Control")],
  points=["Austenitic grades (304/304L, 316/316L, 321, 347, 310) are prone to sensitization, to hot cracking if the weld metal is ferrite-free, and to heavy distortion. [1]",
          "Sensitization depends on time at temperature, so lower heat input means less time in the sensitizing range and less carbide precipitated. [2]",
          "Ferrite in austenitic weld metal is the hot-cracking control, so filler selection and ferrite control are decided deliberately. [4]",
          "Heat input windows and root purging are also part of the control; duplex, ferritic and martensitic grades fail differently (phase imbalance, grain growth and hydrogen cracking respectively). [3]"]),

 dict(id="aluminium", tags=["aluminium"],
  q="Why is aluminium difficult to weld and what are the main problems?",
  aliases=["aluminium welding problems", "aluminum weldability", "why does aluminium porosity happen", "oxide layer on aluminium welding",
           "hot cracking in aluminium", "challenges of welding aluminum alloys"],
  answer="Aluminium is hard to weld because of its oxide film, hydrogen porosity, high thermal conductivity and hot cracking.",
  src=[("25_", "Aluminium oxide forms instantly on exposed aluminium"),
       ("25_", "Hydrogen porosity | RT; visible on sectioning"),
       ("25_", "Its thermal conductivity is roughly four to six times that of steel"),
       ("25_", "A silicon-rich 4xxx filler moves it well past the peak")],
  points=["The oxide forms instantly, is chemically stable and electrically insulating, and melts at a temperature far above the metal beneath it; it also absorbs moisture, making it a hydrogen reservoir. [1]",
          "Hydrogen porosity is the characteristic defect: causes include moisture in the oxide film, hydrocarbon contamination, wet or dirty filler wire, gas-line leaks, turbulent gas flow and condensation; the corrective actions are degreasing then mechanically cleaning immediately before welding, controlling filler storage, leak-checking the gas path and correcting the flow. [2]",
          "Thermal conductivity is roughly four to six times that of steel, so on a long weld the workpiece heats up progressively and parameters correct at the start of a seam are too hot at the end. [3]",
          "Hot cracking depends on alloy chemistry, so a silicon-rich 4xxx filler moves the weld well past the peak into the crack-tolerant region: filler selection for aluminium is a crack-avoidance decision. [4]"]),

 dict(id="dissimilar", tags=["dissimilar"],
  q="What are the main problems when joining dissimilar metals?",
  aliases=["dissimilar metal welding problems", "welding steel to aluminium", "joining stainless to carbon steel", "intermetallic layer in aluminium steel joint",
           "why do dissimilar joints fail", "thermal expansion mismatch in dissimilar welds"],
  answer="A dissimilar joint is a different engineering problem because properties change abruptly across a short distance.",
  src=[("27_", "The joint contains a property gradient"),
       ("27_", "Austenitic stainless expands roughly fifty percent more than ferritic steel"),
       ("27_", "the Fe-Al intermetallic layer that forms at the interface is brittle at any thickness"),
       ("27_", "carbon migration (Section 5)"),
       ("27_", "Mechanical joining and adhesive bonding")],
  points=["Strength, thermal expansion, conductivity and electrochemical potential all step across a short distance; gradients concentrate stress and drive corrosion. [1]",
          "Thermal expansion mismatch: austenitic stainless expands roughly fifty percent more than ferritic steel per degree, which creates cyclic strain at the interface in thermally cycled service. [2]",
          "Iron-aluminium joints form a brittle Fe-Al intermetallic layer that becomes a crack path once it exceeds a few micrometres. [3]",
          "Failures often appear at or near the interface on the ferritic side after long service, driven by carbon migration, thermal expansion mismatch, creep strength mismatch and residual stress. [4]",
          "Mechanical joining (self-piercing rivets, flow-drill screws) and structural adhesives avoid the metallurgical problem entirely. [5]"]),

 dict(id="wps_pqr", tags=["qualification"],
  q="What is the difference between a WPS, a PQR and a welder qualification?",
  aliases=["wps vs pqr", "what is a welding procedure specification", "procedure qualification record meaning", "prequalified wps",
           "does a wps replace welder qualification", "wpq welder performance qualification"],
  answer="The PQR proves what happened once; the WPS states what may be done in production; the range between them is set by the code's essential variable rules.",
  src=[("31_", "The relationship in one sentence"),
       ("31_", "It does not remove the welder qualification requirement"),
       ("32_", "Qualifications do not transfer between systems automatically"),
       ("32_", "A welder qualified vertical-down is not thereby qualified vertical-up")],
  points=["A prequalified WPS may be used without a supporting PQR only if every one of the code's prequalification conditions is met: approved base metal, filler metal and process, a prequalified joint detail, preheat per the code's table, and parameters within limits. [2]",
          "Prequalification applies to the procedure, not to the people: it does not remove the welder qualification requirement. [2]",
          "Welder qualification is specific to how the test was done: vertical-down and vertical-up are distinct, and a welder qualified in one is not thereby qualified in the other. [4]",
          "Qualifications do not transfer between systems automatically: a welder qualified to ISO 9606-1 is not thereby qualified under ASME Section IX. [3]"]),

 dict(id="qualification_standards", tags=["standards", "qualification"],
  q="Which standards apply to welder and procedure qualification?",
  aliases=["iso 9606 vs asme ix", "standards for welder certification", "which code covers welding procedure qualification", "iso 15614 and iso 15607",
           "aws d1.1 prequalification", "welder qualification standards"],
  answer="Three systems dominate: the ISO series, ASME Section IX and AWS D1.1.",
  src=[("75_", "Manual and partly mechanized welders"),
       ("75_", "ISO 15607 sets out qualification by procedure test"),
       ("75_", "AWS D1.1 offers prequalification, which ISO does not")],
  points=["Manual and partly mechanized welders: ISO 9606-1 (steel), -2 (aluminium), -3/-4/-5 (nickel, copper, titanium/zirconium); AWS D1.1 Clause 6 Part C; ASME Section IX Article III. [1]",
          "Operators and setters of mechanized or automatic equipment: ISO 14732. [1]",
          "Procedures: ISO 15607 sets out qualification by procedure test (ISO 15614), pre-production test (15613), tested consumables (15610), previous experience (15611) and standard welding procedure (15612). [2]",
          "AWS D1.1 offers prequalification, which ISO does not. [3]"]),

 dict(id="imperfections", tags=["quality", "defects"],
  q="What are the common weld imperfections and how are acceptance limits set?",
  aliases=["weld acceptance criteria", "iso 5817 quality levels b c d", "how is weld quality level chosen", "are cracks allowed in welds",
           "what are the weld imperfection acceptance limits", "difference between quality level b and c"],
  answer="Acceptance is decided against a quality level, not a single pass/fail line.",
  src=[("36_", "ISO 5817 provides quality levels for imperfections in fusion-welded joints"),
       ("36_", "The level is selected by the application, not by the fabricator"),
       ("36_", "In most codes and at most quality levels, cracks are not permitted at any size"),
       ("36_", "No acceptance limits are reproduced")],
  points=["ISO 5817 gives quality levels for imperfections in fusion-welded joints in steel, nickel, titanium and their alloys (beam welding excluded): B is stringent, C intermediate and D moderate, the most permissive. [1]",
          "The level is set by the design specification, product standard or contract, based on consequence of failure, loading (static or fatigue) and service conditions, not by the fabricator. [2]",
          "Each imperfection type has its own limit, so a weld can meet level B for porosity and fail level C for undercut. [2]",
          "Cracks are the most serious class of imperfection; in most codes and at most quality levels they are not permitted at any size. [3]",
          "The reference set does not reproduce the numeric limits, which belong to the standard itself. [4]"]),

 dict(id="ndt_methods", tags=["ndt"],
  q="Which NDT methods are used to inspect welds and how is the method chosen?",
  aliases=["non destructive testing of welds", "when to use ultrasonic vs radiographic testing", "magnetic particle testing limitations", "how to choose an ndt method",
           "iso 17635 method selection", "surface vs subsurface weld inspection methods"],
  answer="Method choice is governed by ISO 17635 and decided by material, joint, expected defects, access, acceptance criteria and consequence.",
  src=[("35_", "Selection is governed in international practice by ISO 17635"),
       ("35_", "Ferromagnetic or not"),
       ("35_", "Whether it breaks the surface"),
       ("35_", "ISO 17636 (radiographic testing of welds)"),
       ("35_", "Acceptance-level standards referenced in Section 14")],
  points=["Material: whether it is ferromagnetic decides magnetic particle testing, which is eliminated for austenitic stainless steel, aluminium and most nickel alloys; coarse austenitic weld metal constrains ultrasonics. [2]",
          "Joint type and thickness: thin material limits ultrasonic access; heavy sections limit radiographic practicality and dose. [2]",
          "Expected defects: a process prone to lack of fusion needs a method that finds planar defects; one prone to porosity is well served by radiography. [2]",
          "Access: radiography needs both sides; ultrasonics needs one. [2]",
          "Penetrant and magnetic particle testing are surface methods; a subsurface defect is invisible to them regardless of size. [3]",
          "Method standards: ISO 17636 (radiographic), ISO 17640 (ultrasonic), ISO 3452 (penetrant), ISO 13588 (phased array), ISO 10863 (time-of-flight diffraction) and ISO 17643 (eddy current); acceptance levels are in ISO 23277, ISO 23278, ISO 10675-1 and ISO 11666. [4][5]"]),

 dict(id="safety", tags=["safety"],
  q="What are the main hazards in welding and the safety controls?",
  aliases=["welding safety hazards", "confined space welding safety", "how to prevent welding fume exposure", "arc eye and electric shock prevention",
           "welding fire safety", "ansi awsz49.1 welding safety"],
  answer="The hazards that actually injure welders are shock, radiation, fume, fire and asphyxiation, and each has specific controls.",
  src=[("37_", "Fume exposure causes the greatest total burden"),
       ("37_", "Fatal secondary-circuit shock"),
       ("37_", "Controls: confined space entry procedures with permit"),
       ("37_", "ANSI/AWS Z49.1, Safety in Welding")],
  points=["Burns and eye injuries are the most frequent injuries; fire and explosion cause the largest single-event losses; fume causes the greatest total burden of serious harm but is chronic and under-perceived; confined-space asphyxiation causes the highest proportion of fatalities per event. [1]",
          "Shock: dry clothing and gloves, equipment inspection, voltage-reducing devices, and the return connected at the work. [2]",
          "Arc eye in a bystander: screens, matt UV-absorbing surfaces and screened bays in shared spaces. [2]",
          "Fume: remove coatings and use local exhaust at source for zinc oxide from galvanized steel or brass (metal fume fever); identify cadmium in plating or brazing alloys before work; exclude chlorinated solvents from welding areas. [2]",
          "Fire: extend the fire watch after work and check the far side; drums and tanks need cleaning, purging or inerting and gas testing. [2]",
          "Confined spaces: entry permit, atmosphere testing before and continuously during entry, forced ventilation, an attendant outside, rescue arrangements before entry, gas supplies isolated during breaks, and never enter to rescue without breathing apparatus. [3]",
          "The principal framework standard is ANSI/AWS Z49.1, Safety in Welding, Cutting, and Allied Processes. [4]"]),

 dict(id="spot_parameters", tags=["spot"],
  q="What are typical resistance spot welding parameters for low-carbon steel?",
  aliases=["spot welding current for steel", "spot weld nugget diameter rule", "electrode face diameter for spot welding", "iso 14373 spot welding parameters",
           "rsw parameters thickness", "minimum nugget size spot weld"],
  answer="For low-carbon steel the reference gives geometry rules as functions of the thinnest sheet thickness and a typical production current range.",
  src=[("20_", "8–15 kA typical production current range for steel RSW"),
       ("20_", "The metric geometry rules (electrode face diameter, minimum nugget diameter"),
       ("20_", "The imperial first-trial estimate (force, time, current) is a function of ΣT"),
       ("20_", "0.4–3 mm low-carbon-steel scope"),
       ("20_", "The 5√t electrode-diameter coefficient")],
  points=["Typical production current for steel resistance spot welding is 8–15 kA. [1]",
          "Electrode face diameter, minimum nugget diameter, minimum spacing and minimum edge distance are all functions of t, the thickness of the thinnest sheet in the stack in millimetres, per ISO 14373 guidance for low-carbon steel. [2]",
          "The coefficients are 5√t for electrode diameter and 4√t–5√t for nugget diameter; they were developed around low-carbon steel and are not covered for aluminium. [5]",
          "The first-trial force, time and current estimate is a function of ΣT, the sum of all sheet thicknesses in the stack, not of the thinnest sheet. [3]",
          "The scope is low-carbon steel of about 0.4–3 mm, coated or uncoated. [4]"]),

 dict(id="spot_expulsion", tags=["spot", "defects"],
  q="What causes expulsion or spatter in resistance spot welding and how is it fixed?",
  aliases=["spot weld expulsion causes", "why is my spot weld splashing", "spot welding spatter troubleshooting", "expulsion suddenly increased in spot welding",
           "weld splash in resistance welding", "how to reduce expulsion in rsw"],
  answer="Expulsion is ejected molten metal, and it occurs when the heat input is more than the available force and geometry can contain.",
  src=[("12_", "Heat input too high for the available force/geometry to contain"),
       ("12_", "Expulsion suddenly increases"),
       ("03_", "Expulsion increasing after many welds without a program change")],
  points=["Typical cause: current, time or both set too high relative to the force and electrode geometry that must contain the weld. [1]",
          "If expulsion suddenly increases, check in this order: force, tip condition, current and time settings, then material surface condition; do not assume only the current setting changed. [2]",
          "If it increases gradually after many welds with no program change, suspect contact-area and current-density drift from wear or a thermal-state change; check the current/resistance trace, the tip face, and coolant flow and temperature. [3]",
          "It shows as spatter marks or ejected metal at the joint and as a recorded spatter event on the current/voltage trace. [1]"]),

 dict(id="electrode_life", tags=["spot", "maintenance"],
  q="How do I maintain electrodes and manage electrode life in spot welding?",
  aliases=["spot welding tip dressing", "what is electrode mushrooming", "when to dress spot welding electrodes", "electrode tip wear in resistance welding",
           "how often should tips be dressed", "spot weld electrode material selection"],
  answer="Electrode life is managed by dressing tips on a defined trigger before mushrooming lets current density drift.",
  src=[("03_", "Mushrooming — plastic deformation of the tip face"),
       ("03_", "A tip that has mushroomed since the last dressing"),
       ("03_", "Confirm that the defined dressing trigger has actually been reached"),
       ("03_", "Inspect the existing tip condition before cutting"),
       ("03_", "Class 1/2-type alloys (high conductivity) are the default choice"),
       ("03_", "Weld strength trending down over a production run")],
  points=["Mushrooming is plastic deformation of the tip face that increases the effective face diameter and reduces current density for a given programmed current; it is the dominant wear mode on bare steel. [1]",
          "The controller still reports the programmed current, but the process the workpiece experiences has drifted. [2]",
          "Dress on a defined trigger (a weld count against the documented interval for the material and coating, a resistance or quality-trend signal crossing its threshold, or a scheduled maintenance point) rather than ad hoc or purely visually. [3]",
          "Before cutting, inspect and record the tip: face diameter, mushrooming, material pickup, cracking, and taper or seating wear. [4]",
          "Class 1/2-type high-conductivity alloys are the default for standard low-carbon-steel body-panel spot welding; harder, lower-conductivity classes are chosen for wear resistance or for aggressive coatings such as aluminium-coated or heavily galvanized steel. [5]",
          "Weld strength trending down over a production run points to tip wear (mushrooming) or coating pickup. [6]"]),

 dict(id="tig_polarity", tags=["gtaw"],
  q="How do polarity and tungsten selection work in TIG (GTAW) welding?",
  aliases=["tig welding polarity dcen ac", "which tungsten should i use for tig", "why use ac for aluminium tig", "lanthanated vs thoriated tungsten",
           "gtaw polarity for stainless steel", "tig ac balance and cleaning action"],
  answer="Use DCEN for most metals and AC for aluminium and magnesium.",
  src=[("41_", "Set polarity — DCEN for steel"),
       ("41_", "The pattern is a straightforward trade"),
       ("41_", "EWLa-1.5"),
       ("41_", "Grinding thoriated tungsten produces low-level radioactive dust")],
  points=["Polarity: DCEN for steel, stainless, nickel, titanium and copper; AC with an appropriate balance for aluminium and magnesium. [1]",
          "DCEN puts the heat where it is wanted but provides no cleaning; DCEP cleans well but cannot carry useful current without an impractically large electrode; AC alternates to do both. [2]",
          "Tungsten: lanthanated EWLa-1.5 (about 1.3–1.7 % lanthanum oxide, gold-coded) is non-radioactive, with conductivity resembling thoriated. [3]",
          "Grinding thoriated tungsten produces low-level radioactive dust, which is why many organizations moved to lanthanated or ceriated electrodes; use a dedicated wheel with extraction. [4]"]),
]

# ------------------------------------------------------------------------------------------ builder
# Lowercase and collapse whitespace, for substring matching against passage text.
def norm(s):
    return " ".join(s.split()).lower()

STOP = set("the a an of in on for to and or is are was were be by at from as that this these those it its with without not no than then their there into per any all each other such can may must shall do does did which who what when where how why".split())
NUM = re.compile(r"\d+(?:[.,–-]\d+)*")
ACR = re.compile(r"\b(?:[A-Z]{2,}[A-Za-z0-9\-]*|[A-Z]+\d[A-Za-z0-9\-]*|[A-Z][a-z]?\d[A-Za-z0-9\-]*)\b")


# Resolve each FAQ's source quotes to real chunk ids and verify every cited number/standard/acronym and
# most content words of each bullet actually appear in its cited passages.
def build():
    chunks = json.loads((paths.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    normed = [norm(c["text"]) for c in chunks]
    problems, out = [], []
    for f in FAQS:
        srcs = []
        for prefix, quote in f["src"]:
            q = norm(quote)
            hits = [i for i, c in enumerate(chunks) if c["source"].startswith(prefix) and q in normed[i]]
            if not hits:
                problems.append(f"[{f['id']}] quote not found in {prefix}*: {quote!r}")
                srcs.append(None)
            else:
                srcs.append(hits[0])
        pts = []
        for p in f["points"]:
            cites = [int(x) for x in re.findall(r"\[(\d+)\]", p)]
            body = re.sub(r"\s*\[\d+\]", "", p)
            cited_ids = [srcs[c - 1] for c in cites if 0 < c <= len(srcs) and srcs[c - 1] is not None]
            if not cited_ids:
                problems.append(f"[{f['id']}] point has no valid citation: {p[:60]}")
            cited_text = " ".join(normed[i] for i in cited_ids)
            # every number / standard code / acronym stated must appear in the cited passages
            miss = []
            for tok in NUM.findall(body) + ACR.findall(body):
                t = norm(tok).replace("–", "-")
                ct = cited_text.replace("–", "-")
                if t not in ct and t.rstrip("s") not in ct:
                    miss.append(tok)
            # and most content words should come from the cited passages
            words = [w for w in re.findall(r"[a-z][a-z\-]{3,}", norm(body)) if w not in STOP]
            cov = sum(1 for w in words if w in cited_text or w.rstrip("s") in cited_text) / max(len(words), 1)
            if miss:
                problems.append(f"[{f['id']}] tokens not in cited passages {miss}: {body[:70]}...")
            pts.append({"text": p, "cites": cites, "word_support": round(cov, 2)})
        out.append({"id": f["id"], "question": f["q"], "aliases": f["aliases"], "answer": f["answer"], "points": pts,
                    "sources": [i for i in srcs if i is not None], "tags": f["tags"],
                    "source_quotes": [q for _, q in f["src"]]})
    return out, problems


if __name__ == "__main__":
    faqs, problems = build()
    (paths.INDEX_DIR / "faq.json").write_text(json.dumps(faqs, ensure_ascii=False, indent=1), encoding="utf-8")
    low = [(f["id"], p["word_support"]) for f in faqs for p in f["points"] if p["word_support"] < 0.6]
    print(f"{len(faqs)} FAQs written to index/faq.json | {sum(len(f['points']) for f in faqs)} cited bullets")
    print("PROBLEMS:" if problems else "All quotes resolved; every number/standard/acronym in every bullet appears in its cited passages.")
    for p in problems:
        sys.stdout.buffer.write((" - " + p + "\n").encode("utf-8", "replace"))
    print("bullets with word support < 0.60 (check paraphrase):", low or "none")
