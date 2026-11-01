// Model management, Question pairs tab: the questions and answers the model learns from. Approved pairs are the learned pairs (kept in one file beside the base training set).
// A pair is Waiting until an administrator approves it, rejects it with a reason, or asks the person who sent it to change it ("Changes requested"). The table has a tick column:
// the ticked pairs can be saved as Excel, deleted, or trained on. Nothing here writes into a document.

import { el, icon, iconButton } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { openDialog, confirmDialog } from "../components/dialog.js";
import { mountPager } from "../components/pager.js";
import { notice } from "../components/notice.js";
import { openPairDialog } from "./pair-dialog.js";

const STATUSES = [["", "Any status"], ["pending", "Waiting"], ["approved", "Approved"], ["rejected", "Rejected"], ["changes", "Changes requested"]];
// Not found: the document is gone, it was changed, or the sentence is no longer in it. The server tells them apart; people see one tag.
const notFound = (r) => r.in_document === "changed" || r.in_document === "missing";
const STATUS_PILL = { approved: ["pill", "Approved"], pending: ["pill warn", "Waiting"], rejected: ["pill bad", "Rejected"], changes: ["pill info", "Changes requested"] };

// What a pair's Source column says: the document a generated pair came from, or how a person's pair was made.
// The draft cell a pair was made from, as {name, template, field, column}, or null.
export const draftOf = (r) => {
  try {
    return r.draft ? JSON.parse(r.draft) : null;
  } catch {
    return null;
  }
};

export function sourceOf(r) {
  const draft = draftOf(r);
  if (draft) return `Draft: ${draft.name}`;
  if (r.kind !== "manual" && r.kind !== "suggested") return r.doc.replace(/\.docx$/, "");
  return r.refs || r.doc.endsWith(".docx") ? "Manually referenced from document" : "Manually added";
}

// The tooltip of a pair: the whole question and answer, and the documents it refers to.
export function pairTip(r) {
  const refs = r.refs ? JSON.parse(r.refs) : [];
  const where = refs.length ? refs.map((x) => x.doc.replace(/\.docx$/, "")).join(", ") : r.doc.replace(/\.docx$/, "");
  const draft = draftOf(r);
  const from = draft ? `\nFrom the draft "${draft.name}"${draft.template ? ` (template ${draft.template})` : ""}, field "${draft.field}"${draft.column ? `, column "${draft.column}"` : ""}` : "";
  return `Q: ${r.question}\nA: ${r.answer}\n${where}${from}${r.note ? "\nNote: " + r.note : ""}`;
}

function selectOf(label, options) {
  const node = el("select");
  node.setAttribute("aria-label", label);
  options.forEach(([value, text]) => {
    const option = el("option", null, text);
    option.value = value;
    node.appendChild(option);
  });
  return node;
}

// A small window that asks for a note (why a pair is rejected, or what to change). -> the text, or null when cancelled.
function askNote({ title, label, required }) {
  return new Promise((resolve) => {
    const note = el("textarea");
    note.rows = 3;
    note.maxLength = 400;
    const wrap = el("label", "field");
    wrap.append(el("span", null, label), note);
    const problem = el("div");
    let done = false;
    const form = el("div", "fe-form");
    form.append(wrap, problem);
    const dialog = openDialog({
      title,
      body: form,
      onClose: () => !done && resolve(null),
      actions: [
        { label: "Cancel" },
        {
          label: "Send",
          primary: true,
          onClick: (close) => {
            if (required && !note.value.trim()) return problem.replaceChildren(notice("warn", "Write a short note first."));
            done = true;
            close();
            resolve(note.value.trim());
          },
        },
      ],
    });
    return dialog;
  });
}

