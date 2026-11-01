// My submissions: the questions and answers a person has sent for approval, with what happened to each. A new one is Waiting. An administrator approves it, rejects it with a reason, or
// asks for changes; then the person corrects it here and sends it again.

import { $, el, icon, iconButton, pageHeading } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { startPage } from "../components/navbar.js";
import { openDialog, confirmDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";
import { mountPager } from "../components/pager.js";
import { openPairDialog } from "../admin/pair-dialog.js";
import { pairTip, sourceOf } from "../admin/model-pairs.js";

const PILL = { approved: ["pill", "Approved"], pending: ["pill warn", "Waiting"], rejected: ["pill bad", "Rejected"], changes: ["pill info", "Changes requested"] };
const day = (seconds) => new Date(seconds * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });

startPage({ active: "submissions", screen: "13" }).then(() => {
  document.title = "My submissions - Weld Assist";
  const page = $("#pageBody");
  let areas = ["Chat"];
  const messages = el("div", "model__status");
  const say = (kind, text) => messages.replaceChildren(text ? notice(kind, text, { closable: true }) : "");

  const head = el("div", "library__head");
  const add = iconButton("plus", "Send a new question and answer for approval");
  const remove = iconButton("minus", "Delete the ticked questions", () => deleteTicked(), "danger");
  const ticked = new Set();                                      // ids of the ticked rows, kept while the page is turned
  let shown = [];
  const title = pageHeading("My submissions", "Question and answer pairs you send, from your own words, the documents or a draft. Each waits until an administrator approves it. You see here what happened to it.");
  if (can("13.1", "write")) head.append(add, remove);   // the + then the -
  const list = el("div");
  const pagerBox = el("div");
  page.append(title, head, messages, list, pagerBox);

  // The page and the rows per page are in the address, so /submissions?page=2&size=50 opens that page and a reload keeps it.
  const wanted = new URLSearchParams(location.search);
  const size = [10, 25, 50, 100].includes(Number(wanted.get("size"))) ? Number(wanted.get("size")) : 25;
  let offset = 0;
  const pager = mountPager(pagerBox, { sizes: [10, 25, 50, 100], size, page: Number(wanted.get("page")) || 1, onChange: ({ offset: o }) => ((offset = o), load()) });
  offset = (pager.page() - 1) * size;

  async function load() {
    const result = await call("GET", `/api/my/pairs?offset=${offset}&limit=${pager.limit()}`);
    if (!result.ok) return list.replaceChildren(notice("error", result.data.error || "Your submissions could not be loaded."));
    areas = result.data.areas;
    const before = pager.page();
    pager.setTotal(result.data.total);
    if (pager.page() !== before) {                                // the address named a page past the end: go to the last one
      offset = (pager.page() - 1) * pager.limit();
      return load();
    }
    const next = new URLSearchParams();
    if (pager.page() > 1) next.set("page", String(pager.page()));
    if (pager.limit() !== 25) next.set("size", String(pager.limit()));
    history.replaceState(null, "", location.pathname + (next.toString() ? "?" + next : ""));
    draw(result.data.pairs);
  }

  // What a pair is made from, read-only: the documents it quotes or the draft cell it came from.
  function references(pair) {
    const refs = pair.refs ? JSON.parse(pair.refs) : [];
    const box = el("div", "fe-form");
    if (refs.length) refs.forEach((x) => box.appendChild(el("p", "hint", `${x.doc.replace(/\.docx$/, "").replace(/_/g, " ")}: ${x.text}`)));
    else box.appendChild(el("p", "hint", `Your own passage: ${pair.section || ""}${pair.evidence ? ". " + pair.evidence : ""}`));
    return box;
  }

  function resend(pair) {
    const form = el("div", "fe-form");
    const question = el("input");
    question.value = pair.question;
    question.maxLength = 300;
    const answer = el("textarea");
    answer.rows = 3;
    answer.value = pair.answer;
    answer.disabled = Boolean(pair.refs || pair.draft);            // an answer picked from a document or a draft cell stays as picked
    const problem = el("div");
    const field = (label, node) => {
      const wrap = el("label", "field");
      wrap.append(el("span", null, label), node);
      return wrap;
    };
    form.append(field("Question", question), field("Answer", answer), ...(pair.draft ? [] : [el("b", null, "References"), references(pair)]), problem);
    openDialog({
      title: "Edit and send again",
      body: form,
      actions: [
        { label: "Cancel" },
        {
          label: "Save and send",
          primary: true,
          onClick: async (close) => {
            const result = await call("POST", `/api/my/pairs/${pair.id}/edit`, { question: question.value, answer: answer.value });
            if (!result.ok) return problem.replaceChildren(notice("error", result.data.error || "That did not work.", { closable: true }));
            close();
            say("success", "Saved. It is Waiting for approval.");
            load();
          },
        },
      ],
    });
  }

  // The header box ticks every row that can be ticked; an approved pair is not tickable (an administrator removes it).
  const deletable = (r) => r.status !== "approved";

  function paintTicks() {
    const rows = shown.filter(deletable);
    const n = rows.filter((r) => ticked.has(r.id)).length;
    const all = list.querySelector("input.tick[data-all]");
    if (all) {
      all.checked = rows.length > 0 && n === rows.length;
      all.indeterminate = n > 0 && n < rows.length;
      all.disabled = !rows.length;
    }
    remove.disabled = !ticked.size;
    remove.style.opacity = ticked.size ? "1" : "0.4";
  }

  async function deleteTicked() {
    const n = ticked.size;
    if (!n) return;
    const yes = await confirmDialog({ title: "Delete questions", text: `Delete ${n} question${n === 1 ? "" : "s"}? This cannot be undone.`, yes: "Delete", danger: true });
    if (!yes) return;
    const result = await call("POST", "/api/my/pairs/delete", { ids: [...ticked] });
    if (!result.ok) return say("error", result.data.error || "Nothing was deleted.");
    ticked.clear();
    say("success", `Deleted ${result.data.deleted}.${result.data.left ? ` ${result.data.left} could not be deleted.` : ""}`);
    load();
  }

  async function deleteOne(r) {
    if (!(await confirmDialog({ title: "Delete question", text: "Delete this question? This cannot be undone.", yes: "Delete", danger: true }))) return;
    const result = await call("POST", "/api/my/pairs/delete", { ids: [r.id] });
    if (!result.ok || !result.data.deleted) return say("error", (result.data && result.data.error) || "It could not be deleted.");
    ticked.delete(r.id);
    say("success", "Deleted.");
    load();
  }

  function draw(rows) {
    shown = rows;
    const table = el("table", "table");
    const header = el("tr");
    const allBox = el("input", "tick");
    allBox.type = "checkbox";
    allBox.dataset.all = "1";
    allBox.setAttribute("aria-label", "Select all");
    allBox.title = "Select all";
    allBox.onchange = () => {
      rows.filter(deletable).forEach((r) => (allBox.checked ? ticked.add(r.id) : ticked.delete(r.id)));
      body.querySelectorAll("input.tick:not(:disabled)").forEach((box) => (box.checked = allBox.checked));
      paintTicks();
    };
    const allCell = el("th", "tickcol");
    allCell.appendChild(allBox);
    header.appendChild(allCell);
    ["Question", "Source", "Status", "Comment", "Sent"].forEach((label) => header.appendChild(el("th", null, label)));
    header.appendChild(el("th", "right", "Actions"));
    table.appendChild(el("thead")).appendChild(header);
    const body = el("tbody");
    table.appendChild(body);
    if (!rows.length) {
      const cell = el("td", "hint", "You have not sent anything yet.");
      cell.colSpan = 7;
      body.appendChild(el("tr")).appendChild(cell);
    }
    rows.forEach((r) => {
      const question = el("td", null, r.question);
      question.title = pairTip(r);
      const state = el("td");
      state.appendChild(el("span", ...PILL[r.status]));
      const asked = r.status === "rejected" || r.status === "changes";             // the administrator's reason, in a column of its own
      const comment = el("td", null, asked && r.note ? r.note : "");
      if (asked && !r.note) comment.appendChild(el("span", "hint", "No comment was left."));
      if (asked && r.note) comment.title = r.note;
      const acts = el("div", "acts");
      if (deletable(r)) {                                         // an approved pair is changed and removed by an administrator
        acts.appendChild(iconButton("edit", "Edit the question and answer, and see its references", () => resend(r)));
        acts.appendChild(iconButton("trash", "Delete this question", () => deleteOne(r), "danger"));
      }
      const actions = el("td");
      actions.appendChild(acts);
      const tickCell = el("td", "tickcol");
      const box = el("input", "tick");
      box.type = "checkbox";
      box.disabled = !deletable(r);
      box.checked = ticked.has(r.id) && deletable(r);
      box.setAttribute("aria-label", "Select this question");
      if (box.disabled) box.title = "An approved question is removed by an administrator";
      box.onchange = () => {
        if (box.checked) ticked.add(r.id);
        else ticked.delete(r.id);
        paintTicks();
      };
      tickCell.appendChild(box);
      const row = el("tr");
      row.append(tickCell, question, el("td", null, sourceOf(r)), state, comment, el("td", null, day(r.created)), actions);
      body.appendChild(row);
    });
    const wrap = el("div", "table-wrap");
    wrap.appendChild(table);
    list.replaceChildren(wrap);
    paintTicks();
  }

  add.onclick = () => openPairDialog({
    areas,
    drafts: true,
    saveLabel: "Send",
    title: "Send a question and answer",
    send: (body) => call("POST", "/api/my/pairs", body),
    onDone: () => {
      say("success", "Sent. It is Waiting for approval.");
      load();
    },
  });
  load();
});
