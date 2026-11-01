"""Formulas: the safe calculator, the rules for saving, the shipped formulas against their own worked examples and source passages,
the storage rules (who may change what) and the question matcher."""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from welding_ai import paths
from welding_ai.formulas import ask, expr, simple, validate
from welding_ai.formulas.calc import CalcError, calculate
from welding_ai.formulas.store import FormulaError, FormulaStore

SEED = json.loads(paths.FORMULAS_SEED.read_text(encoding="utf-8"))


def seed(formula_id):
    return validate.clean(next(e for e in SEED if e["id"] == formula_id))


def minimal():
    """A small valid formula to break one rule at a time."""
    return {
        "name": "Test area", "purpose": "Used to check the rules for saving a formula.", "source": {"reference": "Test document, section 1"},
        "assumptions": ["Only a test."],
        "variables": [{"symbol": "w", "name": "Width", "meaning": "Width of the plate in millimetres.", "unit": "mm", "min": 0},
                      {"symbol": "h", "name": "Height", "meaning": "Height of the plate in millimetres.", "unit": "mm", "min": 0}],
        "outputs": [{"symbol": "A", "name": "Area", "unit": "mm2", "expr": "w * h", "decimals": 1}],
        "examples": [{"title": "2 by 3", "inputs": {"w": 2, "h": 3}, "expected": {"A": 6}}],
    }


class ExprTests(unittest.TestCase):
    def run_expr(self, text, **values):
        return expr.evaluate(expr.parse(text), values)

    def test_arithmetic(self):
        self.assertAlmostEqual(self.run_expr("5 * sqrt(t)", t=4), 10)
        self.assertAlmostEqual(self.run_expr("a + b / 2 - 1", a=3, b=4), 4)
        self.assertAlmostEqual(self.run_expr("-x ** 2", x=3), -9)
        self.assertTrue(self.run_expr("x > 0.08", x=0.1))

    def test_nothing_but_arithmetic_is_accepted(self):
        for bad in ("__import__('os').system('x')", "a.b", "[1, 2]", "1 if x else 2", "lambda: 1", "'text'", "open('f')", "(1).real",
                    "x[0]", "x == 1", "1 < 2 < 3", "sqrt(x=4)", "pow(2, 3)", "True", "x; y", "", "x" * 400):
            with self.assertRaises(expr.ExprError, msg=bad):
                expr.parse(bad)

    def test_results_must_be_real_and_sensible(self):
        for text, values in (("1 / x", {"x": 0}), ("sqrt(x)", {"x": -1}), ("x ** 0.5", {"x": -4}), ("x ** 99", {"x": 2}), ("log(x)", {"x": 0}), ("x * x", {"x": 1e16})):
            with self.assertRaises(expr.ExprError, msg=text):
                self.run_expr(text, **values)
        with self.assertRaises(expr.ExprError):
            self.run_expr("a + b", a=1)                                  # b has no value

    def test_names_leave_out_functions(self):
        self.assertEqual(expr.names(expr.parse("sqrt(a) + max(b, c) * 2")), {"a", "b", "c"})

    def test_substituted_reads_like_arithmetic(self):
        tree = expr.parse("V * I * 60 / (v * 1000)")
        self.assertEqual(expr.substituted(tree, {"V": 28, "I": 260, "v": 350}), "28 × 260 × 60 ÷ (350 × 1000)")
        self.assertEqual(expr.substituted(expr.parse("5 * sqrt(t)"), {"t": 1.4}), "5 × sqrt(1.4)")