export async function pairsTab(panel) {
  const status = el("div", "model__status");              // the one place every message of this page appears; each can be closed
  const messages = el("div", "model__status");
  status.appendChild(messages);
  const say = (kind, text) => messages.replaceChildren(text ? notice(kind, text, { closable: true }) : "");
  let areas = ["Chat", "Fault Finder", "Procedure Advisor"];
  const cache = new Map();                                          // id -> the row last shown, so ticked rows on other pages are still known
  const ticked = new Set();                                         // ids of the ticked rows; kept while the page is turned
  let shown = [];

  const head = el("div", "model__cardhead");
  const title = el("div");
  const heading = el("h2", null, "Question pairs");
  title.appendChild(heading);
  const add = iconButton("plus", "Add a pair");
  const removeSelected = iconButton("minus", "Delete the ticked pairs", () => deleteTicked(), "danger");
  const beside = el("div", "acts");                                // the + then the - next to the heading
  if (can("7.2", "write")) beside.appendChild(add);
  if (can("7.1", "write")) beside.appendChild(removeSelected);
  title.style.cssText = "display:flex;align-items:center;gap:12px";
  title.appendChild(beside);
  const tools = el("div", "row-line");
  const excel = el("button", "btn");
  excel.type = "button";
  excel.append(icon("download"), document.createTextNode("Save as Excel"));
  const trial = el("button", "btn primary");
  trial.type = "button";
  trial.append(icon("play"), document.createTextNode("Test selected pairs"));
  if (can("7.2", "write")) tools.appendChild(excel);
  if (can("7.3", "write")) tools.appendChild(trial);
  head.append(title, tools);

  const search = el("input");
  search.type = "search";
  search.placeholder = "Search questions and answers";
  search.setAttribute("aria-label", "Search questions and answers");
  const statusFilter = selectOf("Status", STATUSES);
  const filters = el("div", "model__filters");
  filters.append(search, statusFilter);
  const tableBox = el("div");
  const pagerBox = el("div");
  const section = el("section", "section");

  // The two approval settings are kept as the server holds them; they have no buttons on this page.
  const auto = el("input");
  auto.type = "checkbox";
  const strict = el("input");
  strict.type = "checkbox";
  const test = iconButton("play", "Test the answers: checks every question and answer pair against the documents, then asks a sample of the questions. Nothing is changed.");
  section.append(head, filters, tableBox, pagerBox);

  function actionsHeader() {
    const th = el("th", "right");
    const row = el("div", "acts");
    if (can("7.3", "write")) row.append(test);
    th.append("Actions", row);
    return th;
  }

  panel.append(el("p", "tab-desc", "The questions and answers people add. An approved pair answers at once, with no training. The model is never trained on these."), status, section);

  let offset = 0;
  const pager = mountPager(pagerBox, { onChange: ({ offset: o }) => ((offset = o), load()) });

  async function loadSummary() {
    const result = await call("GET", "/api/admin/qa/summary");
    if (!result.ok) return say("error", result.data.error || "The summary could not be loaded.");
    const c = result.data;
    auto.checked = c.auto_approve;
    strict.checked = c.only_found;
  }

  async function loadAreas() {
    const result = await call("GET", "/api/admin/qa/hand");
    if (result.ok) areas = result.data.areas;
  }

  async function load() {
    const query = new URLSearchParams({ status: statusFilter.value, origin: "hand", q: search.value.trim(), offset: String(offset), limit: String(pager.limit()) });
    const result = await call("GET", "/api/admin/qa?" + query);
    if (!result.ok) return tableBox.replaceChildren(notice("error", result.data.error || "The pairs could not be loaded.", { closable: true }));
    pager.setTotal(result.data.total);
    draw(result.data.rows);
  }

  async function decide(body, done) {
    const result = await call("POST", "/api/admin/qa/decide", body);
    if (!result.ok) return say("error", result.data.error || "That did not work.");
    say("success", done(result.data));
    window.dispatchEvent(new Event("weld:refresh"));
    await loadSummary();
    load();
  }

  const decideWithNote = async (r, statusName, options, done) => {
    const note = await askNote(options);
    if (note === null) return;
    decide({ ids: [r.id], status: statusName, note }, () => done);
  };

  const byPerson = (r) => r.kind === "manual" || r.kind === "suggested";

  // ---- the ticked rows
  const tickedRows = () => [...ticked].map((id) => cache.get(id)).filter(Boolean);
  const tickedApproved = () => tickedRows().filter((r) => r.status === "approved");

  function paintTicks() {
    const all = tableBox.querySelector("input.tick[data-all]");
    const n = shown.filter((r) => ticked.has(r.id)).length;
    if (all) {
      all.checked = shown.length > 0 && n === shown.length;
      all.indeterminate = n > 0 && n < shown.length;
    }
    removeSelected.disabled = !ticked.size;
    removeSelected.style.opacity = ticked.size ? "1" : "0.4";
    const ready = tickedApproved().length > 0;                       // Train works on approved pairs only
    trial.disabled = !ready;
    trial.title = ready ? "" : "Tick one or more approved pairs first";
    excel.title = ticked.size ? `Download the ${ticked.size} ticked pair${ticked.size === 1 ? "" : "s"} as Excel` : "Nothing is ticked, so this downloads every pair in the list as Excel";
  }

  // Delete every ticked pair added by a person. A generated pair cannot be deleted (it is training data made from a document): reject it instead.
  async function deleteTicked() {
    const mine = tickedRows();
    const people = mine.filter(byPerson);
    const skipped = mine.length - people.length;
    if (!people.length) return say("warn", "Generated pairs cannot be deleted. Reject them with the cross instead.");
    const yes = await confirmDialog({ title: "Delete pairs", text: `Delete ${people.length} pair${people.length === 1 ? "" : "s"}? ${skipped ? `${skipped} generated pair${skipped === 1 ? " is" : "s are"} not deleted.` : ""}`, yes: "Delete", danger: true });
    if (!yes) return;
    let failed = 0;
    for (const r of people) {
      const done = await call("POST", `/api/admin/qa/hand/${r.id}/delete`);
      if (done.ok) ticked.delete(r.id);
      else failed += 1;
    }
    say(failed ? "error" : "success", failed ? `${failed} could not be deleted.` : `Deleted ${people.length}.`);
    await loadSummary();
    load();
  }

  excel.onclick = () => {
    const link = el("a");
    const filter = new URLSearchParams({ status: statusFilter.value, origin: "hand", q: search.value.trim() });          // nothing ticked: the list as it is shown
    link.href = "/api/admin/qa/export.xlsx?" + (ticked.size ? "ids=" + [...ticked].join(",") : filter);
    link.download = "question-pairs.xlsx";
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  // Ask each ticked pair's question the way a person would and see whether the reply holds the approved answer. An approved pair is used at once (no training), so a failure
  // here means the model words it badly; only then is a retrain offered, and only on the pairs that failed.
  trial.onclick = async () => {
    const rows = tickedApproved();
    trial.disabled = true;
    say("info", `Asking ${rows.length} question${rows.length === 1 ? "" : "s"}. This takes a little while...`);
    const result = await call("POST", "/api/admin/qa/test", { ids: rows.map((r) => r.id) });
    trial.disabled = false;
    if (!result.ok) return say("error", result.data.error || "The test did not run.");
    const t = result.data;
    say("success", t.differ.length ? `Test done. ${t.matched} of ${t.tested} answered correctly.` : `Test done. All ${t.tested} answered correctly.`);
  };

  // ---- the table
  function draw(rows) {
    shown = rows;
    rows.forEach((r) => cache.set(r.id, r));
    const table = el("table", "table");
    const header = el("tr");
    const allBox = el("input", "tick");
    allBox.type = "checkbox";
    allBox.dataset.all = "1";
    allBox.setAttribute("aria-label", "Select all");
    allBox.title = "Select all";
    allBox.onchange = () => {
      rows.forEach((r) => (allBox.checked ? ticked.add(r.id) : ticked.delete(r.id)));
      body.querySelectorAll("input.tick").forEach((box) => (box.checked = allBox.checked));
      paintTicks();
    };
    const allCell = el("th", "tickcol");
    allCell.appendChild(allBox);
    header.appendChild(allCell);
    ["Question", "Source", "Status", "Comment", "Added by"].forEach((label) => header.appendChild(el("th", null, label)));
    header.appendChild(actionsHeader());
    table.appendChild(el("thead")).appendChild(header);
    const body = el("tbody");
    table.appendChild(body);
    if (!rows.length) {
      const cell = el("td", "hint", "No pair matches.");
      cell.colSpan = 7;
      body.appendChild(el("tr")).appendChild(cell);
    }
    rows.forEach((r) => {
      const question = el("td", null, r.question);
      question.title = pairTip(r);
      const sourceCell = el("td", null, sourceOf(r));
      const statusCell = el("td");
      statusCell.appendChild(el("span", ...STATUS_PILL[r.status]));
      const asked = r.status === "rejected" || r.status === "changes";             // the reason given, in a column of its own
      const comment = el("td", null, asked && r.note ? r.note : "");
      if (asked && !r.note) comment.appendChild(el("span", "hint", "No comment was left."));
      if (asked && r.note) comment.title = r.note;
      if (notFound(r)) statusCell.append(" ", el("span", "pill warn", "Not found"));
      const acts = el("div", "acts");
      if (can("7.1", "write")) {
        if (!byPerson(r)) acts.appendChild(iconButton("edit", "Edit this pair", () => editDialog(r)));
        if (r.status !== "approved") {
          const blocked = notFound(r);
          const approve = iconButton("check", blocked ? "Not found in the document, so it cannot be approved" : "Approve", () => decide({ ids: [r.id], status: "approved" }, () => "Approved."));
          approve.disabled = blocked;
          acts.appendChild(approve);
        }
        if (r.status === "pending" && byPerson(r)) {
          acts.appendChild(iconButton("undo", "Ask the person to change it and send it again", () => decideWithNote(r, "changes", { title: "Request changes", label: "Reason for the change", required: true }, "Changes requested.")));
        }
        if (r.status !== "rejected") acts.appendChild(iconButton("close", "Reject", () => decideWithNote(r, "rejected", { title: "Reject this pair", label: "Reason for rejection", required: false }, "Rejected."), "danger"));
      }
      const actions = el("td");
      actions.appendChild(acts);
      const tickCell = el("td", "tickcol");
      const box = el("input", "tick");
      box.type = "checkbox";
      box.checked = ticked.has(r.id);
      box.setAttribute("aria-label", "Select this pair");
      box.onchange = () => {
        if (box.checked) ticked.add(r.id);
        else ticked.delete(r.id);
        paintTicks();
      };
      tickCell.appendChild(box);
      const row = el("tr");
      row.append(tickCell, question, sourceCell, statusCell, comment, el("td", null, r.added_by || (byPerson(r) ? "-" : "System")), actions);
      body.appendChild(row);
    });
    const wrap = el("div", "table-wrap");
    wrap.appendChild(table);
    tableBox.replaceChildren(wrap);
    paintTicks();
  }

  // Changing the wording of a generated pair: its answer must still be in its document
  function editDialog(pair) {
    const field = (text, node) => {
      const wrap = el("label", "field");
      wrap.append(el("span", null, text), node);
      return wrap;
    };
    const question = el("input");
    question.maxLength = 300;
    question.value = pair.question;
    const answer = el("textarea");
    answer.rows = 3;
    answer.value = pair.answer;
    const problem = el("div");
    const form = el("div", "fe-form");
    form.append(field("Question", question), field("Answer", answer), problem);
    openDialog({
      title: "Edit this pair",
      body: form,
      actions: [
        { label: "Cancel" },
        {
          label: "Save",
          primary: true,
          onClick: async (close) => {
            const result = await call("POST", "/api/admin/qa/edit", { id: pair.id, question: question.value, answer: answer.value, area: pair.area });
            if (!result.ok) return problem.replaceChildren(notice("error", result.data.error || "That did not work.", { closable: true }));
            close();
            say("success", "Saved.");
            load();
          },
        },
      ],
    });
  }

  add.onclick = () => openPairDialog({
    areas,
    send: (body) => call("POST", body.refs ? "/api/admin/qa/add" : "/api/admin/qa/hand", body),
    onDone: async () => {
      say("success", "The pair was added.");
      await loadSummary();
      load();
    },
  });

  test.onclick = async () => {
    test.disabled = true;
    say("info", "Checking every pair against the documents...");
    const checked = await call("POST", "/api/admin/qa/check");
    if (!checked.ok) {
      test.disabled = false;
      return say("error", checked.data.error || "The check did not run.");
    }
    const d = checked.data;
    const lost = d.changed + d.missing;
    load();
    say("info", "Asking a sample of the questions. This takes a little while...");
    const result = await call("POST", "/api/admin/qa/test");
    test.disabled = false;
    if (!result.ok) return say("error", result.data.error || "The test did not run.");
    const t = result.data;
    say("success", t.differ.length || lost ? `Test done. ${t.matched} of ${t.tested} answered correctly${lost ? `, ${lost} not found in the documents` : ""}.` : "Test done. Everything passed.");
  };

  strict.onchange = async () => {
    await call("POST", "/api/admin/qa/only-found", { on: strict.checked });
  };
  auto.onchange = async () => {
    await call("POST", "/api/admin/qa/auto", { on: auto.checked });
    loadSummary();
  };
  let timer = 0;
  search.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(reload, 250);
  };
  function reload() {
    offset = 0;
    pager.reset();
    load();
  }
  statusFilter.onchange = reload;

  await loadSummary();
  await loadAreas();
  await load();
}
