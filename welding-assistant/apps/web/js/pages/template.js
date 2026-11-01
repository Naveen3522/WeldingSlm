// The template editor: change what every new draft starts from. Right-click for insert, Delete for rows, "Upload Word table" instead of typing, the disk to save,
// download to edit it in Word. Only people whose permission allows it get here; each control also needs its own permission.

import { $, el, icon, iconButton } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { startPage } from "../components/navbar.js";
import { confirmDialog } from "../components/dialog.js";
import { mountGrid } from "../components/grid-editor.js";
import { notice } from "../components/notice.js";
import { uploadWordTable } from "../drafts/word-upload.js";

const params = new URLSearchParams(location.search);

startPage({ active: "drafts", screen: "5" }).then(async () => {
  const page = $("#pageBody");
  let id = params.get("id") ? Number(params.get("id")) : null;
  const status = el("div", "work__status");

  const name = el("input", "work__name");
  name.type = "text";
  name.placeholder = "Template name";
  name.maxLength = 120;
  name.setAttribute("aria-label", "Template name");
  const title = el("h1", "work__title", "Template");
  const sub = el("p", "page-desc", "Change the rows and columns every new draft starts from.");
  const titles = el("div");
  titles.style.flex = "1";
  titles.append(title, sub);
  const save = iconButton("save", "Save", () => saveTemplate());
  save.classList.add("primary");
  const download = el("a", "ibtn");
  download.title = "Download template (Word)";
  download.setAttribute("aria-label", "Download template (Word)");
  download.appendChild(icon("download"));
  const head = el("div", "work__head page-heading");
  head.append(titles);
  if (can("5.2", "write") || can("5.5", "write")) head.appendChild(save);
  if (can("5.3", "read")) head.appendChild(download);
  const nameRow = el("label", "field");
  nameRow.append(el("span", null, "Name"), name);
  const gridHolder = el("div");
  page.append(head, nameRow, status, gridHolder);

  const grid = mountGrid(gridHolder, { structure: true, onChange: refresh, onUpload: can("5.4", "write") ? () => uploadWordTable((h, r) => ((grid.load(h, r), refresh(), markDirty()))) : null });
  let nameSaved = "";
  let forceDirty = false;
  const markDirty = () => {
    forceDirty = true;
    refresh();
  };
  const dirty = () => grid.isDirty() || forceDirty || name.value.trim() !== nameSaved;

  function refresh() {
    save.disabled = !dirty();
    save.style.opacity = save.disabled ? "0.4" : "1";
    title.textContent = id === null ? "New template" : "Edit template";            // the heading stays as it is while the name is typed
    if (id !== null) download.href = `/api/templates/${id}/download.docx`;
    download.hidden = id === null;
  }

  async function saveTemplate() {
    const { heads, rows } = grid.data();
    const result = await call("POST", "/api/templates/save", { id, name: name.value, heads, rows });
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not save the template."));
    const t = result.data.template;
    id = t.id;
    name.value = nameSaved = t.name;
    forceDirty = false;
    history.replaceState(null, "", `/template.html?id=${id}`);
    grid.load(t.heads, t.rows);
    status.replaceChildren(notice("success", "Saved."));
    refresh();
  }

  async function leave() {
    if (dirty()) {
      const yes = await confirmDialog({ title: "Discard unsaved changes?", text: "You have changes that are not saved. Leaving now goes back to the last saved version.", yes: "Discard", danger: true });
      if (!yes) return;
    }
    window.onbeforeunload = null;
    location.href = "/drafts.html";
  }

  window.weldLeave = leave;                                      // the page's Back button asks first when there are unsaved changes
  window.onbeforeunload = (event) => {
    if (dirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  name.oninput = refresh;

  if (id !== null) {
    const result = await call("GET", `/api/templates/${id}`);
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not open the template."));
    const t = result.data.template;
    name.value = nameSaved = t.name;
    grid.load(t.heads, t.rows);
  } else {
    grid.load([], []);
    name.focus();
  }
  refresh();
});
