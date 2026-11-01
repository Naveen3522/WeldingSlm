// The draft editor: the template's table with the empty cells to fill in. A draft only exists once it has been saved (the disk icon): leaving with changes that are
// not saved asks first, and going back discards them. A person who may restructure also gets insert and delete for rows and columns, for this draft only.

import { $, el, icon, iconButton } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { startPage } from "../components/navbar.js";
import { confirmDialog } from "../components/dialog.js";
import { mountGrid } from "../components/grid-editor.js";
import { notice } from "../components/notice.js";

const params = new URLSearchParams(location.search);

startPage({ active: "drafts", screen: "4" }).then(async () => {
  const page = $("#pageBody");
  let id = params.get("id") ? Number(params.get("id")) : null;
  let templateId = params.get("template") ? Number(params.get("template")) : null;
  let name = params.get("name") || "";
  let templateName = "";
  let filled = null;
  const status = el("div", "work__status");

  const title = el("h1", "work__title");
  const sub = el("p", "page-desc");
  const titles = el("div");
  titles.style.flex = "1";
  titles.append(title, sub);
  const save = iconButton("save", "Save", () => saveDraft());
  save.classList.add("primary");
  const head = el("div", "work__head page-heading");
  const excel = el("a", "ibtn");
  excel.title = "Download as Excel";
  excel.setAttribute("aria-label", "Download as Excel");
  excel.appendChild(icon("download"));
  head.append(titles, save, excel);

  let editable = can("4.2", "write") || id === null;                // a draft that is someone else's is read-only (set once it is loaded)
  const gridBox = el("div");
  const gridHolder = el("div");
  gridBox.appendChild(gridHolder);
  page.append(head, status, gridBox);
  const gridApi = mountGrid(gridHolder, { structure: can("4.6", "write") && editable, renameHeads: false, onChange: refresh });

  function refresh() {
    save.disabled = !editable || (id !== null && !gridApi.isDirty());
    save.style.opacity = save.disabled ? "0.4" : "1";
    title.textContent = name || "New draft";
    const parts = [templateName ? `Template: ${templateName}` : ""];
    if (filled) parts.push(filled.total ? (filled.complete ? "Complete" : `${filled.filled} of ${filled.total} filled`) : "");
    sub.replaceChildren(document.createTextNode(parts.filter(Boolean).join(" · ")));
    if (gridApi.isDirty() || id === null) {
      const mark = el("span", "work__unsaved");
      mark.append(el("span", "work__dot"), document.createTextNode(id === null ? "not saved yet" : "unsaved changes"));
      sub.append(" ", mark);
    }
    if (id !== null) excel.href = `/api/drafts/${id}/download.xlsx`;
    excel.hidden = id === null || !can("4.4", "read");
  }

  async function saveDraft() {
    const { heads, rows } = gridApi.data();
    const result = await call("POST", "/api/drafts/save", { id, name, template_id: templateId, heads, rows });
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not save the draft."));
    const draft = result.data.draft;
    id = draft.id;
    name = draft.name;
    filled = draft;
    history.replaceState(null, "", `/draft.html?id=${id}`);
    gridApi.load(draft.heads, draft.rows, draft.base);
    gridApi.markSaved();
    status.replaceChildren(notice("success", "Saved."));
    refresh();
  }

  async function leave() {
    if (gridApi.isDirty() || id === null) {
      const yes = await confirmDialog({ title: "Discard unsaved changes?", text: "You have changes that are not saved. Leaving now goes back to the last saved version.", yes: "Discard", danger: true });
      if (!yes) return;
    }
    window.onbeforeunload = null;
    location.href = "/drafts.html";
  }

  window.weldLeave = leave;                                      // the page's Back button asks first when there are unsaved changes
  window.onbeforeunload = (event) => {
    if (gridApi.isDirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  };

  // load: an existing draft, or the template a new one starts from
  if (id !== null) {
    const result = await call("GET", `/api/drafts/${id}`);
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not open the draft."));
    const d = result.data.draft;
    name = d.name;
    templateName = d.template;
    templateId = d.template_id;
    filled = d;
    if (!d.can_edit) editable = false;                                 // only the person who made a draft, or an administrator, changes it
    gridApi.load(d.heads, d.rows, editable ? d.base : d.rows);
    if (!editable) {
      gridApi.setReadOnly(true);
      status.replaceChildren(notice("info", "This draft was made by " + (d.by || "someone else") + ". Only they or an administrator can change it. You can read it and download it."));
    }
  } else {
    const result = await call("GET", `/api/templates/${templateId}`);
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not open the template."));
    const t = result.data.template;
    templateName = t.name;
    gridApi.load(t.heads, t.rows, t.rows);
  }
  refresh();
});
