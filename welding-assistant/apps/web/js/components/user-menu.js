// The "Username (role)" menu: one flat list of what this person can open, then Sign out. Only what the permissions allow is listed (nothing is greyed).
//   Admin: Profile, Model management, Users, Activity, Sign out     Weld Engineer: Profile, Model management, Sign out     User: Profile, Sign out

import { el, icon } from "../core/dom.js";
import { can } from "../core/permissions.js";
import { signOut } from "../core/session.js";
import { attachMenu } from "./menu.js";

// Older pages ask for the model the person picked; the choice is gone (the AI summary always uses the model in use), so the server decides.
export function chosenModel() {
  return "";
}

// What the menu can hold: the screen that has to be readable, the label, the icon, where it goes, and the key a page gives to mark itself as current.
const ENTRIES = [
  { key: "profile", label: "Profile", icon: "account", href: "/profile" },
  { key: "submissions", label: "My submissions", icon: "lightbulb", href: "/submissions", screen: "13" },
  { key: "model", label: "Model management", icon: "brain", href: "/admin/model", screen: "6" },
  { key: "users", label: "Users", icon: "users", href: "/admin/users", screen: "8" },
  { key: "activity", label: "Activity", icon: "list", href: "/admin/activity", screen: "11" },
];

function item(entry, active) {
  const node = el("a", "menu-item" + (entry.key === active ? " current" : ""));
  node.href = entry.href;
  node.append(icon(entry.icon), el("span", null, entry.label));
  return node;
}

// -> the rows for this person (also used inside the hamburger).
export function accountRows(active) {
  const rows = ENTRIES.filter((e) => !e.screen || can(e.screen)).map((e) => item(e, active));
  const out = el("button", "menu-item danger");
  out.type = "button";
  out.append(icon("signout"), el("span", null, "Sign out"));
  out.onclick = signOut;
  return [...rows, el("div", "menu-line"), out];
}

export function whoRow(user) {
  const who = el("div", "menu-who");
  const text = el("div");
  text.append(el("b", null, user.username), el("small", null, user.role_name || user.level_name || "User"));
  who.append(el("span", "menu-avatar", (user.username || "?").slice(0, 1).toUpperCase()), text);
  return who;
}

// -> the button "name (role)" with its menu, ready for the top bar.
export function userMenu(user, active) {
  const wrap = el("div", "menu-wrap acct-wrap");
  const button = el("button", "iconbtn");
  button.id = "acctBtn";
  button.type = "button";
  button.append(icon("account"), el("span", "acct-name", `${user.username} (${user.role_name || user.level_name || "User"})`), icon("chevron-down"));
  const panel = el("div", "menu-panel");
  panel.append(whoRow(user), ...accountRows(active));
  wrap.append(button, panel);
  attachMenu(wrap, button, panel);
  return wrap;
}
