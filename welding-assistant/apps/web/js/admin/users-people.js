// Users, People: everyone who has an account. Search, change a person's role from a drop-down (it applies from their next request), reset a password (a
// one-time password valid for 24 hours, shown once) and remove a person. People create their own accounts, so there is no Add button. What each person may do
// here follows the permissions: a control the signed-in Admin may not use is not shown.

import { el, icon, iconButton } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { confirmDialog, openDialog } from "../components/dialog.js";
import { copyButton } from "../components/copy-button.js";
import { notice } from "../components/notice.js";

const DOTS = "••••-••••-••••";

// The one-time password is shown once, hidden behind dots until the eye is pressed.
function showTempPassword(person, temp) {
  const body = el("div", "people__temp");
  const shown = el("code", "people__temp-key", DOTS);
  let visible = false;
  const eye = iconButton("eye", "Show the password", () => {
    visible = !visible;
    shown.textContent = visible ? temp : DOTS;
    eye.title = visible ? "Hide the password" : "Show the password";
    eye.setAttribute("aria-label", eye.title);
    eye.replaceChildren(icon(visible ? "eye-off" : "eye"));
  }, "plain");
  const line = el("div", "people__temp-line");
  line.append(shown, eye, copyButton(temp, "Copy the password"));
  body.append(el("label", "hint", "One-time password"), line, notice("warn", "Save this now. It is lost when you close this. Valid for 24 hours."));
  openDialog({ title: `Password reset for ${person.username}`, body, actions: [{ label: "Done", primary: true }] });
}

export function peoplePanel(panel, me) {
  let users = [];
  let roles = [];
  const status = el("div", "people__status");
  const search = el("input");
  search.type = "search";
  search.placeholder = "Search by username, email or role";
  search.setAttribute("aria-label", "Search people");
  const tools = el("div", "people__tools");
  const box = el("div", "search people__search");
  box.append(icon("search"), search);
  tools.appendChild(box);
  const tableBox = el("div", "table-wrap");
  panel.append(tools, status, tableBox);

  const say = (kind, text) => status.replaceChildren(text ? notice(kind, text) : "");

  async function load() {
    const result = await call("GET", "/api/users");
    if (!result.ok) return say("error", result.data.error || "Could not load the people.");
    users = result.data.users;
    roles = result.data.roles;
    draw();
  }

  async function changeRole(person, select) {
    const role = roles.find((r) => String(r.id) === select.value);
    const result = await call("POST", `/api/users/${person.id}/update`, { username: person.username, email: person.email, role_id: role.id });
    if (!result.ok) {
      select.value = String(person.role_id);
      return say("error", result.data.error || "Could not change the role.");
    }
    say("success", `${person.username} is now ${role.name}. It applies from their next request.`);
    await load();
  }

  async function resetPassword(person) {
    const result = await call("POST", `/api/users/${person.id}/reset`, {});
    if (!result.ok) return say("error", result.data.error || "Could not reset the password.");
    say("", "");
    showTempPassword(person, result.data.temp_password);
  }

  async function remove(person) {
    const yes = await confirmDialog({ title: `Remove ${person.username}?`, text: "Their account is deleted. Their past questions stay in the activity log.", yes: "Remove", danger: true });
    if (!yes) return;
    const result = await call("POST", `/api/users/${person.id}/delete`, {});
    if (!result.ok) return say("error", result.data.error || "Could not remove the person.");
    say("success", `${person.username} was removed.`);
    await load();
  }

  function roleCell(person) {
    const mine = person.id === me.id;
    const select = el("select", "people__role");
    select.setAttribute("aria-label", `Role for ${person.username}`);
    const isAdmin = person.role_name === "Admin";
    roles.filter((r) => r.name !== "Admin" || isAdmin).forEach((r) => {
      const option = el("option", null, r.name);
      option.value = String(r.id);
      select.appendChild(option);
    });
    select.value = String(person.role_id);
    select.disabled = mine || !can("8.1", "write");
    select.onchange = () => changeRole(person, select);
    return select;
  }

  function draw() {
    const text = search.value.trim().toLowerCase();
    const rows = users.filter((u) => !text || `${u.username} ${u.email} ${u.role_name}`.toLowerCase().includes(text));
    const table = el("table", "table");
    const head = el("tr");
    ["Username", "Email", "Role", "Level"].forEach((label) => head.appendChild(el("th", null, label)));
    head.appendChild(el("th", "right", "Actions"));
    table.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    table.appendChild(body);
    if (!rows.length) {
      const empty = el("tr");
      const cell = el("td", "hint", users.length ? "No one matches." : "No people yet.");
      cell.colSpan = 5;
      empty.appendChild(cell);
      body.appendChild(empty);
    }
    rows.forEach((person) => {
      const tr = el("tr");
      const who = el("div", "person");
      who.append(el("i", null, (person.username || "?").slice(0, 1).toUpperCase()), el("span", null, person.username + (person.id === me.id ? " (you)" : "")));
      const name = el("td");
      name.appendChild(who);
      const level = el("td", null, String(person.level));
      const acts = el("div", "acts");
      if (can("8.2", "write")) acts.appendChild(iconButton("key", "Reset password", () => resetPassword(person)));
      if (person.id !== me.id && can("8.3", "write")) acts.appendChild(iconButton("trash", "Remove", () => remove(person), "danger"));
      const actions = el("td");
      actions.appendChild(acts);
      const email = el("td", "people__email", person.email);
      const role = el("td");
      role.appendChild(roleCell(person));
      tr.append(name, email, role, level, actions);
      body.appendChild(tr);
    });
    tableBox.replaceChildren(table);
  }

  search.oninput = draw;
  load();
}