class CalcTests(unittest.TestCase):
    def test_arc_energy_and_heat_input(self):
        f = seed("arc_energy_heat_input")
        r = calculate(f, {"V": 29, "I": 290, "v": 400, "eta": 0.8})
        got = {o["symbol"]: o["text"] for o in r["outputs"]}
        self.assertEqual(got, {"arc_energy": "1.26", "heat_input": "1.01"})        # the figures printed in document 22, section 16
        self.assertIn("29 × 290 × 60 ÷ (400 × 1000)", r["outputs"][0]["steps"])

    def test_optional_input_left_out_skips_only_what_needs_it(self):
        r = calculate(seed("arc_energy_heat_input"), {"V": 28, "I": 260, "v": 350})
        self.assertEqual([o["symbol"] for o in r["outputs"]], ["arc_energy"])
        self.assertEqual(r["outputs"][0]["text"], "1.25")                         # document 22, section 3
        self.assertEqual(r["skipped"][0]["symbol"], "heat_input")
        self.assertIn("Thermal efficiency", r["skipped"][0]["message"])

    def test_optional_input_with_a_default_is_assumed_and_said_so(self):
        r = calculate(seed("ce_iiw"), {"C": 0.2, "Mn": 1.2})
        self.assertAlmostEqual(r["outputs"][0]["value"], 0.2 + 1.2 / 6)
        self.assertEqual(len(r["assumed"]), 5)
        self.assertTrue(all("was not given" in a for a in r["assumed"]))

    def test_missing_required_input_is_reported_not_guessed(self):
        with self.assertRaises(CalcError) as cm:
            calculate(seed("arc_energy_heat_input"), {"V": 28})
        self.assertEqual(sorted(cm.exception.missing), ["I", "v"])

    def test_bad_input_is_reported_per_field(self):
        f = seed("arc_energy_heat_input")
        for values, field in (({"V": "abc", "I": 1, "v": 1}, "V"), ({"V": -5, "I": 1, "v": 1}, "V"), ({"V": 1, "I": 1, "v": 1, "eta": 1.5}, "eta"),
                              ({"V": float("inf"), "I": 1, "v": 1}, "V"), ({"V": True, "I": 1, "v": 1}, "V")):
            with self.assertRaises(CalcError, msg=str(values)) as cm:
                calculate(f, values)
            self.assertIn(field, cm.exception.fields)
        with self.assertRaises(CalcError):
            calculate(f, {"V": 1, "I": 1, "v": 1, "nonsense": 3})
        with self.assertRaises(CalcError):
            calculate(f, {"V": 1, "I": 1, "v": 0})                                 # division by zero is an error, not a crash

    def test_comma_decimals_are_accepted(self):
        r = calculate(seed("electrode_face_diameter"), {"t": "1,4"})
        self.assertEqual(r["outputs"][0]["text"], "5.9")

    def test_formula_warnings_come_from_the_documents(self):
        self.assertTrue(calculate(seed("ce_iiw"), {"C": 0.1, "Mn": 1.0})["warnings"])                    # below 0.18 % C
        self.assertFalse(calculate(seed("ce_iiw"), {"C": 0.2, "Mn": 1.0})["warnings"])
        self.assertTrue(calculate(seed("first_trial_estimate"), {"sumT": 0.09})["warnings"])
        self.assertTrue(calculate(seed("dilution"), {"A_parent": 70, "A_total": 60})["warnings"])


