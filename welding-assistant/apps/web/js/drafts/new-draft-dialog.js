// "New draft": a name and a template. A draft is only made when it is first saved, so Create just opens the editor on that template.
// People who may manage templates also see a pencil and a trash beside each one, and a "New template" row; everyone else sees only the names.

import { el, icon, iconButton } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { confirmDialog, openDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";

export async function newDraftDialog() {
  const result = await call("GET", "/api/templates");
  const templates = result.ok ? result.data.templates : [];
  const body = el("div", "newdraft");
  const name = el("input");
  name.type = "text";
  name.placeholder = "Name this draft";
  name.maxLength = 120;
  name.setAttribute("aria-label", "Name");
  const nameLabel = el("label", "field");
  nameLabel.append(el("span", null, "Name"), name);
  const list = el("div", "newdraft__list");
  const status = el("div");
  body.append(nameLabel, el("span", "newdraft__title", "Template"), list, status);
  let chosen = templates.length ? templates[0].id : null;
  let dialog = null;

  async function remove(template) {
    const yes = await confirmDialog({ title: `Remove ${template.name}?`, text: "Drafts already made from it keep their own copy.", yes: "Remove", danger: true });
    if (!yes) return;
    const done = await call("POST", `/api/templates/${template.id}/delete`, {});
    if (!done.ok) return status.replaceChildren(notice("error", done.data.error || "Could not remove it."));
    dialog.close();
    newDraftDialog();
  }

  function draw() {
    list.replaceChildren();
    if (!templates.length) list.appendChild(el("p", "hint", "There are no templates yet."));
    templates.forEach((t) => {
      const row = el("div", "newdraft__row");
      const radio = el("input");
      radio.type = "radio";
      radio.name = "template";
      radio.checked = t.id === chosen;
      radio.onchange = () => (chosen = t.id);
      const label = el("label", "newdraft__name");
      label.append(radio, el("span", null, t.name), el("small", "hint", ` ${t.fields} fields`));
      row.appendChild(label);
      if (can("5.1", "write")) {
        const edit = iconButton("edit", "Edit this template", () => (location.href = `/template.html?id=${t.id}`), "plain");
        row.appendChild(edit);
      }
      if (can("5.6", "write")) row.appendChild(iconButton("trash", "Remove this template", () => remove(t), "danger"));
      list.appendChild(row);
    });
    if (can("5.5", "write")) {
      const add = el("a", "newdraft__add");
      add.href = "/template.html?new=1";
      add.append(icon("plus"), el("span", null, "New template"));
      list.appendChild(add);
    }
  }
  draw();

  dialog = openDialog({
    title: "New draft",
    body,
    actions: [
      { label: "Cancel" },
      {
        label: "Create",
        primary: true,
        onClick: () => {
          if (!name.value.trim()) return status.replaceChildren(notice("error", "Give the draft a name."));
          if (!chosen) return status.replaceChildren(notice("error", "Choose a template."));
          location.href = `/draft.html?template=${chosen}&name=${encodeURIComponent(name.value.trim())}`;
        },
      },
    ],
  });
}
