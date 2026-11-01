"""The agents: slot reading, and the parameter lookup checked against the numbers printed in document 20."""
import json
import unittest

from welding_ai import paths
from welding_ai.agents import parameters, slots
from welding_ai.agents.knowledge import Knowledge
from welding_ai.formulas.store import FormulaStore
from welding_ai.retrieval import retrieval


def formulas():
    store = FormulaStore(":memory:")
    store.sync_documents(json.loads(paths.FORMULAS_SEED.read_text(encoding="utf-8")))
    return {f["id"]: f for f in store.usable()}


class SlotReading(unittest.TestCase):
    def test_a_full_situation(self):
        s = slots.read("I am spot welding 1.2 mm DP600 galvannealed to 1.5 mm HSLA and I get expulsion. What should I do?")
        self.assertEqual(s.process, "rsw")
        self.assertEqual([(round(x.thickness_mm, 2), x.material) for x in s.sheets], [(1.2, "ahss"), (1.5, "hsla")])
        self.assertTrue(s.coating)
        self.assertEqual(s.symptom, "expulsion")
        self.assertTrue(s.first_person)
        self.assertIn("fix", s.asks)
        self.assertEqual(s.parts(), 5)

    def test_a_grade_read_as_a_family_is_flagged_as_an_assumption(self):
        s = slots.read("spot welding DP600")
        reading = next(r for r in s.readings if r[0] == "DP600")
        self.assertTrue(reading[2])
        self.assertIn("dual-phase", reading[1])

    def test_everyday_words_are_not_read_as_acronyms(self):
        s = slots.read("I saw cracks in the weld after the circuit trip, with a 6000 N load")
        self.assertEqual((s.process, s.materials), ("", []))
        self.assertEqual(s.symptom, "cracking")

    def test_a_number_is_not_an_aluminium_alloy_unless_it_says_so(self):
        self.assertEqual(slots.read("a load of 6000 N").materials, [])
        self.assertEqual(slots.read("5083 alloy plate").materials, ["aluminium"])

    def test_thickness_in_inches_and_stacks(self):
        s = slots.read("two sheets of 0.04 in, and 2 x 1.5 mm")
        self.assertEqual([round(x.thickness_mm, 2) for x in s.sheets], [1.5, 1.5, 1.02])

    def test_one_material_for_a_stack_is_assumed_and_said(self):
        s = slots.read("2 x 1.2 mm stainless steel")
        self.assertEqual([x.material for x in s.sheets], ["stainless", "stainless"])
        self.assertTrue(any(r[2] for r in s.readings))

    def test_unpaired_sheets_are_not_guessed(self):
        s = slots.read("1.0 mm and 1.5 mm and 2.0 mm, mild steel and HSLA")
        self.assertEqual([x.material for x in s.sheets], ["", "", ""])
        self.assertTrue(any("not paired" in r[1] for r in s.readings))

    def test_high_strength_steel_alone_is_unclear(self):
        s = slots.read("cracks appear hours after welding a high strength steel")
        self.assertEqual(s.symptom, "hydrogen cracking")
        self.assertTrue(s.unclear)

    def test_weak_weld_is_read_with_a_flagged_note(self):
        s = slots.read("my spot welds are weak")
        self.assertEqual(s.symptom, "undersize nugget")
        self.assertTrue(next(r for r in s.readings if r[1].startswith("undersize"))[2])