class ValidateTests(unittest.TestCase):
    def problems(self, mutate):
        body = minimal()
        mutate(body)
        return validate.problems(validate.clean(body))

    def test_a_sound_formula_passes(self):
        self.assertEqual(self.problems(lambda b: None), [])

    def has(self, mutate, text):
        found = self.problems(mutate)
        self.assertTrue(any(text in p for p in found), f"expected a problem mentioning {text!r}, got {found}")

    def test_every_variable_must_be_explained(self):
        self.has(lambda b: b["variables"][0].update(meaning="width"), "explain what it means")
        self.has(lambda b: b["variables"][0].update(unit=""), "unit")
        self.has(lambda b: b["variables"][0].update(name=""), "give it a name")

    def test_variables_must_be_used_and_defined(self):
        self.has(lambda b: b["variables"].append({"symbol": "z", "name": "Extra", "meaning": "An input nothing uses at all.", "unit": "mm"}), "never used")
        self.has(lambda b: b["outputs"][0].update(expr="w * q"), "'q'")
        self.has(lambda b: b["variables"][1].update(symbol="w"), "used twice")
        self.has(lambda b: b["variables"][1].update(symbol="sqrt"), "function")
        self.has(lambda b: b["variables"][1].update(symbol="2h"), "must start with a letter")

    def test_purpose_source_and_limits_are_required(self):
        self.has(lambda b: b.update(purpose="short"), "Why it is needed")
        self.has(lambda b: b.update(source={}), "Source")
        self.has(lambda b: b.update(assumptions=[]), "Limits")
        self.has(lambda b: b.update(name=""), "Name")

    def test_unsafe_or_broken_expressions_are_refused(self):
        self.has(lambda b: b["outputs"][0].update(expr="__import__('os')"), "Only numbers")
        self.has(lambda b: b["outputs"][0].update(expr="w *"), "Cannot read")
        self.has(lambda b: b["outputs"][0].update(expr="w * h", symbol="w"), "already used")

    def test_the_formula_must_reproduce_its_example(self):
        self.has(lambda b: b["examples"][0].update(expected={"A": 7}), "gives A = 6")
        self.has(lambda b: b["examples"].clear(), "Worked example")
        self.has(lambda b: b["examples"][0].update(expected={"nope": 6}), "named after outputs")
        self.assertEqual(self.problems(lambda b: b["examples"][0].update(expected={"A": 6.05})), [])    # within the 2% the documents' rounding needs

    def test_warning_rules_are_checked(self):
        self.has(lambda b: b.update(checks=[{"when": "w >", "message": "x"}]), "Cannot read")
        self.has(lambda b: b.update(checks=[{"when": "q > 1", "message": "x"}]), "'q'")
        self.has(lambda b: b.update(checks=[{"when": "w > 1", "message": ""}]), "message")

    def test_clean_ignores_junk(self):
        self.assertEqual(validate.clean("nonsense")["name"], "")
        self.assertIn("A = w * h", validate.clean(minimal())["display"])


