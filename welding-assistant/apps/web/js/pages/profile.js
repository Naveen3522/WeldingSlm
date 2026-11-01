// The Profile page: your username and role, then four plain rows: Username, Email, Password and Recovery key. Everything about your own account is here.
// The recovery key is only ever shown once: after "Make new key" (which asks for your password first) it is hidden behind dots until the eye is pressed,
// the copy button works while it is hidden, and leaving the page returns the row to "Not shown".

import { $, el, icon, iconButton, pageHeading } from "../core/dom.js";
import { call } from "../core/api.js";
import { startPage } from "../components/navbar.js";
import { copyButton } from "../components/copy-button.js";
import { openDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";
import { addEyes } from "../components/password-eye.js";

const DOTS = "••••-••••-••••-••••";

function row(label, value, action) {
  const node = el("div", "profile__row");
  const shown = typeof value === "string" ? el("span", "profile__value", value) : value;
  node.append(el("span", "profile__label", label), shown, action || el("span"));
  return node;
}

function textLink(label, onClick, href) {
  const node = el(href ? "a" : "button", "profile__link", label);
  if (href) node.href = href;
  else node.type = "button";
  if (onClick) node.onclick = onClick;
  return node;
}

// Asks for the password, then asks the server for a new key. -> the key, or "" when the person closed the dialog.
function askForNewKey() {
  return new Promise((resolve) => {
    const body = el("div");
    body.append(el("p", null, "A new key replaces the old one, which stops working. You need your password to make it."));
    const label = el("label", "field");
    label.append(el("span", null, "Your password"));
    const input = el("input");
    input.type = "password";
    input.autocomplete = "current-password";
    label.appendChild(input);
    const out = el("div");
    body.append(label, out);
    addEyes(body);
    let done = false;
    openDialog({
      title: "Make a new recovery key",
      body,
      onClose: () => !done && resolve(""),
      actions: [
        { label: "Cancel" },
        {
          label: "Make new key",
          primary: true,
          onClick: async (close) => {
            const result = await call("POST", "/api/auth/recovery-key", { password: input.value });
            if (!result.ok) return out.replaceChildren(notice("error", result.data.error || "Could not make a key."));
            done = true;
            resolve(result.data.recovery_key);
            close();
          },
        },
      ],
    });
  });
}

function keyRow(rows, warning) {
  const value = el("span", "profile__value", "Not shown");
  const action = el("span", "profile__action");
  const make = textLink("Make new key", async () => {
    const key = await askForNewKey();
    if (!key) return;
    let visible = false;
    value.className = "profile__value profile__value--mono";
    value.textContent = DOTS;
    const eye = iconButton("eye", "Show the key", () => {
      visible = !visible;
      value.textContent = visible ? key : DOTS;
      eye.title = visible ? "Hide the key" : "Show the key";
      eye.setAttribute("aria-label", eye.title);
      eye.replaceChildren(icon(visible ? "eye-off" : "eye"));
    }, "plain");
    action.replaceChildren(eye, copyButton(key, "Copy the key"));
    warning.replaceChildren(notice("warn", "Save this key now. It is lost when you leave this page."));
  });
  action.appendChild(make);
  rows.appendChild(row("Recovery key", value, action));
}

startPage({ active: "profile" }).then((user) => {
  const page = $("#pageBody");
  const role = user.role_name || user.level_name || "User";
  page.appendChild(pageHeading("Profile", "Your account details. Change your password or make a new recovery key."));
  const head = el("div", "profile__head");
  const names = el("div");
  names.append(el("div", "profile__name", user.username), el("div", "profile__role", role));
  head.append(el("span", "profile__avatar", (user.username || "?").slice(0, 1).toUpperCase()), names);

  const rows = el("div", "profile__rows");
  rows.append(
    row("Username", user.username, copyButton(user.username, "Copy the username")),
    row("Email", user.email, copyButton(user.email, "Copy the email")),
    row("Password", el("span", "profile__value profile__value--mono", "••••••••"), textLink("Change", null, "/password.html")),
  );
  const warning = el("div");
  keyRow(rows, warning);
  page.append(head, rows, warning);
});
