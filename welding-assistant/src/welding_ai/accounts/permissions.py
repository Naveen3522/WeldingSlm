"""Who may see and do what: screens, each with its controls, every one with a read level and a write level (1 to 10), in a plain file an Admin can edit:
data/settings/permissions.xml (or through the Permissions tab on the Users page).

    <Permissions version="2">
      <Screen id="4" name="Drafts" read="1" write="1">
        <Controls>
          <Control id="4.5" name="Delete a draft" read="10" write="10"/>
        </Controls>
      </Screen>
    </Permissions>

A person's level (from their role) is compared with these numbers. Read means they can see it; write means they can change or run it. A control needs both its
own level and its screen's level, so what a person can use is the higher of the two. The server checks every request with `can`; the pages only hide what a
person cannot use (hidden, never greyed).

Rules the file must keep, or it is refused and the last good one stays in force (the reason is kept in `problem`): valid XML, version 2, only screens and
controls the app knows, no id twice, levels whole numbers 1 to 10, write not below read, a control not below its screen, and the Users screens (People, Roles,
Permissions) stay at 10. An entry missing from the file is Admin only.
"""
import logging
import os
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("welding.permissions")
ADMIN = 10
LOCKED_SCREENS = ("8", "9", "10")             # Users: People, Roles, Permissions are always Admin only

# (id, name, read, write, [(control id, name, read, write)]): the screens the app has and the levels they start at.
# A screen's write level is the lowest write level of any of its controls (a control cannot be below its screen), so Formulas is (1, 1) because a User may
# suggest, and the controls that add or edit sit at 5.
CATALOGUE = [
    ("1", "Chat (Ask)", 1, 1, [("1.1", "Ask a question", 1, 1), ("1.2", "AI summary button", 1, 1), ("1.3", "Download answer as Word", 1, 1),
                                ("1.4", "Generate a Word file from a template", 1, 1)]),
    ("12", "Agents", 1, 1, [("12.1", "Ask an agent", 1, 1)]),
    ("2", "Library", 1, 1, [("2.1", "Open a document", 1, 1), ("2.2", "Add documents", 5, 5), ("2.3", "Delete documents", 5, 5),
                       ("2.4", "Reindex documents (deletes the question pairs written by hand)", 5, 5)]),
    ("3", "Formulas", 1, 1, [("3.1", "Calculate", 1, 1), ("3.2", "Suggest a formula", 1, 1), ("3.3", "Add or edit a formula", 5, 5),
                             ("3.4", "Approve a suggestion", 5, 5), ("3.5", "Remove a formula", 5, 5)]),
    ("4", "Drafts", 1, 1, [("4.1", "New draft (+)", 1, 1), ("4.2", "Edit a draft", 1, 1), ("4.3", "Save a draft", 1, 1), ("4.4", "Download Word", 1, 1),
                           ("4.5", "Delete a draft", 10, 10), ("4.6", "Insert or delete rows and columns in a draft", 5, 5)]),
    ("5", "Templates", 5, 5, [("5.1", "Edit a template (rows and columns)", 5, 5), ("5.2", "Save a template", 5, 5), ("5.3", "Download a template", 5, 5),
                              ("5.4", "Upload a Word table", 5, 5), ("5.5", "New template", 5, 5), ("5.6", "Remove a template", 5, 5)]),
    ("6", "Models", 5, 5, [("6.1", "View models and training", 5, 5), ("6.2", "Use this model", 10, 10), ("6.3", "Rename or delete a model", 10, 10),
                            ("6.4", "Full retrain", 10, 10), ("6.5", "Test the training setup", 10, 10)]),
    ("7", "Question pairs", 5, 5, [("7.1", "Approve, reject, edit", 5, 5), ("7.2", "Add a pair", 5, 5), ("7.3", "Check and test", 5, 5)]),
    ("8", "Users: People", 10, 10, [("8.1", "Change a role", 10, 10), ("8.2", "Reset password", 10, 10), ("8.3", "Remove a person", 10, 10)]),
    ("9", "Users: Roles", 10, 10, [("9.1", "Add, edit, delete roles", 10, 10)]),
    ("10", "Users: Permissions", 10, 10, [("10.1", "Save permissions", 10, 10)]),
    ("11", "Activity", 10, 10, [("11.1", "Export for Excel", 10, 10), ("11.2", "Clear activity", 10, 10)]),
    ("13", "My submissions", 1, 1, [("13.1", "Send a question and answer for approval", 1, 1)]),
]

