// The Drafts list: every draft (everyone sees all of them), newest first, with search and the pager. + makes a new draft; each row has a pencil (edit), a download
// and, only for people whose permission allows it, a trash. Nothing is shown that the person may not use.

import { $, el, icon, iconButton, pageHeading } from "../core/dom.js";
import { call } from "../core/api.js";
import { when } from "../core/format.js";
import { can } from "../core/permissions.js";
import { startPage } from "../components/navbar.js";
import { confirmDialog } from "../components/dialog.js";
import { mountPager } from "../components/pager.js";
import { notice } from "../components/notice.js";
import { newDraftDialog } from "../drafts/new-draft-dialog.js";

startPage({ active: "drafts", screen: "4" }).then(() => {
  const page = $("#pageBody");
  const head = el("div", "work__head page-heading");
  const heading = pageHeading("Drafts", "Forms filled in from templates. Start a new draft, edit one, or download it as Excel.");
  heading.className = "";
  heading.style.flex = "1";
  heading.querySelector("h1").className = "work__title";
  head.appendChild(heading);
  if (can("4.1", "write")) head.appendChild(Object.assign(iconButton("plus", "New draft", newDraftDialog, "primary"), {}));

  const search = el("input");
  search.type = "search";
  search.placeholder = "Search drafts";
  search.setAttribute("aria-label", "Search drafts");
  const searchBox = el("div", "work__search");
  searchBox.append(icon("search"), search);
  const status = el("div", "work__status");
  const tableBox = el("div", "table-wrap");
  const pagerBox = el("div");
  page.append(head, searchBox, status, tableBox, pagerBox);

  let offset = 0;
  const pager = mountPager(pagerBox, { onChange: ({ offset: o }) => ((offset = o), load()) });

  async function load() {
    const q = new URLSearchParams({ offset: String(offset), limit: String(pager.limit()), q: search.value.trim() });
    const result = await call("GET", "/api/drafts?" + q);
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not load the drafts."));
    status.replaceChildren();
    pager.setTotal(result.data.total);
    draw(result.data.drafts);
  }

  async function remove(draft) {
    const yes = await confirmDialog({ title: `Delete ${draft.name}?`, text: "The draft is deleted for everyone.", yes: "Delete", danger: true });
    if (!yes) return;
    const done = await call("POST", `/api/drafts/${draft.id}/delete`, {});
    if (!done.ok) return status.replaceChildren(notice("error", done.data.error || "Could not delete the draft."));
    load();
  }

  function draw(drafts) {
    const table = el("table", "table");
    const header = el("tr");
    ["Draft", "Template", "By", "Updated", "Progress"].forEach((label) => header.appendChild(el("th", null, label)));
    header.appendChild(el("th", "right", "Actions"));
    table.appendChild(el("thead")).appendChild(header);
    const body = el("tbody");
    table.appendChild(body);
    if (!drafts.length) {
      const empty = el("tr");
      const cell = el("td", "hint", search.value.trim() ? "No draft matches." : "No drafts yet.");
      cell.colSpan = 6;
      empty.appendChild(cell);
      body.appendChild(empty);
    }
    drafts.forEach((d) => {
      const tr = el("tr");
      const acts = el("div", "acts");
      if (can("4.2", "write") && d.can_edit) {
        const edit = iconButton("edit", "Edit", () => (location.href = `/draft.html?id=${d.id}`));
        acts.appendChild(edit);
      }
      if (can("4.4", "read")) {
        const dl = el("a", "ibtn");
        dl.href = `/api/drafts/${d.id}/download.xlsx`;
        dl.title = "Download as Excel";
        dl.setAttribute("aria-label", "Download as Excel");
        dl.appendChild(icon("download"));
        acts.appendChild(dl);
      }
      if (can("4.5", "write")) acts.appendChild(iconButton("trash", "Delete", () => remove(d), "danger"));
      const actions = el("td");
      actions.appendChild(acts);
      const progress = d.total ? (d.complete ? "Complete" : `${d.filled} of ${d.total}`) : "-";
      tr.append(el("td", "drafts__name", d.name), el("td", null, d.template || "-"), el("td", null, d.by || "-"), el("td", null, when(d.updated)), el("td", "drafts__progress", progress), actions);
      body.appendChild(tr);
    });
    tableBox.replaceChildren(table);
  }

  let timer = 0;
  search.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      offset = 0;
      pager.reset();
      load();
    }, 250);
  };
  load();
});
