// The Library: every document, searched by name. A click opens it in the viewer. People whose permission allows it also get "Add documents" (a file with the name of an
// existing one replaces it) and "Delete" (tick documents, then Delete (n), then confirm; deleted documents wait in a trash folder, they are not erased).
// An upload or a delete only saves: the documents are used in answers after someone presses Reindex (here or in Model management), which also warns that the question pairs
// written by hand are deleted. Documents not indexed yet are listed with a note. Nothing here trains the model.

import { $, el, icon, niceTitle, pageHeading } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { openViewer } from "../chat/viewer.js";
import { startPage } from "../components/navbar.js";
import { confirmDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";
import { watchNow } from "../components/status-bar.js";
import { reindexButton, documentState, waiting } from "../components/reindex.js";

const WATCH_MS = 2000;
const chapter = (source) => {
  const n = source.match(/^\d+/);
  return n ? "Ch. " + Number(n[0]) : "";
};

startPage({ active: "library", screen: "2" }).then(() => {
  const page = $("#pageBody");
  const canAdd = can("2.2", "write");
  const canDelete = can("2.3", "write");
  let documents = [];
  let waitingState = null;                                  // what the server says is new, changed or removed since the last indexing
  let deleting = false;
  const ticked = new Set();

  const title = pageHeading("Library", "Every document the answers come from. Open one to read it. Add, replace or delete documents, then press Reindex.");
  const count = el("span", "library__count");
  const head = el("div", "library__head");
  head.append(count);

  const search = el("input");
  search.type = "search";
  search.placeholder = "Search documents by name";
  search.setAttribute("aria-label", "Search documents by name");
  const searchBox = el("div", "library__search");
  searchBox.append(icon("search"), search);
  const tools = el("div", "library__tools");
  tools.appendChild(searchBox);

  const file = el("input");
  file.type = "file";
  file.accept = ".docx";
  file.multiple = true;
  file.hidden = true;
  const add = el("button", "btn primary");
  add.type = "button";
  add.append(icon("plus"), document.createTextNode("Add documents"));
  add.title = "A file with the same name replaces the old one";
  const remove = el("button", "btn");
  remove.type = "button";
  const cancel = el("button", "btn ghost", "Cancel");
  cancel.type = "button";
  cancel.hidden = true;
  const reindexer = reindexButton({ onStarted: () => { say("info", "Reindexing has started. The list updates when it has finished."); afterChange(true); }, onProblem: (why) => say("error", why) });
  if (canAdd) tools.append(add, file);
  if (canDelete) tools.append(remove, cancel);
  tools.appendChild(reindexer.node);

  const status = el("div", "library__status");
  const list = el("div", "library__list");
  page.append(title, head, tools, status, list);

  const say = (kind, text) => status.replaceChildren(text ? notice(kind, text) : "");

  async function load() {
    try {
      documents = await (await fetch("/api/library")).json();
    } catch (error) {
      documents = null;
    }
    waitingState = await documentState();
    reindexer.paint(waitingState);
    draw();
  }

  function paintTools() {
    remove.replaceChildren(icon("trash"), document.createTextNode(deleting ? `Delete (${ticked.size})` : "Delete"));
    remove.classList.toggle("danger", deleting);
    remove.disabled = deleting && !ticked.size;
    cancel.hidden = !deleting;
  }

  function draw() {
    const text = search.value.trim().toLowerCase();
    count.textContent = documents ? `${documents.length} indexed${waiting(waitingState) ? `, ${waiting(waitingState)} waiting` : ""}` : "";
    list.replaceChildren();
    if (!documents) return list.appendChild(el("div", "library__empty", "The library could not be loaded."));
    const mark = new Map();                                 // name -> why it is not in the index yet
    if (waitingState) {
      (waitingState.new || []).forEach((n) => mark.set(n, "Not indexed yet"));
      (waitingState.changed || []).forEach((n) => mark.set(n, "Changed, not indexed yet"));
      (waitingState.removed || []).forEach((n) => mark.set(n, "Deleted, still indexed"));
    }
    const known = new Set(documents.map((d) => d.source));
    const all = [...documents, ...(waitingState ? waitingState.new || [] : []).filter((n) => !known.has(n)).map((n) => ({ source: n, unindexed: true }))].sort((a, b) => a.source.localeCompare(b.source));
    const rows = all.filter((d) => !text || niceTitle(d.source).toLowerCase().includes(text));
    if (!rows.length) return list.appendChild(el("div", "library__empty", documents.length ? "No document matches." : "The library is empty."));
    if (deleting) {
      const everyone = el("label", "library__row");
      const master = el("input");
      master.type = "checkbox";
      master.checked = rows.every((d) => ticked.has(d.source));
      master.setAttribute("aria-label", "Select all");
      master.onchange = () => {
        rows.forEach((d) => (master.checked ? ticked.add(d.source) : ticked.delete(d.source)));
        paintTools();
        draw();
      };
      everyone.append(master, el("span", "library__name", text ? `Select all ${rows.length} shown` : `Select all (${rows.length})`));
      list.appendChild(everyone);
    }
    rows.forEach((doc) => {
      const row = el(deleting ? "label" : "button", "library__row");
      if (!deleting) row.type = "button";
      if (deleting) {
        const tick = el("input");
        tick.type = "checkbox";
        tick.checked = ticked.has(doc.source);
        tick.setAttribute("aria-label", "Select " + niceTitle(doc.source));
        tick.onchange = () => {
          if (tick.checked) ticked.add(doc.source);
          else ticked.delete(doc.source);
          paintTools();
          draw();
        };
        row.appendChild(tick);
      } else {
        row.appendChild(icon("doc"));
      }
      row.append(el("span", "library__name", niceTitle(doc.source)));
      if (mark.has(doc.source)) row.appendChild(el("span", "pill warn", mark.get(doc.source)));
      row.appendChild(el("span", "library__chapter", chapter(doc.source)));
      row.title = doc.source;
      if (!deleting && !doc.unindexed) row.onclick = () => openViewer({ source: doc.source, chunk_id: 0, heading: "" }, []);
      list.appendChild(row);
    });
  }

  function stopDeleting() {
    deleting = false;
    ticked.clear();
    say("", "");
    paintTools();
    draw();
  }

  remove.onclick = async () => {
    if (!deleting) {
      deleting = true;
      say("info", "Tick the documents to delete. Deleted documents are moved to a trash folder, not erased.");
      paintTools();
      return draw();
    }
    const n = ticked.size;
    const yes = await confirmDialog({
      title: "Delete documents",
      text: `Delete ${n} document${n === 1 ? "" : "s"}? ${n === 1 ? "Its" : "Their"} text stops being searchable. A model that was already trained on ${n === 1 ? "it" : "them"} cannot forget what it learned.`,
      yes: "Delete",
      danger: true,
    });
    if (!yes) return;
    const result = await call("POST", "/api/admin/documents/delete", { names: [...ticked] });
    stopDeleting();
    if (!result.ok) return say("error", result.data.error || "The documents could not be deleted.");
    say("success", `Deleted ${result.data.deleted.length}. Press Reindex so the answers stop using ${result.data.deleted.length === 1 ? "it" : "them"}.`);
    afterChange();
  };
  cancel.onclick = stopDeleting;

  add.onclick = () => file.click();
  file.onchange = async () => {
    if (!file.files.length) return;
    const data = new FormData();
    [...file.files].forEach((f) => data.append("files", f, f.name));
    file.value = "";
    say("info", "Uploading...");
    let result;
    try {
      const response = await fetch("/api/admin/documents/upload", { method: "POST", body: data });
      result = { ok: response.ok, data: await response.json().catch(() => ({})) };
    } catch (error) {
      result = { ok: false, data: { error: "Server not reachable." } };
    }
    const saved = result.data.saved || [];
    const rejected = result.data.rejected || [];
    const replaced = saved.filter((s) => s.replaced).length;
    const lines = [];
    if (saved.length) {
      lines.push(notice("success", `Added ${saved.length - replaced}${replaced ? `, replaced ${replaced}` : ""}. Press Reindex to use ${saved.length === 1 ? "it" : "them"} in answers.`));
    }
    rejected.forEach((r) => lines.push(notice("warn", `${r.name}: ${r.reason}`)));
    if (!saved.length && !rejected.length) lines.push(notice("error", result.data.error || "Nothing was added."));
    status.replaceChildren(...lines);
    if (saved.length) afterChange();
  };

  // Saving or deleting changes only what is waiting; a reindex changes the list when it has finished, so that one is followed until it ends.
  function afterChange(follow = false) {
    watchNow();
    window.dispatchEvent(new Event("weld:refresh"));
    load();
    if (!follow) return;
    let seen = false;
    let polls = 0;
    const wait = setInterval(async () => {
      const state = await call("GET", "/api/admin/documents");
      polls += 1;
      if (state.ok && state.data.job.state === "running") seen = true;
      if (state.ok && state.data.job.state !== "running" && (seen || polls > 5)) {
        clearInterval(wait);
        say("success", "The documents are indexed.");
        await load();
        window.dispatchEvent(new Event("weld:refresh"));
      }
    }, WATCH_MS);
  }

  search.oninput = draw;
  paintTools();
  load();
});
