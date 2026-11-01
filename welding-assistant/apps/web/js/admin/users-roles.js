// Users, Roles: the roles people can have, as an editable list (a name and a level). + adds a row, - removes the ticked rows, the disk saves everything at once.
// User (1) and Admin (10) are fixed. Levels may repeat, so two roles can sit at the same level; a name cannot repeat and "Admin" is reserved.
// The disk stays off until every role has a name and a level and nothing is wrong; a role people still hold is refused by the server with a one-line message.

import { el, icon, iconButton } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { notice } from "../components/notice.js";

const LEVELS = [1, 2, 3, 4, 5, 6, 7, 8, 9];

export function rolesPanel(panel) {
  let rows = [];
  let saved = "";
  const status = el("div", "roles__status");
  const bar = el("div", "roles__tools");
  const add = iconButton("plus", "Add a role", () => {
    const admin = rows.findIndex((r) => r.fixed && r.level === 10);
    rows.splice(admin < 0 ? rows.length : admin, 0, { id: null, name: "", level: "", fixed: false, tick: false });
    draw();
    const inputs = panel.querySelectorAll(".roles__name:not(:disabled)");
    const fresh = [...inputs].find((i) => !i.value);
    if (fresh) fresh.focus();
  });
  const remove = iconButton("minus", "Remove the ticked roles", () => {
    rows = rows.filter((r) => !r.tick);
    draw();
  });
  const save = iconButton("save", "Save changes", saveAll);
  save.classList.add("roles__save");
  bar.append(add, remove, save);
  const tableBox = el("div", "table-wrap");
  const problem = el("div", "roles__status");
  panel.append(bar, tableBox, problem, status);

  const editable = can("9.1", "write");
  if (!editable) bar.hidden = true;

  const snapshot = () => JSON.stringify(rows.map((r) => [r.id, r.name.trim(), r.level]));

  // -> the first thing wrong with the list, or "".
  function check() {
    const seen = new Set();
    for (const r of rows) {
      const name = r.name.trim();
      if (!name) return "Every role needs a name.";
      if (r.level === "" || r.level == null) return "Every role needs a level.";
      if (!r.fixed && name.toLowerCase() === "admin") return "Admin is a reserved name.";
      if (seen.has(name.toLowerCase())) return `"${name}" is used twice.`;
      seen.add(name.toLowerCase());
    }
    return "";
  }

  function refresh() {
    const wrong = check();
    problem.replaceChildren(wrong ? notice("error", wrong) : "");
    save.disabled = Boolean(wrong) || snapshot() === saved;
    save.style.opacity = save.disabled ? "0.4" : "1";
    remove.disabled = !rows.some((r) => r.tick && !r.fixed);
    remove.style.opacity = remove.disabled ? "0.4" : "1";
  }

  async function saveAll() {
    if (check()) return;
    const result = await call("POST", "/api/roles/save", { roles: rows.map((r) => ({ id: r.id, name: r.name.trim(), level: Number(r.level) })) });
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not save the roles."));
    status.replaceChildren(notice("success", "Roles saved."));
    load(result.data.roles);
  }

  function draw() {
    const table = el("table", "table");
    const head = el("tr");
    ["", "Role name", "Level"].forEach((label) => head.appendChild(el("th", null, label)));
    table.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    table.appendChild(body);
    rows.forEach((r) => {
      const tick = el("input");
      tick.type = "checkbox";
      tick.checked = r.tick;
      tick.disabled = r.fixed || !editable;
      tick.setAttribute("aria-label", "Select " + (r.name || "this role"));
      tick.onchange = () => {
        r.tick = tick.checked;
        refresh();
      };
      const name = el("input", "roles__name");
      name.type = "text";
      name.value = r.name;
      name.placeholder = "Role name";
      name.disabled = r.fixed || !editable;
      name.setAttribute("aria-label", "Role name");
      name.oninput = () => {
        r.name = name.value;
        refresh();
      };
      const level = el("select", "roles__level");
      level.setAttribute("aria-label", "Level");
      if (r.fixed) {
        const only = el("option", null, String(r.level));
        level.appendChild(only);
      } else {
        const blank = el("option", null, "Level");
        blank.value = "";
        level.appendChild(blank);
        LEVELS.forEach((n) => level.appendChild(el("option", null, String(n))));
        level.value = r.level === "" ? "" : String(r.level);
      }
      level.disabled = r.fixed || !editable;
      level.onchange = () => {
        r.level = level.value === "" ? "" : Number(level.value);
        refresh();
      };
      const tr = el("tr");
      [tick, name, level].forEach((node) => {
        const cell = el("td");
        cell.appendChild(node);
        tr.appendChild(cell);
      });
      body.appendChild(tr);
    });
    tableBox.replaceChildren(table);
    refresh();
  }

  function load(list) {
    rows = list.map((r) => ({ id: r.id, name: r.name, level: r.level, fixed: r.fixed, tick: false }));
    saved = snapshot();
    draw();
  }

  call("GET", "/api/roles").then((result) => {
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not load the roles."));
    load(result.data.roles);
  });
}