class Parameters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        index = retrieval.load()
        with open(paths.INDEX_DIR / "tables.json", encoding="utf-8") as fh:
            tables = json.load(fh)
        cls.kn = Knowledge(tables, index)
        cls.f = formulas()

    def run_for(self, text, wanted=None):
        s = slots.read(text)
        return parameters.compute(s, wanted or list(parameters.ALL), self.f, self.kn), s

    def quantity(self, r, name):
        return next(q for q in r.quantities if q["name"].startswith(name))

    def test_asked_quantities(self):
        self.assertEqual(parameters.asked("what is the electrode force for 1.0 mm mild steel"), ["force"])
        self.assertEqual(set(parameters.asked("weld time and current for 2 mm")), {"time", "current"})
        self.assertEqual(parameters.asked("starting parameters for 1.4 mm steel"), list(parameters.ALL))
        self.assertEqual(parameters.asked("what is porosity"), [])

    def test_arc_welding_current_is_not_taken_for_spot_welding(self):
        self.assertFalse(parameters.in_context("what current for 3 mm mild steel with MIG", "gmaw"))
        self.assertTrue(parameters.in_context("what current for 1 mm sheet, spot weld", "rsw"))
        self.assertTrue(parameters.in_context("electrode force for 1.0 mm mild steel", ""))

    def test_reproduces_the_documents_table_for_each_band(self):
        # two equal sheets of the representative thickness; document 20, section 4 (steel) and section 5 (aluminium)
        rows = {0.65: (307, 5.1, 5120), 1.0: (472, 7.9, 7870), 1.4: (661, 11.0, 8820), 1.8: (850, 14.2, 11340), 2.5: (1181, 19.7, 15750)}
        for t, (force, cycles, current) in rows.items():
            r, _ = self.run_for(f"spot welding 2 x {t} mm mild steel")
            self.assertEqual(r.refused, "", t)
            self.assertAlmostEqual(self.quantity(r, "Electrode force")["value"], force, delta=force * 0.002, msg=t)
            self.assertAlmostEqual(self.quantity(r, "Weld time")["value"], cycles, delta=0.06, msg=t)
            self.assertAlmostEqual(self.quantity(r, "Weld current")["value"], current, delta=current * 0.002, msg=t)

    def test_the_geometry_rows(self):
        r, _ = self.run_for("spot welding 2 x 1.0 mm mild steel")
        self.assertEqual(self.quantity(r, "Electrode face")["text"], "5.0")
        self.assertEqual(self.quantity(r, "Minimum nugget")["text"], "4.0 to 5.0")
        self.assertEqual(self.quantity(r, "Minimum spacing")["text"], "16.0")
        edges = [q["text"] for q in r.quantities if q["name"].startswith("Minimum edge")]
        self.assertEqual(edges, ["5.0", "6.3"])                               # the document: 5.0 to 6.3 mm

    def test_the_geometry_uses_the_thinnest_sheet_and_the_force_the_whole_stack(self):
        r, _ = self.run_for("spot welding 1.0 mm to 2.0 mm mild steel")
        self.assertEqual(self.quantity(r, "Electrode face")["text"], "5.0")           # 5 x sqrt(1.0)
        self.assertAlmostEqual(r.stack_in, 3.0 / 25.4, places=3)
        self.assertAlmostEqual(self.quantity(r, "Electrode force")["value"], 6000 * round(3.0 / 25.4, 4), delta=1)
        self.assertTrue(any("thinnest" in n for n in r.notes))

    def test_the_twenty_percent_reduction_applies_only_above_the_threshold(self):
        below, _ = self.run_for("spot welding 2 x 1.0 mm mild steel", ["current"])
        above, _ = self.run_for("spot welding 2 x 1.4 mm mild steel", ["current"])
        self.assertNotIn("x 0.8", below.quantities[0]["steps"])
        self.assertIn("x 0.8", above.quantities[0]["steps"])

    def test_force_is_converted_to_newtons_and_says_so(self):
        r, _ = self.run_for("spot welding 2 x 1.0 mm mild steel", ["force"])
        q = r.quantities[0]
        self.assertEqual(q["text"], "472 lbf")
        self.assertIn("2.10 kN", q["unit"])

    def test_aluminium_multiplies_current_and_time_and_has_no_force_or_geometry(self):
        r, _ = self.run_for("spot welding 2 x 1.0 mm aluminium")
        self.assertAlmostEqual(self.quantity(r, "Weld current")["value"], 23600, delta=60)
        self.assertAlmostEqual(self.quantity(r, "Weld time")["value"], 2.6, delta=0.05)
        names = [q["name"] for q in r.quantities]
        self.assertFalse(any(n.startswith(("Electrode", "Minimum")) for n in names))     # neither force nor geometry is given for aluminium
        self.assertEqual(len(r.gaps), 2)

    def test_hsla_ahss_and_zinc_carry_the_documents_own_caveat_and_no_invented_multiplier(self):
        r, _ = self.run_for("spot welding 2 x 1.0 mm DP600", ["current"])
        self.assertEqual(r.family, "ahss")
        text = " ".join(c[1] for c in r.caveats)
        self.assertIn("independent procedure qualification", text)
        self.assertIn("must never be transferred without requalification", text)
        self.assertAlmostEqual(r.quantities[0]["value"], 7870, delta=20)            # the steel baseline, not a made-up AHSS number
        z, _ = self.run_for("spot welding 2 x 1.0 mm galvannealed steel", ["current"])
        self.assertEqual(z.family, "zinc")
        self.assertIn("coating", " ".join(c[1] for c in z.caveats).lower())

    def test_a_mixed_stack_is_flagged_as_not_covered_by_a_schedule(self):
        r, _ = self.run_for("spot welding 1.2 mm DP600 to 1.5 mm HSLA", ["current"])
        self.assertEqual(r.family, "mixed")
        self.assertTrue(any("mixed stack" in n for n in r.notes))
        self.assertTrue(r.caveats)

    def test_no_material_means_the_baseline_and_says_so(self):
        r, _ = self.run_for("electrode force for 1.0 mm sheet", ["force"])
        self.assertEqual(r.family, "mild")
        self.assertTrue(any("No material was named" in n for n in r.notes))

    def test_sheets_outside_the_documents_range_are_refused(self):
        for text in ("spot welding 0.2 mm mild steel", "spot welding 5 mm mild steel", "spot welding 1.0 mm to 4 mm mild steel"):
            r, _ = self.run_for(text)
            self.assertIn("covers sheets from", r.refused, text)
            self.assertEqual(r.quantities, [])

    def test_one_thickness_is_read_as_two_equal_sheets_and_says_so(self):
        one, _ = self.run_for("spot welding 1.0 mm mild steel", ["force", "current"])
        two, _ = self.run_for("spot welding 2 x 1.0 mm mild steel", ["force", "current"])
        self.assertEqual([q["text"] for q in one.quantities], [q["text"] for q in two.quantities])
        self.assertTrue(any("two equal sheets" in n for n in one.notes))
        self.assertFalse(any("two equal sheets" in n for n in two.notes))

    def test_a_missing_thickness_is_asked_for(self):
        r, _ = self.run_for("what is the electrode force for mild steel")
        self.assertIn("thickness", r.refused)

    def test_materials_the_guide_does_not_cover_are_refused(self):
        r, _ = self.run_for("spot welding 1.0 mm stainless steel")
        self.assertIn("none for stainless", r.refused.replace("It gives none for ", "none for "))
        self.assertEqual(r.quantities, [])



