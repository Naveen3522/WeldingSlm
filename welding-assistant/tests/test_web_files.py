"""The web app is many small files that point at each other: every page, style, script and import has to resolve, and only the app's own files are served."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
sys.path.insert(0, str(ROOT / "apps" / "api"))

import static_files  # noqa: E402

IMPORT = re.compile(r'(?:from|import)\s*\(?\s*"(\.{1,2}/[^"]+)"')
ICON_NAME = re.compile(r'(?:icon|iconButton)\("([a-z-]+)"')


class Pages(unittest.TestCase):
    def test_every_page_loads_files_the_server_will_serve(self):
        pages = sorted((WEB / "pages").rglob("*.html"))
        self.assertGreaterEqual(len(pages), 9)
        for page in pages:
            text = page.read_text(encoding="utf-8")
            for ref in re.findall(r'(?:href|src)="(/[^"]+)"', text):
                if ref.startswith("/api/") or ref in ("/", "/login.html") or ref.endswith(".html"):
                    continue
                found = static_files.static_file(WEB, ref.split("?")[0])
                self.assertTrue(found is not None and found.is_file(), f"{page.name} points at {ref}, which is not served")

    def test_every_page_url_maps_to_its_file(self):
        for url, name in (("/", "index.html"), ("/login.html", "login.html"), ("/agents.html", "agents.html"), ("/admin/users.html", "admin/users.html"),
                          ("/admin/model.html", "admin/model.html"), ("/forgot.html", "forgot.html"), ("/password.html", "password.html"), ("/formulas.html", "formulas.html")):
            self.assertEqual(static_files.static_file(WEB, url), WEB / "pages" / name)
            self.assertTrue((WEB / "pages" / name).is_file(), name)

    def test_nothing_else_on_disk_is_reachable(self):
        for bad in ("/../server.py", "/js/../../api/server.py", "/css/../../../data/app.db", "/pages/index.html", "/data/app.db", "/vendor/../x.js", "/js/core/%2e%2e/x.js",
                    "/js/a.b.js", "/admin/../index.html", "/vendor/fontawesome/css/../../../x.css", "/js/x.py", "//etc/passwd"):
            self.assertIsNone(static_files.static_file(WEB, bad), bad)


class Scripts(unittest.TestCase):
    def test_every_import_points_at_a_file(self):
        scripts = sorted((WEB / "js").rglob("*.js"))
        self.assertGreater(len(scripts), 30)
        for script in scripts:
            for ref in IMPORT.findall(script.read_text(encoding="utf-8")):
                self.assertTrue((script.parent / ref).resolve().is_file(), f"{script.relative_to(WEB)} imports {ref}")

    def test_every_page_script_exists(self):
        for page in (WEB / "pages").rglob("*.html"):
            for ref in re.findall(r'<script type="module" src="(/js/[^"]+)"', page.read_text(encoding="utf-8")):
                self.assertTrue((WEB / ref[1:]).is_file(), f"{page.name}: {ref}")

    def test_every_icon_name_used_is_defined(self):
        known = set(re.findall(r'"?([a-z-]+)"?: "[a-z_0-9]+"', (WEB / "js" / "core" / "icons.js").read_text(encoding="utf-8")))
        for script in (WEB / "js").rglob("*.js"):
            for name in ICON_NAME.findall(script.read_text(encoding="utf-8")):
                self.assertIn(name, known, f"{script.name} uses the icon '{name}'")

    def test_the_icon_font_is_local(self):
        for css in (WEB / "css").rglob("*.css"):
            self.assertNotIn("cdn", css.read_text(encoding="utf-8").lower(), css.name)
        for page in (WEB / "pages").rglob("*.html"):
            self.assertNotRegex(page.read_text(encoding="utf-8"), r'(?:href|src)="https?://', page.name)
        self.assertTrue((WEB / "vendor" / "material-symbols" / "MaterialSymbols.woff2").is_file())

    def test_every_icon_has_a_character_in_the_trimmed_font(self):
        table = set(re.findall(r"^\s+([a-z_0-9]+): 0x[0-9a-f]+,", (WEB / "js" / "core" / "icon-codepoints.js").read_text(encoding="utf-8"), re.M))
        names = (WEB / "js" / "core" / "icons.js").read_text(encoding="utf-8").split("const NAMES = {", 1)[1].split("};", 1)[0]
        for glyph in set(re.findall(r':\s*"([a-z_0-9]+)"', names)):
            self.assertIn(glyph, table, f"icon '{glyph}' is not in icon-codepoints.js: rebuild the icon font")
        for page in (WEB / "pages").rglob("*.html"):
            for glyph in re.findall(r'data-icon="([a-z_0-9]+)"', page.read_text(encoding="utf-8")):
                self.assertIn(glyph, table, f"{page.name} uses the icon '{glyph}'")


if __name__ == "__main__":
    unittest.main()


class Styles(unittest.TestCase):
    def test_a_bare_class_is_defined_in_one_stylesheet_only(self):
        seen = {}
        for css in sorted((WEB / "css").rglob("*.css")):
            text = re.sub(r"/\*.*?\*/", "", css.read_text(encoding="utf-8"), flags=re.S)
            for head in re.findall(r"([^{}]+)\{", text):
                for selector in head.split(","):
                    m = re.match(r"^\.([A-Za-z][\w-]*)\s*$", selector.strip())
                    if m:
                        seen.setdefault(m.group(1), set()).add(css.name)
        shared = {c: sorted(files) for c, files in seen.items() if len(files) > 1}
        self.assertEqual(shared, {}, "a class defined in two stylesheets lets two components distort each other")