# The older rights the routes still ask for, and the screen or control each one now means: (id, "read" | "write")
RIGHTS = {
    "ask": ("1.1", "read"), "summary": ("1.2", "write"), "history": ("1", "read"), "agents": ("12", "read"), "suggest_formula": ("3.2", "write"),
    "edit_formula": ("3.3", "write"), "library": ("2.2", "write"), "reindex": ("2.4", "write"), "doc_delete": ("2.3", "write"), "model_edit": ("6.3", "write"), "train_test": ("6.5", "write"), "pair_add": ("7.2", "write"), "pair_check": ("7.3", "write"), "training_pairs": ("7.1", "write"), "view_models": ("6.1", "read"), "models": ("6.2", "write"),
    "users": ("8", "read"), "activity": ("11", "read"), "admin": ("10", "write"), "train": ("6.4", "write"), "roles": ("9", "read"), "permissions": ("10", "read"),
    "user_role": ("8.1", "write"), "user_reset": ("8.2", "write"), "user_remove": ("8.3", "write"), "roles_save": ("9.1", "write"), "permissions_save": ("10.1", "write"),
    "export_activity": ("11.1", "read"), "clear_activity": ("11.2", "write"), "manual_answer": ("7.2", "write"), "my_pairs": ("13", "read"), "suggest_pair": ("13.1", "write"), "template_ask": ("1.4", "write"),
    "draft_view": ("4", "read"), "draft_new": ("4.1", "write"), "draft_edit": ("4.2", "write"), "draft_save": ("4.3", "write"), "draft_download": ("4.4", "read"),
    "draft_delete": ("4.5", "write"), "draft_shape": ("4.6", "write"),
    "template_view": ("5", "read"), "template_edit": ("5.1", "write"), "template_save": ("5.2", "write"), "template_download": ("5.3", "read"),
    "template_upload": ("5.4", "write"), "template_new": ("5.5", "write"), "template_remove": ("5.6", "write"),
}


class PermissionsError(Exception):
    pass


def _level(text, where):
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise PermissionsError(f"{where}: the level must be a whole number from 1 to 10.")
    if not 1 <= value <= ADMIN:
        raise PermissionsError(f"{where}: the level must be a whole number from 1 to 10.")
    return value


def defaults():
    """The built-in levels as a tree: [{id, name, read, write, controls: [{id, name, read, write}]}]."""
    return [{"id": sid, "name": name, "read": rd, "write": wr, "controls": [{"id": cid, "name": cname, "read": crd, "write": cwr} for cid, cname, crd, cwr in controls]}
            for sid, name, rd, wr, controls in CATALOGUE]


def validate(tree):
    """-> the tree with every known screen and control present (names from the app), or raises PermissionsError saying what is wrong."""
    known = {s["id"]: s for s in defaults()}
    seen, out = set(), []
    for screen in tree:
        sid = str(screen.get("id"))
        if sid not in known:
            raise PermissionsError(f"Screen {sid} is not a screen of this app.")
        if sid in seen:
            raise PermissionsError(f"Screen {sid} appears twice.")
        seen.add(sid)
        rd, wr = _level(screen.get("read"), f"{known[sid]['name']}"), _level(screen.get("write"), f"{known[sid]['name']}")
        if wr < rd:
            raise PermissionsError(f"{known[sid]['name']}: the write level cannot be below the read level.")
        if sid in LOCKED_SCREENS and (rd, wr) != (ADMIN, ADMIN):
            raise PermissionsError(f"{known[sid]['name']} stays Admin only (10).")
        controls, cseen = [], set()
        known_controls = {c["id"]: c for c in known[sid]["controls"]}
        for control in screen.get("controls", []):
            cid = str(control.get("id"))
            if cid not in known_controls:
                raise PermissionsError(f"Control {cid} is not a control of {known[sid]['name']}.")
            if cid in cseen:
                raise PermissionsError(f"Control {cid} appears twice.")
            cseen.add(cid)
            crd, cwr = _level(control.get("read"), known_controls[cid]["name"]), _level(control.get("write"), known_controls[cid]["name"])
            if cwr < crd:
                raise PermissionsError(f"{known_controls[cid]['name']}: the write level cannot be below the read level.")
            if crd < rd or cwr < wr:
                raise PermissionsError(f"{known_controls[cid]['name']}: a control cannot be below its screen ({known[sid]['name']}).")
            if sid in LOCKED_SCREENS and (crd, cwr) != (ADMIN, ADMIN):
                raise PermissionsError(f"{known_controls[cid]['name']} stays Admin only (10).")
            controls.append({"id": cid, "name": known_controls[cid]["name"], "read": crd, "write": cwr})
        out.append({"id": sid, "name": known[sid]["name"], "read": rd, "write": wr, "controls": controls})
    return out


def parse(text):
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise PermissionsError(f"The file is not valid XML ({e}).")
    if root.tag != "Permissions" or root.get("version") != "2":
        raise PermissionsError('The file must start with <Permissions version="2">.')
    return validate([{"id": s.get("id"), "read": s.get("read"), "write": s.get("write"),
                      "controls": [{"id": c.get("id"), "read": c.get("read"), "write": c.get("write")} for c in s.findall("Controls/Control") + s.findall("Control")]} for s in root.findall("Screen")])


def render(tree):
    root = ET.Element("Permissions", version="2")
    for s in tree:
        screen = ET.SubElement(root, "Screen", id=s["id"], name=s["name"], read=str(s["read"]), write=str(s["write"]))
        controls = ET.SubElement(screen, "Controls")                 # each screen, then its controls in a group of their own, every one with read and write
        for c in s["controls"]:
            ET.SubElement(controls, "Control", id=c["id"], name=c["name"], read=str(c["read"]), write=str(c["write"]))
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