class Troubleshooting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        index = retrieval.load()
        with open(paths.INDEX_DIR / "tables.json", encoding="utf-8") as fh:
            cls.kn = Knowledge(json.load(fh), index)

    def find(self, symptom, process="", materials=()):
        from welding_ai.agents import troubleshooting
        return troubleshooting.lookup(symptom, process, self.kn, materials)

    def test_expulsion_quotes_the_resistance_row_and_its_discriminator(self):
        f = self.find("expulsion", "rsw")
        self.assertEqual([r["imperfection"] for r in f.rows], ["Expulsion"])
        self.assertIn("Reduce current or time; increase force", f.rows[0]["action"])
        self.assertIn("Visible expelled metal", f.rows[0]["discriminators"])
        self.assertTrue(any("Expulsion vs surface pit" in p["pair"] for p in f.pairs))
        self.assertEqual(f.ask, "")

    def test_a_resistance_symptom_is_read_from_the_resistance_table_even_with_no_process_named(self):
        self.assertEqual([r["imperfection"] for r in self.find("undersize nugget").rows], ["Undersize nugget"])

    def test_porosity_in_arc_welding_comes_from_the_group_tables_not_the_beam_ones(self):
        f = self.find("porosity", "gmaw")
        self.assertTrue(f)
        self.assertTrue(all("poros" in r["imperfection"].lower() or "blowhole" in r["imperfection"].lower() for r in f.rows))
        self.assertFalse(any("aluminium" in r["imperfection"].lower() for r in f.rows))             # mild steel was not named, aluminium was not
        self.assertTrue(any("aluminium" in r["imperfection"].lower() for r in self.find("porosity", "gmaw", ["aluminium"]).rows))
        self.assertFalse(any("FSW" in p["pair"] or "keyhole" in p["pair"].lower() for p in f.pairs))
        self.assertTrue(any("Porosity vs slag" in p["pair"] for p in f.pairs))

    def test_a_generic_crack_names_the_kinds_and_asks_which(self):
        f = self.find("cracking", "")
        self.assertGreater(len(f.rows), 4)
        self.assertIn("Which of these matches what you see", f.ask)
        self.assertIn("timing", f.ask)
        self.assertTrue(any("Solidification crack vs hydrogen crack" in p["pair"] for p in f.pairs))

    def test_hydrogen_cracking_is_found_through_the_mechanism_column(self):
        f = self.find("hydrogen cracking", "")
        self.assertTrue(f)
        self.assertTrue(all("hydrogen" in r["mechanism"].lower() for r in f.rows))

    def test_arc_spatter_adds_the_documents_order_of_variables(self):
        f = self.find("spatter", "gmaw")
        self.assertTrue(f.spatter_order)
        self.assertEqual(f.spatter_order[0][0], "Transfer mode")
        self.assertEqual(self.find("spatter", "rsw").spatter_order, [])

    def test_a_symptom_the_tables_do_not_name_gives_nothing_rather_than_a_guess(self):
        self.assertFalse(self.find("distortion", "gmaw"))
        self.assertFalse(self.find("not a symptom", ""))

    def test_every_row_has_a_corrective_action_and_a_chunk_to_open(self):
        for symptom in ("expulsion", "porosity", "undercut", "lack of fusion", "slag inclusion", "burn-through"):
            f = self.find(symptom, "rsw" if symptom == "expulsion" else "gmaw")
            self.assertTrue(f, symptom)
            for r in f.rows:
                self.assertTrue(r["action"], (symptom, r["imperfection"]))
                self.assertIsInstance(r["chunk"], int)


if __name__ == "__main__":
    unittest.main()
