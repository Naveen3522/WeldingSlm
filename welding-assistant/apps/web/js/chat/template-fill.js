// Ask fills a template row by row. For each row that has empty cells the chat asks one question: the field name, then the columns left to fill, separated by semicolons
// ("Electrode force: Minimum ; Nominal"). The answer is one line the same way; its parts go to the columns in order, an empty part leaves that cell empty, extra parts are
// ignored with a note. Chips: "Skip this row" and "Leave the rest empty". At the end the filled table is offered as a Word file (the server checks it still matches the template).

import { el, icon, iconButton } from "../core/dom.js";
import { notice } from "../components/notice.js";

let session = null;

export const filling = () => session !== null;

// POST the table, save the Word file the server makes. -> null, or the problem as text.
export async function downloadTable({ name, template_id: templateId = null, heads, rows }) {
  try {
    const response = await fetch("/api/ask/table.docx", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, template_id: templateId, heads, rows }) });
    if (!response.ok) return (await response.json().catch(() => ({}))).error || "The Word file could not be made.";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = (name || "template").replace(/[^A-Za-z0-9._ -]+/g, "") + ".docx";
    link.click();
    URL.revokeObjectURL(link.href);
    return null;
  } catch (error) {
    return "Server not reachable.";
  }
}

// The Word icon for a table, the same icon button as the answer's own Word download. Its problem, if any, is said through `onProblem(text)`.
export function tableIcon(file, onProblem = () => {}) {
  const button = iconButton("download", (file.label || "Word file") + " (download)", null, "plain small");
  button.onclick = async () => {
    button.disabled = true;
    const bad = await downloadTable(file);
    button.disabled = false;
    onProblem(bad);
  };
  return button;
}

function say(host, ...parts) {
  const root = el("div", "bot-msg");
  const body = el("div", "bot-body");
  const card = el("div", "answer-card");
  const head = el("div", "answer-card__head");
  head.append(icon("brand"), el("b", null, "WELD ASSIST"), el("span", "answer-card__wording", "Template"));
  card.append(head, ...parts);
  body.appendChild(card);
  root.appendChild(body);
  host.appendChild(root);
  root.scrollIntoView({ block: "end" });
  return card;
}

const paragraph = (text) => el("p", "answer", text);
const columnsOf = (row) => row.map((cell, i) => (i > 0 && !cell.trim() ? i : -1)).filter((i) => i > 0);

function chips(card, ...items) {
  const box = el("div", "chips");
  items.forEach(([label, onClick]) => {
    const node = el("button", null, label);
    node.type = "button";
    node.onclick = () => {
      box.remove();
      onClick();
    };
    box.appendChild(node);
  });
  card.appendChild(box);
}

function question() {
  const { rows, heads, queue, at, host } = session;
  if (at >= queue.length) return finish();
  const row = rows[queue[at]];
  const columns = columnsOf(row);
  const card = say(host, paragraph(`${row[0]}: ${columns.map((i) => heads[i]).join(" ; ")}`));
  if (columns.length > 1) card.appendChild(el("p", "answer__hint", "Separate the columns with semicolons. An empty part leaves that cell empty."));
  chips(card, ["Skip this row", () => ((session.at += 1), question())], ["Leave the rest empty", finish]);
}

function finish() {
  const { name, id, heads, rows, host, filled } = session;
  session = null;
  const card = say(host, paragraph(filled ? `Done. ${filled} cell${filled === 1 ? "" : "s"} filled.` : "Done. Nothing was filled."));
  const row = el("div", "answer-card__row");
  const problem = el("div");
  row.append(tableIcon({ name, template_id: id, heads, rows, label: "Word file" }, (bad) => problem.replaceChildren(...(bad ? [notice("error", bad)] : []))), el("span", null, "Word file"));
  card.append(row, problem);
}

export function startFill({ template_id: id, name, heads, rows }, host) {
  const copy = rows.map((r) => [...r]);
  session = { id, name, heads, rows: copy, queue: copy.map((r, i) => (columnsOf(r).length ? i : -1)).filter((i) => i >= 0), at: 0, host, filled: 0 };
  question();
}

// A line typed while the questions are on. -> true when it was taken as an answer.
export function answer(text, host) {
  if (!session) return false;
  if (/^\s*(stop|cancel|done|finish)\s*$/i.test(text)) {
    finish();
    return true;
  }
  const { rows, queue, at } = session;
  const row = rows[queue[at]];
  const columns = columnsOf(row);
  const parts = text.split(";").map((p) => p.trim());
  const notes = [];
  columns.forEach((column, i) => {
    if (parts[i]) {
      row[column] = parts[i];
      session.filled += 1;
    }
  });
  if (parts.length > columns.length) notes.push(notice("info", "Only the first " + columns.length + " part" + (columns.length === 1 ? "" : "s") + " were used. The rest were ignored."));
  const done = columns.filter((_c, i) => parts[i]).map((c) => `${session.heads[c]} = ${row[c]}`);
  say(host, paragraph(done.length ? `${row[0]}: ${done.join("; ")}` : `${row[0]}: left empty.`), ...notes);
  session.at += 1;
  question();
  return true;
}

export const stopFilling = () => {
  session = null;
};