class Permissions:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.problem = ""
        self._lock = threading.RLock()
        self._stamp = None
        self._set(defaults())
        self.reload()

    def _set(self, tree):
        self.tree = tree
        self._screens = {s["id"]: s for s in tree}
        self._controls = {c["id"]: (s, c) for s in tree for c in s["controls"]}

    def ensure_file(self):
        """Writes the built-in levels to the file when there is none, so there is something to edit."""
        with self._lock:
            if self.path and not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(render(defaults()), encoding="utf-8")
            self.reload()
            self._add_new_controls()

    def _add_new_controls(self):
        """A control that a newer version of the app added is missing from a file written by an older one, which would make it Admin only. Add it at its built-in level
        (the old file is kept as permissions.xml.bak); everything already in the file stays exactly as it is."""
        if not self.path or not self.path.is_file() or self.problem:
            return
        present = {c["id"] for s in self.tree for c in s["controls"]}
        missing = [(s["id"], c) for s in defaults() for c in s["controls"] if c["id"] not in present]
        if not missing:
            return
        tree = validate_copy(self.tree)
        by_id = {s["id"]: s for s in tree}
        for screen in defaults():                                          # a screen a newer version added
            if screen["id"] not in by_id:
                tree.append({**screen, "controls": []})
                by_id[screen["id"]] = tree[-1]
        for sid, control in missing:
            if sid in by_id:
                by_id[sid]["controls"].append(dict(control))
        try:
            self.save(tree)
            log.info("permissions: added %s", ", ".join(c["id"] for _s, c in missing))
        except PermissionsError as e:
            log.warning("permissions: could not add the new controls (%s)", e)

    def reload(self):
        """Reads the file when it has changed. A file that breaks a rule is refused: the last good levels stay and `problem` says why."""
        if not self.path or not self.path.is_file():
            return
        with self._lock:
            stamp = self.path.stat().st_mtime_ns
            if stamp == self._stamp:
                return
            self._stamp = stamp
            try:
                self._set(parse(self.path.read_text(encoding="utf-8")))
                self.problem = ""
            except (OSError, PermissionsError) as e:
                self.problem = f"{self.path.name} was not used: {e}"
                log.warning(self.problem)

    # ------------------------------------------------------------------ the checks
    def _need(self, item, mode):
        """The level a screen ("4") or a control ("4.5") asks for. A control needs the higher of its own level and its screen's."""
        self.reload()
        if item in self._screens:
            return self._screens[item][mode]
        if item in self._controls:
            screen, control = self._controls[item]
            return max(screen[mode], control[mode])
        return ADMIN                                              # not in the file: Admin only

    def can(self, level, item, mode="read"):
        return level >= self._need(item, mode)

    def allowed(self, level, right):
        """The older call: a named right (`ask`, `edit_formula`, ...) is a screen or control and a mode."""
        item, mode = RIGHTS[right]
        return self.can(level, item, mode)

    def resolve(self, level):
        """What this level may see and do, for the page to show or hide: {"screens": {"4": {"read": true, "write": true}}, "controls": {"4.1": {...}}}."""
        self.reload()
        screens = {sid: {"read": self.can(level, sid, "read"), "write": self.can(level, sid, "write")} for sid in self._screens}
        controls = {cid: {"read": self.can(level, cid, "read"), "write": self.can(level, cid, "write")} for cid in self._controls}
        return {"screens": screens, "controls": controls}

    def levels_tree(self):
        """Every screen and control the app has, with the levels in force; one the file leaves out is shown as Admin only (10), which is what it means."""
        self.reload()
        out = []
        for base in defaults():
            screen = self._screens.get(base["id"])
            controls = {c["id"]: c for c in screen["controls"]} if screen else {}
            out.append({"id": base["id"], "name": base["name"], "read": screen["read"] if screen else ADMIN, "write": screen["write"] if screen else ADMIN,
                        "controls": [dict(controls.get(c["id"]) or {**c, "read": ADMIN, "write": ADMIN}) for c in base["controls"]]})
        return out

    def save(self, tree):
        """Checks the whole tree and writes the file (the previous file is kept as permissions.xml.bak). -> the saved tree."""
        clean = validate(tree)
        with self._lock:
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists():
                    self.path.replace(self.path.with_suffix(".xml.bak"))
                tmp = self.path.with_suffix(".xml.tmp")
                tmp.write_text(render(clean), encoding="utf-8")
                os.replace(tmp, self.path)
                self._stamp = self.path.stat().st_mtime_ns
            self._set(clean)
            self.problem = ""
        return self.levels_tree()


def validate_copy(tree):
    return [{**s, "controls": [dict(c) for c in s["controls"]]} for s in tree]


_current = None


def current():
    """The permissions in force for this server: the file under paths.SETTINGS_DIR, or the built-in levels when there is no file."""
    global _current
    if _current is None:
        from welding_ai import paths
        _current = Permissions(paths.SETTINGS_DIR / "permissions.xml")
    return _current
