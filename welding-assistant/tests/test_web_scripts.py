"""The pages' scripts: every one parses, and the Agents page's question builder gives the right sentence for what is chosen. Skipped when Node.js is not installed."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web" / "js"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is not installed")
class Scripts(unittest.TestCase):
    def test_every_script_parses(self):
        bad = []
        with tempfile.TemporaryDirectory() as folder:                    # checked as modules: copied to .mjs files, which Node reads as modules
            for number, path in enumerate(sorted(WEB.rglob("*.js"))):
                copy = Path(folder) / f"{number}.mjs"
                copy.write_bytes(path.read_bytes())
                done = subprocess.run([NODE, "--check", str(copy)], capture_output=True, text=True)
                if done.returncode:
                    bad.append(f"{path.relative_to(WEB)}: {done.stderr.strip().splitlines()[-1] if done.stderr.strip() else 'failed'}")
        self.assertEqual(bad, [])

    def build(self, agent, fields):
        script = (f'import {{ buildQuestion }} from "{(WEB / "pages" / "agent-question.js").as_uri()}";'
                  f"console.log(JSON.stringify(buildQuestion({json.dumps(agent)}, {json.dumps(fields)})));")
        done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    def test_nothing_chosen_gives_no_question(self):
        self.assertEqual(self.build("fault-finder", {"process": "", "material": "", "problem": "", "details": ""}), "")
        self.assertEqual(self.build("procedure-advisor", {"task": "", "material": "", "thickness": "", "industry": "", "details": "  "}), "")

    def test_fault_finder_uses_only_what_is_chosen(self):
        self.assertEqual(self.build("fault-finder", {"problem": "cracking"}), "What are the causes and corrective actions of cracking?")
        self.assertEqual(self.build("fault-finder", {"problem": "porosity", "process": "MIG", "material": "steel", "details": "Gas is 100% CO2."}),
                         "What are the causes and corrective actions of porosity in MIG welding of steel? Additional details: Gas is 100% CO2.")
        self.assertEqual(self.build("fault-finder", {"process": "TIG"}), "What are the common weld defects in TIG welding, with their causes and corrective actions?")
        self.assertEqual(self.build("fault-finder", {"details": "Weld keeps splashing"}), "I have a weld problem. What could be causing it? Additional details: Weld keeps splashing.")

    def test_procedure_advisor_follows_the_task(self):
        self.assertEqual(self.build("procedure-advisor", {"task": "wps", "material": "aluminium", "thickness": "3", "industry": "rail"}),
                         "What should a welding procedure specification for aluminium 3 mm thick in the rail industry contain?")
        self.assertEqual(self.build("procedure-advisor", {"task": "qualify", "details": "Position 3G."}), "How do I qualify a welder or a procedure? Additional details: Position 3G.")
        self.assertTrue(self.build("procedure-advisor", {"material": "steel"}).startswith("What should I know about welding"))


if __name__ == "__main__":
    unittest.main()
