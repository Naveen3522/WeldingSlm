// Users, Permissions: every screen of the app and its controls, each with a read level and a write level (1 to 10). A screen is a group: its own row, then its
// controls beneath it. Read = can see it; write = can change or run it. The same rules as the server keep the disk off until the list is valid:
// write is not below read, and a control is not below its screen. The Users screens stay at 10.

import { el, icon } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { notice } from "../components/notice.js";

const LEVELS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const LOCKED = ["8", "9", "10"];

function levelSelect(value, label, disabled, onChange) {
  const select = el("select", "perms__level");
  select.setAttribute("aria-label", label);
  LEVELS.forEach((n) => select.appendChild(el("option", null, String(n))));
  select.value = String(value);
  select.disabled = disabled;
  select.onchange = () => onChange(Number(select.value));
  return select;
}

export function permissionsPanel(panel) {
  let screens = [];
  let saved = "";
  const open = new Set();
  const status = el("div", "perms__status");
  const bar = el("div", "perms__tools");
  const save = el("button", "ibtn");
  save.type = "button";
  save.title = "Save changes";
  save.setAttribute("aria-label", "Save changes");
  save.appendChild(icon("save"));
  bar.appendChild(save);
  const problem = el("div", "perms__status");
  const tableBox = el("div", "table-wrap");
  panel.append(bar, problem, tableBox, status);
  const editable = can("10.1", "write");
  if (!editable) save.hidden = true;

  const snapshot = () => JSON.stringify(screens.map((s) => [s.id, s.read, s.write, s.controls.map((c) => [c.id, c.read, c.write])]));

  // -> the first thing wrong, or "".
  function check() {
    for (const s of screens) {
      if (s.write < s.read) return `${s.name}: the write level cannot be below the read level.`;
      for (const c of s.controls) {
        if (c.write < c.read) return `${c.name}: the write level cannot be below the read level.`;
        if (c.read < s.read || c.write < s.write) return `${c.name}: a control cannot be below its screen (${s.name}).`;
      }
    }
    return "";
  }

  function refresh() {
    const wrong = check();
    problem.replaceChildren(wrong ? notice("error", wrong) : "");
    save.disabled = Boolean(wrong) || snapshot() === saved;
    save.style.opacity = save.disabled ? "0.4" : "1";
  }

  save.onclick = async () => {
    if (check()) return;
    const result = await call("POST", "/api/permissions/save", { screens });
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not save the permissions."));
    status.replaceChildren(notice("success", "Permissions saved. They apply from the next request."));
    screens = result.data.screens;
    saved = snapshot();
    draw();
  };

  function row(item, locked, screen) {
    const tr = el("tr", screen ? "perms__screen" : "perms__control");
    const first = el("td");
    if (screen) {
      const toggle = el("button", "ibtn");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", open.has(item.id) ? "true" : "false");
      toggle.setAttribute("aria-label", `Show the controls of ${item.name}`);
      toggle.appendChild(icon(open.has(item.id) ? "minus" : "plus"));
      toggle.onclick = () => {
        if (open.has(item.id)) open.delete(item.id);
        else open.add(item.id);
        draw();
      };
      first.appendChild(toggle);
    }
    const name = el("td", "perms__name");
    name.appendChild(el("span", screen ? "perms__label" : null, item.name));
    const read = el("td");
    read.appendChild(levelSelect(item.read, "Read level", locked || !editable, (v) => { item.read = v; refresh(); }));
    const write = el("td");
    write.appendChild(levelSelect(item.write, "Write level", locked || !editable, (v) => { item.write = v; refresh(); }));
    tr.append(first, name, read, write);
    return tr;
  }

  function draw() {
    const table = el("table", "table");
    const head = el("tr");
    ["", "Screen", "Read level", "Write level"].forEach((label) => head.appendChild(el("th", null, label)));
    table.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    table.appendChild(body);
    screens.forEach((s) => {
      const locked = LOCKED.includes(s.id);
      body.appendChild(row(s, locked, true));
      if (open.has(s.id)) {                                      // the plus opens a grid of its own under the screen, with a heading of its own
        const inner = el("table", "table perms__nested");
        const sub = el("tr");
        ["Controls", "Read level", "Write level"].forEach((label) => sub.appendChild(el("th", null, label)));
        inner.appendChild(el("thead")).appendChild(sub);
        const rows = el("tbody");
        s.controls.forEach((c) => {
          const tr = row(c, locked, false);
          tr.removeChild(tr.firstChild);                         // no plus on a control
          rows.appendChild(tr);
        });
        inner.appendChild(rows);
        const cell = el("td", "perms__nestedcell");
        cell.colSpan = 4;
        cell.appendChild(inner);
        body.appendChild(el("tr", "perms__nestedrow")).appendChild(cell);
      }
    });
    tableBox.replaceChildren(table);
    refresh();
  }

  call("GET", "/api/permissions").then((result) => {
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not load the permissions."));
    screens = result.data.screens;
    saved = snapshot();
    if (result.data.problem) status.replaceChildren(notice("warn", result.data.problem));
    draw();
  });
}