class ShippedFormulaTests(unittest.TestCase):
    def test_there_are_formulas_and_each_one_is_valid(self):
        self.assertGreaterEqual(len(SEED), 11)
        for e in SEED:
            found = validate.problems(validate.clean(e))
            self.assertEqual(found, [], e["id"])

    def test_ids_are_unique_and_related_ones_exist(self):
        ids = [e["id"] for e in SEED]
        self.assertEqual(len(ids), len(set(ids)))
        for e in SEED:
            for r in e["related"]:
                self.assertIn(r, ids, f"{e['id']} points at {r}")

    def test_every_formula_names_its_document_and_quotes_it(self):
        for e in SEED:
            s = e["source"]
            self.assertTrue(s["document"] and s["section"] and s["anchor"], e["id"])
            self.assertTrue((paths.DOCS_DIR / s["document"]).is_file() or not paths.DOCS_DIR.exists(), f"{e['id']}: {s['document']} not found")

    @unittest.skipUnless((paths.INDEX_DIR / "chunks.json").is_file(), "needs the built index")
    def test_each_quoted_anchor_is_really_in_its_document(self):
        squash = lambda t: re.sub(r"\s+", " ", t)
        chunks = json.loads((paths.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
        for e in SEED:
            s = e["source"]
            hit = any(c["source"] == s["document"] and squash(s["anchor"]) in squash(c["text"]) for c in chunks)
            self.assertTrue(hit, f"{e['id']}: the passage {s['anchor']!r} is not in {s['document']}")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = FormulaStore(":memory:")
        self.report = self.store.sync_documents(SEED)

    def code(self, fn, *a):
        with self.assertRaises(FormulaError) as cm:
            fn(*a)
        return cm.exception.code

    def test_sync_adds_every_document_formula_once(self):
        self.assertEqual(len(self.report["added"]), len(SEED))
        self.assertEqual(self.report["invalid"], {})
        again = self.store.sync_documents(SEED)
        self.assertEqual((again["added"], again["updated"], again["retired"]), ([], [], []))
        self.assertEqual(len(self.store.usable()), len(SEED))

    def test_sync_follows_the_file(self):
        changed = copy.deepcopy(SEED)
        changed[0]["purpose"] += " Extra sentence added in the document."
        dropped = changed.pop()
        report = self.store.sync_documents(changed)
        self.assertEqual(report["updated"], [changed[0]["id"]])
        self.assertEqual(report["retired"], [dropped["id"]])
        self.assertNotIn(dropped["id"], [f["id"] for f in self.store.usable()])
        self.assertEqual(self.store.get(changed[0]["id"])["version"], 2)

    def test_sync_reports_a_broken_entry_and_keeps_the_rest(self):
        broken = copy.deepcopy(SEED)
        broken[0]["outputs"][0]["expr"] = "I ** "
        report = self.store.sync_documents(broken)
        self.assertIn(broken[0]["id"], report["invalid"])
        self.assertEqual(report["retired"], [])                                   # a typo must not take a formula away

    def test_document_formulas_cannot_be_edited_but_can_be_taken_off_the_list(self):
        for fn in (lambda: self.store.update("dilution", minimal(), "admin1"), lambda: self.store.approve("dilution", "admin1")):
            with self.assertRaises(FormulaError):
                fn()
        self.assertFalse(self.store.delete("dilution", "admin1")["active"])                  # removing is allowed: the documents stay as they are
        self.assertTrue(self.store.restore("dilution", "admin1")["active"])

    def test_admin_adds_edits_removes_and_restores_with_a_history(self):
        f = self.store.create("plate_area", minimal(), "admin1")
        self.assertEqual((f["status"], f["version"]), ("admin", 1))
        edited = minimal()
        edited["name"] = "Plate area"
        self.assertEqual(self.store.update("plate_area", edited, "admin1")["version"], 2)
        self.assertFalse(self.store.delete("plate_area", "admin1")["active"])
        self.assertNotIn("plate_area", [x["id"] for x in self.store.usable()])
        self.assertTrue(self.store.restore("plate_area", "admin2")["active"])
        history = self.store.history("plate_area")
        self.assertEqual([h["action"] for h in history], ["restored", "removed", "edited", "created"])
        self.assertEqual(history[0]["changed_by"], "admin2")

    def test_suggestions_stay_hidden_until_approved(self):
        self.store.suggest("plate_area", minimal(), "eng1")
        self.assertNotIn("plate_area", [x["id"] for x in self.store.usable()])
        self.assertIn("plate_area", [x["id"] for x in self.store.everything()])
        self.store.approve("plate_area", "admin1")
        self.assertIn("plate_area", [x["id"] for x in self.store.usable()])
        self.assertEqual(self.code(self.store.approve, "plate_area", "admin1"), "not_suggested")

    def test_nothing_invalid_is_saved(self):
        bad = minimal()
        bad["purpose"] = ""
        with self.assertRaises(FormulaError) as cm:
            self.store.create("plate_area", bad, "admin1")
        self.assertEqual(cm.exception.code, "invalid")
        self.assertTrue(cm.exception.problems)
        self.assertEqual(self.code(self.store.get, "plate_area"), "not_found")

    def test_ids_are_checked(self):
        for bad in ("", "Plate", "1plate", "ab", "ask", "suggest", "has space", "x" * 50):
            self.assertEqual(self.code(self.store.create, bad, minimal(), "admin1"), "bad_id", bad)
        self.assertEqual(self.code(self.store.create, "dilution", minimal(), "admin1"), "exists")

    def test_everything_wrong_is_reported_in_one_go(self):
        with self.assertRaises(FormulaError) as cm:
            self.store.create("", {}, "admin1")                                    # an empty form: no id, no name, nothing
        self.assertEqual(cm.exception.code, "invalid")
        said = " ".join(cm.exception.problems)
        for part in ("id must be", "Name", "Why it is needed", "Source", "Variables", "Outputs"):
            self.assertIn(part, said)

    def test_an_admin_formula_cannot_be_overwritten_by_the_documents(self):
        self.store.create("plate_area", minimal(), "admin1")
        clash = copy.deepcopy(SEED) + [dict(validate.clean(minimal()), id="plate_area")]
        self.assertIn("plate_area", self.store.sync_documents(clash)["invalid"])
        self.assertEqual(self.store.get("plate_area")["status"], "admin")


class AskTests(unittest.TestCase):
    def setUp(self):
        store = FormulaStore(":memory:")
        store.sync_documents(SEED)
        self.formulas = store.usable()

    def test_heat_input_question_with_numbers(self):
        r = ask.ask(self.formulas, "What is the heat input at 28 V, 260 A and 350 mm/min?")
        self.assertEqual(r["state"], "answered")
        self.assertEqual(r["formula_id"], "arc_energy_heat_input")
        self.assertEqual(r["result"]["outputs"][0]["text"], "1.25")
        self.assertEqual(r["result"]["skipped"][0]["symbol"], "heat_input")        # no efficiency was given: only arc energy, and it says so

    def test_efficiency_in_words_gives_heat_input_too(self):
        r = ask.ask(self.formulas, "heat input with V=29, I=290, v=400 and efficiency 0.8")
        self.assertEqual(r["state"], "answered")
        self.assertEqual({o["symbol"]: o["text"] for o in r["result"]["outputs"]}, {"arc_energy": "1.26", "heat_input": "1.01"})

    def test_symbols_are_case_sensitive(self):
        r = ask.ask(self.formulas, "arc energy V=28 I=260 v=350")
        self.assertEqual({m["symbol"]: m["value"] for m in r["extracted"]}, {"V": 28, "I": 260, "v": 350})

    def test_the_same_numbers_written_three_ways_all_read_correctly(self):
        want = {"V": 28, "I": 260, "v": 350}
        for q in ("arc energy V=28 I=260 v=350", "arc energy 28 V 260 A 350 mm/min", "arc energy voltage 28, current 260, travel speed 350",
                  "arc energy V: 28, I: 260, v: 350"):
            self.assertEqual(ask.extract(next(f for f in self.formulas if f["id"] == "arc_energy_heat_input"), q)["values"], want, q)

    def test_a_list_of_elements_is_read(self):
        f = next(f for f in self.formulas if f["id"] == "ce_iiw")
        self.assertEqual(ask.extract(f, "C 0.2, Mn 1.2 Cr 0.1")["values"], {"C": 0.2, "Mn": 1.2, "Cr": 0.1})
        self.assertEqual(ask.extract(f, "carbon 0.2% manganese 1.2%")["values"], {"C": 0.2, "Mn": 1.2})

    def test_ambiguous_writing_is_asked_about_never_guessed(self):
        f = next(f for f in self.formulas if f["id"] == "arc_energy_heat_input")
        got = ask.extract(f, "V 28 I 260 v 350")                        # "260 v" could be 260 volts
        self.assertIn("V", got["conflicts"])
        self.assertNotIn("V", got["values"])

    def test_missing_numbers_are_asked_for(self):
        r = ask.ask(self.formulas, "heat input at 28 V and 260 A")
        self.assertEqual((r["state"], r["missing"]), ("need_values", ["v"]))
        self.assertIsNone(r["result"])

    def test_two_carbon_equivalents_means_asking_which(self):
        r = ask.ask(self.formulas, "calculate the carbon equivalent")
        self.assertEqual(r["state"], "choose")
        self.assertEqual({c["id"] for c in r["candidates"]} & {"ce_iiw", "ce_aws_d1_1"}, {"ce_iiw", "ce_aws_d1_1"})

    def test_naming_the_variant_picks_it(self):
        r = ask.ask(self.formulas, "CE with silicon for C 0.2, Mn 1.2, Si 0.3")
        self.assertEqual((r["state"], r["formula_id"]), ("answered", "ce_aws_d1_1"))
        self.assertAlmostEqual(r["result"]["outputs"][0]["value"], 0.2 + 1.5 / 6)

    def test_choosing_after_the_question_works(self):
        r = ask.ask(self.formulas, "carbon equivalent C 0.2 Mn 1.2", formula_id="ce_iiw")
        self.assertEqual(r["state"], "answered")
        self.assertEqual(len(r["result"]["assumed"]), 5)

    def test_values_typed_into_the_form_override_and_complete(self):
        r = ask.ask(self.formulas, "heat input", formula_id="arc_energy_heat_input", values={"V": 28, "I": 260, "v": 350, "eta": 0.8})
        self.assertEqual(r["state"], "answered")
        self.assertEqual(r["result"]["outputs"][1]["text"], "1.00")

    def test_spot_weld_geometry(self):
        r = ask.ask(self.formulas, "what electrode face diameter for 1.4 mm sheet")
        self.assertEqual((r["state"], r["formula_id"], r["result"]["outputs"][0]["text"]), ("answered", "electrode_face_diameter", "5.9"))
        r = ask.ask(self.formulas, "minimum nugget diameter for thickness 1.0 mm")
        self.assertEqual([o["text"] for o in r["result"]["outputs"]], ["4.0", "5.0"])

    def test_what_the_documents_have_no_formula_for_is_not_covered(self):
        for q in ("calculate t8/5 cooling time", "what preheat temperature do I need", "what is the capital of France", "hello"):
            r = ask.ask(self.formulas, q)
            self.assertEqual(r["state"], "not_covered", q)
            self.assertIsNone(r["result"])

    def test_extra_words_do_not_make_a_formula_up(self):
        # found by the live test: "plate", "mm" and "cooling" used to pull in the heat-input formula, whose description mentions cooling
        for q in ("calculate t8/5 cooling time for 20 mm plate", "what preheat temperature for 20 mm plate", "calculate cooling time for 20 mm plate",
                  "how thick should my plate be", "mm", "time"):
            r = ask.ask(self.formulas, q)
            self.assertEqual(r["state"], "not_covered", q)
            self.assertEqual(r["candidates"], [], q)

    def test_real_questions_still_match_after_that(self):
        for q, want in (("heat input for 20 mm plate at 28 V 260 A 350 mm/min", "arc_energy_heat_input"), ("what is dilution", "dilution"),
                        ("minimum edge distance for a 4 mm nugget", "min_edge_distance"), ("Pcm for C 0.1", "pcm"),
                        ("how far apart can spot welds be, weld spacing for 1 mm sheet", "min_weld_spacing"), ("electrode tip diameter for 1.2 mm", "electrode_face_diameter")):
            self.assertEqual(ask.ask(self.formulas, q)["formula_id"], want, q)

    def test_two_different_numbers_for_one_variable_are_not_guessed(self):
        r = ask.ask(self.formulas, "arc energy V=28 and V=30, I=260, v=350")
        self.assertIn("V", r["conflicts"])
        self.assertEqual(r["state"], "need_values")
        self.assertIn("V", r["missing"])

    def test_out_of_range_values_are_invalid_not_calculated(self):
        r = ask.ask(self.formulas, "heat input", formula_id="arc_energy_heat_input", values={"V": 28, "I": 260, "v": 350, "eta": 2})
        self.assertEqual(r["state"], "invalid")


if __name__ == "__main__":
    unittest.main()


class SimpleFormTests(unittest.TestCase):
    def setUp(self):
        self.store = FormulaStore(":memory:")

    def form(self, **kw):
        base = {"name": "Resistive heat", "expression": "I**2 * R * t", "symbol": "Q", "unit": "J",
                "variables": [{"symbol": "I", "unit": "A"}, {"symbol": "R", "unit": "ohm"}, {"symbol": "t", "unit": "s"}]}
        return {**base, **kw}

    def test_a_simple_form_is_saved_and_calculates(self):
        f = self.store.create("", self.form(), "admin")
        self.assertEqual(f["id"], "resistive_heat")
        self.assertEqual(f["display"], "Q = I**2 * R * t")
        out = calculate(f, {"I": 2, "R": 3, "t": 5})
        self.assertEqual(out["outputs"][0]["value"], 60)

    def test_every_symbol_must_be_used_and_defined(self):
        with self.assertRaises(FormulaError) as unused:
            self.store.create("", self.form(variables=self.form()["variables"] + [{"symbol": "z", "unit": "m"}]), "admin")
        self.assertTrue(any("'z'" in p and "never used" in p for p in unused.exception.problems))
        with self.assertRaises(FormulaError) as missing:
            self.store.create("", self.form(expression="I**2 * R * t * k"), "admin")
        self.assertTrue(any("'k'" in p for p in missing.exception.problems))

    def test_the_id_is_made_unique(self):
        self.store.create("", self.form(), "admin")
        self.assertEqual(self.store.create("", self.form(), "admin")["id"], "resistive_heat_2")

    def test_simple_form_round_trip_for_editing(self):
        f = self.store.create("", self.form(), "admin")
        self.assertEqual(simple.to_simple(f)["expression"], "I**2 * R * t")
        changed = self.store.update(f["id"], self.form(expression="I**2 * R * t / 2"), "admin")
        self.assertEqual(changed["version"], 2)
