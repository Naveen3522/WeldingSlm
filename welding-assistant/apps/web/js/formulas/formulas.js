// Formulas page: the list, one formula with every variable defined, a calculator, the formula agent's question box,
// and (administrators) the editor. Text is always set as text, never as markup.

import { can } from "../core/permissions.js";
import { $, el, niceTitle, iconButton, icon, pageHeading } from "../core/dom.js";
import { openViewer } from "../chat/viewer.js";
import { openDialog, confirmDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";

const root = $("#formulas");
const STATUS = { documents: "From the documents", admin: "Added manually", suggested: "Suggested, not yet approved" };
const USED_FOR_MAX = 200;

let me = null;
let summaries = [];
let filterText = "";
const ticked = new Set();                                 // ids of the ticked formulas; only formulas that are not from the documents can be ticked

const canEdit = () => can("3.3", "write");              // Editors and administrators add, change and remove formulas; everyone else suggests

export function initFormulas(user) {
  me = user;
}

export function showFormulas(on) {
  if (on) showList();
}

// Opening a formula adds a history step, so Back returns to the list and Back again leaves the page.
let shown = null;                                       // the formula on screen, or null for the list
window.addEventListener("popstate", (event) => {
  const wanted = event.state && event.state.formula ? event.state.formula : null;
  if (wanted === shown) return;                         // only the document viewer opened or closed
  if (wanted) openDetail(wanted, undefined, true);
  else showList();
});
const backToList = () => (history.state && history.state.formula ? history.back() : showList());

async function api(method, url, data) {
  try {
    const response = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: data ? JSON.stringify(data) : undefined });
    return { ok: response.ok, status: response.status, data: await response.json().catch(() => ({})) };
  } catch (error) {
    return { ok: false, status: 0, data: { error: "Server not reachable." } };
  }
}

function btn(label, className, onClick) {
  const node = el("button", className, label);
  node.type = "button";
  node.onclick = onClick;
  return node;
}

// One line in the same tinted notice the whole app uses. kind: "ok", "info", "warn" or "bad"; the parts are text or elements.
const message = (kind, ...parts) => {
  const node = notice({ ok: "success", bad: "error" }[kind] || kind, "");
  node.lastElementChild.append(...parts);
  return node;
};

const lines = (display) => String(display || "").split("; ").join("\n");
const unitText = (unit) => (unit && unit !== "-" ? " " + unit : "");

function badge(formula) {
  const node = el("span", "fm-badge s-" + formula.status, STATUS[formula.status] || formula.status);
  const wrap = el("span", "fm-badges");
  wrap.appendChild(node);
  if (formula.active === false) wrap.appendChild(el("span", "fm-badge s-removed", "Removed"));
  return wrap;
}

// ---------------------------------------------------------------------- the list
async function showList() {
  shown = null;
  window.weldLeave = null;
  root.replaceChildren(el("div", "fm-loading", "Loading..."));
  const result = await api("GET", "/api/formulas");
  if (!result.ok) {
    root.replaceChildren(message("bad", result.data.error || "The formulas could not be loaded."));
    return;
  }
  summaries = result.data;
  drawList();
}

function drawList(note, undo = []) {
  const page = el("div", "fm-page");

  const heading = pageHeading("Formulas", "The formulas found in the documents, each with a calculator. Ask for a calculation, or open a formula and enter your values.");
  const head = el("div", "fm-head");                                        // the heading with the + and the - beside it
  const title = heading.querySelector("h1");
  head.appendChild(title);
  head.appendChild(iconButton("plus", canEdit() ? "Add a formula" : "Suggest a formula", () => openEditor(null), "primary"));
  const remove = iconButton("minus", "Remove the ticked formulas from the list", () => removeTicked(), "danger");
  if (can("3.5", "write")) head.appendChild(remove);                       // the + then the -
  heading.prepend(head);
  page.appendChild(heading);

  // The box that ticks every formula shown (those the filter leaves): half filled when only some are ticked.
  const everyBox = el("input", "tick");
  everyBox.type = "checkbox";
  everyBox.setAttribute("aria-label", "Select all formulas");
  everyBox.title = "Select all";
  let shownIds = [];

  function paintRemove() {
    remove.disabled = !ticked.size;
    remove.style.opacity = ticked.size ? "1" : "0.4";
    const n = shownIds.filter((id) => ticked.has(id)).length;
    everyBox.checked = shownIds.length > 0 && n === shownIds.length;
    everyBox.indeterminate = n > 0 && n < shownIds.length;
    everyBox.disabled = !shownIds.length;
  }
  everyBox.onchange = () => {
    shownIds.forEach((id) => (everyBox.checked ? ticked.add(id) : ticked.delete(id)));
    cards.querySelectorAll(".fm-tick").forEach((box) => (box.checked = everyBox.checked));
    paintRemove();
  };

  async function removeTicked() {
    const ids = [...ticked];
    if (!ids.length) return;
    const yes = await confirmDialog({ title: "Remove formulas", text: `Remove ${ids.length} formula${ids.length === 1 ? "" : "s"} from the list? The documents are not changed, and you can undo it.`, yes: "Remove", danger: true });
    if (!yes) return;
    const gone = [];
    for (const id of ids) {
      const result = await api("POST", `/api/formulas/${id}/delete`, {});
      if (result.ok) {
        ticked.delete(id);
        gone.push(id);
      }
    }
    await showList();
    drawList(gone.length ? `Removed ${gone.length}.` : "Nothing could be removed.", gone);
  }
  if (note) {
    const line = message("ok", note);
    if (undo.length) {                                                   // put the removed formulas back
      const back = btn("Undo", "btn", async () => {
        for (const id of undo) await api("POST", `/api/formulas/${id}/restore`, {});
        await showList();
        drawList(`Put back ${undo.length}.`);
      });
      line.appendChild(back);
    }
    page.appendChild(line);
  }

  const answer = el("div", "fm-answer");
  const ask = el("form", "fm-ask");
  const question = el("input");
  question.placeholder = "Ask for a calculation, e.g. heat input at 28 V, 260 A and 350 mm/min";
  question.setAttribute("aria-label", "Ask for a calculation");
  const go = el("button", "btn primary", "Calculate");
  go.type = "submit";
  ask.append(question, go);
  ask.onsubmit = (event) => {
    event.preventDefault();
    if (question.value.trim()) runAsk(question.value.trim(), answer);
  };
  const filter = el("input", "fm-filter");
  filter.placeholder = "Filter formulas...";
  filter.setAttribute("aria-label", "Filter formulas");
  filter.value = filterText;
  const bar = el("div", "fm-bar");                                          // the question for the calculator and the filter, on one line
  bar.append(ask, filter);
  const selectLine = el("label", "fm-selectall");                           // "Select all" with its tick box, on a line of its own
  selectLine.append(everyBox, document.createTextNode("Select all"));
  page.append(bar, answer);
  if (can("3.5", "write")) page.appendChild(selectLine);

  const cards = el("div");
  page.appendChild(cards);
  root.replaceChildren(page);

  function drawCards() {
    cards.textContent = "";
    const text = filterText.toLowerCase();
    const rows = summaries.filter((f) => (f.active && f.status !== "suggested")
      && (!text || (f.name + " " + f.summary + " " + f.group + " " + f.display).toLowerCase().includes(text)));
    shownIds = rows.map((f) => f.id);
    if (!rows.length) {
      const anything = summaries.some((f) => f.active && f.status !== "suggested");
      cards.appendChild(anything ? message("info", "No formula matches the filter.")
        : message("info", "There are no formulas yet. " + (canEdit() ? "Add one with the + button, or add a document that has one and press Reindex in the Library." : "Suggest one with the + button.")));
    }
    const groups = [...new Set(rows.map((f) => f.group || "Other"))];
    groups.forEach((group) => {
      cards.appendChild(el("h2", "fm-group", group));
      const grid = el("div", "fm-cards");
      rows.filter((f) => (f.group || "Other") === group).forEach((f) => grid.appendChild(cell(f, paintRemove)));
      cards.appendChild(grid);
    });
    paintRemove();
  }
  filter.oninput = () => {
    filterText = filter.value;
    drawCards();
  };
  drawCards();
  paintRemove();
}

// A card with its tick box. Removing a formula only takes it off the list: the documents are never touched, and it can be put back with Undo.
function cell(formula, onTick) {
  const wrap = el("div", "fm-cell");
  const node = card(formula);
  wrap.appendChild(node);
  if (can("3.5", "write")) {
    const box = el("input", "tick fm-tick");
    box.type = "checkbox";
    box.checked = ticked.has(formula.id);
    box.setAttribute("aria-label", "Select " + formula.name);
    box.onchange = () => {
      if (box.checked) ticked.add(formula.id);
      else ticked.delete(formula.id);
      onTick();
    };
    wrap.classList.add("fm-cell--tick");
    wrap.appendChild(box);
  }
  return wrap;
}

function card(formula) {
  const node = el("button", "fm-card" + (formula.active ? "" : " off"));
  node.type = "button";
  const doc = formula.source && formula.source.document;
  const src = el("span", "fm-srcicon");                       // where the formula came from, as an icon
  src.title = doc ? "Source: " + niceTitle(doc) : "Added manually. Not linked to a document.";
  src.appendChild(icon(doc ? "doc" : "edit"));
  const top = el("span", "fm-card__top");
  top.append(el("b", null, formula.name), src);
  node.append(top, el("code", "fm-expr-small", lines(formula.display)));
  if (formula.status === "suggested" || !formula.active) node.appendChild(badge(formula));
  node.onclick = () => openDetail(formula.id);
  return node;
}

// ---------------------------------------------------------------------- the formula agent
async function runAsk(question, box, formulaId) {
  box.replaceChildren(el("div", "fm-loading", "Working..."));
  const result = await api("POST", "/api/formulas/ask", { q: question, formula_id: formulaId });
  if (!result.ok) {
    box.replaceChildren(message("bad", result.data.error || "That did not work."));
    return;
  }
  const reply = result.data;
  if (reply.state === "not_covered") {
    box.replaceChildren(message("warn", "Not covered. " + reply.message + (canEdit() ? " You can add one with the + button." : " You can suggest one with the + button.")));
  } else if (reply.state === "choose") {
    const wrap = message("warn", reply.message);
    const options = el("div", "fm-choices");
    reply.candidates.forEach((c) => options.appendChild(btn(c.name, "btn", () => runAsk(question, box, c.id))));
    wrap.appendChild(options);
    box.replaceChildren(wrap);
  } else {
    openDetail(reply.formula_id, { question, reply });
  }
}

// ---------------------------------------------------------------------- one formula
async function openDetail(id, context, fromHistory = false) {
  shown = id;
  window.weldLeave = () => backToList();                // Back from a formula goes to the list; from the list, home
  if (!fromHistory) history.pushState({ formula: id }, "");
  root.replaceChildren(el("div", "fm-loading", "Loading..."));
  const result = await api("GET", "/api/formulas/" + id);
  if (!result.ok) {
    root.replaceChildren(message("bad", result.data.error || "The formula could not be loaded."));
    return;
  }
  drawDetail(result.data, context);
}

function section(title, ...nodes) {
  const wrap = el("section", "fm-section");
  wrap.append(el("h2", null, title), ...nodes);
  return wrap;
}

function table(headers, rows) {
  const grid = el("table", "fm-table");
  const head = el("tr");
  headers.forEach((h) => head.appendChild(el("th", null, h)));
  grid.appendChild(el("thead")).appendChild(head);
  const body = el("tbody");
  rows.forEach((cells) => {
    const row = el("tr");
    cells.forEach((cell) => {
      const td = el("td");
      td.append(cell);                          // a string becomes text, a node is placed as it is
      row.appendChild(td);
    });
    body.appendChild(row);
  });
  grid.appendChild(body);
  const scroll = el("div", "fm-scroll");
  scroll.appendChild(grid);
  return scroll;
}

function drawDetail(f, context) {
  const page = el("div", "fm-page");
  const title = el("div", "fm-title");
  title.append(el("h1", null, f.name));
  const main = el("div", "fm-main");                         // heading, description and equation stay together in one card
  main.appendChild(title);
  if (f.summary) main.appendChild(el("p", "fm-summary", f.summary));
  main.appendChild(el("div", "fm-expr", lines(f.display)));
  if (f.status === "suggested" || !f.active) main.appendChild(badge(f));
  page.appendChild(main);
  if (f.source && f.source.document) page.appendChild(message("info", "Formula copied from the document. Check the source."));
  else if (f.status === "admin") page.appendChild(message("warn", "Formula added manually. Not from the documents. It may not be correct."));

  if (!f.active && canEdit()) page.appendChild(message("warn", "This formula is removed, so users do not see it."));
  if (f.status === "suggested" && canEdit()) page.appendChild(message("warn", "Suggested by " + f.updated_by + ". Users cannot see it until you approve it."));

  const calc = calculator(f, context);
  if (context && context.question) {
    const found = (context.reply.extracted || []).map((m) => `${m.symbol} = ${m.value} (from “${m.text}”)`);
    const note = message("info", el("b", null, "From your question: "), "“" + context.question + "”. ",
      found.length ? "Numbers read: " + found.join("; ") + ". Check them before you rely on the result." : "No numbers were found in it.");
    if (context.reply.conflicts && context.reply.conflicts.length) note.append(" Two different numbers were given for " + context.reply.conflicts.join(", ") + ", so please enter them below.");
    page.appendChild(note);
  }
  page.appendChild(calc.node);

  if (f.source && f.source.document) page.appendChild(sourceBlock(f));
  if (canEdit() && f.status !== "documents") page.appendChild(adminControls(f));
  root.replaceChildren(page);
  if (context && context.reply && context.reply.state === "answered") calc.show(context.reply.result);
  else if (context && context.reply && context.reply.message) calc.say("warn", context.reply.message, context.reply.missing);
}

function sourceBlock(f) {
  const s = f.source;
  const words = (s.anchor || "").split(/\s+/).filter((w) => w.length > 2).slice(0, 4);
  const open = iconButton("doc", "Open the source: " + niceTitle(s.document) + (s.section ? ", " + s.section.replace(/^\d+\.\s*/, "") : ""), () =>
    openViewer({ source: s.document, chunk_id: s.chunk_id == null ? 0 : s.chunk_id, heading: s.section || "" }, words));
  const box = el("div", "fm-source");
  box.append(open, el("small", null, "Source: " + s.reference));
  return box;
}

// ---------------------------------------------------------------------- the calculator
function calculator(f, context) {
  const node = el("div", "fm-calc");
  const grid = el("div", "fm-inputs");
  const fields = {};
  f.variables.forEach((v) => {
    const label = el("label");
    const caption = el("span", "fm-caption");
    caption.append(el("b", null, v.symbol), el("small", null, v.unit && v.unit !== "-" ? ` (${v.unit})` : ""));
    if (v.name && v.name !== v.symbol) caption.title = v.name + (v.meaning ? ": " + v.meaning : "");
    const input = el("input");
    input.inputMode = "decimal";
    input.placeholder = v.required ? "" : v.default != null ? "if left out: " + v.default : "optional";
    const error = el("small", "fm-err");
    label.append(caption, input, error);
    grid.appendChild(label);
    fields[v.symbol] = { input, error };
  });
  const out = el("div", "fm-result");
  const run = iconButton("play", "Calculate", null, "primary wide");
  run.append(" Calculate");
  node.append(grid, run, out);

  const clearErrors = () => Object.values(fields).forEach((x) => {
    x.error.textContent = "";
    x.input.classList.remove("bad");
  });
  const flag = (symbol, text) => {
    if (!fields[symbol]) return;
    fields[symbol].error.textContent = text;
    fields[symbol].input.classList.add("bad");
  };

  async function calculate() {
    clearErrors();
    const values = {};
    Object.entries(fields).forEach(([symbol, x]) => {
      if (x.input.value.trim()) values[symbol] = x.input.value.trim();
    });
    out.replaceChildren(el("div", "fm-loading", "Calculating..."));
    const result = await api("POST", `/api/formulas/${f.id}/calculate`, { values });
    if (!result.ok) {
      Object.entries(result.data.fields || {}).forEach(([symbol, text]) => flag(symbol, text));
      out.replaceChildren(message("bad", result.data.error || "That did not work."));
      return;
    }
    show(result.data);
  }

  function show(data) {
    out.replaceChildren();
    data.inputs.filter((i) => !i.assumed).forEach((i) => {
      if (fields[i.symbol] && !fields[i.symbol].input.value) fields[i.symbol].input.value = i.value;
    });
    data.outputs.forEach((o) => {
      const row = el("div", "fm-out");
      row.append(el("div", "fm-value", o.text + unitText(o.unit)), el("div", "fm-outname", o.name), el("code", null, `${o.steps} = ${o.text}`));
      out.appendChild(row);
    });
    data.skipped.forEach((s) => out.appendChild(message("info", s.message)));
    data.assumed.forEach((a) => out.appendChild(message("warn", a)));
    data.warnings.forEach((w) => out.appendChild(message("warn", w)));
    }

  function use(values) {
    clearErrors();
    Object.entries(fields).forEach(([symbol, x]) => (x.input.value = values[symbol] != null ? values[symbol] : ""));
    calculate();
  }

  function say(kind, text, missing = []) {
    const extracted = new Set(((context && context.reply && context.reply.extracted) || []).map((m) => m.symbol));
    ((context && context.reply && context.reply.extracted) || []).forEach((m) => {
      if (fields[m.symbol]) fields[m.symbol].input.value = m.value;
    });
    missing.forEach((symbol) => !extracted.has(symbol) && flag(symbol, "Needed"));
    out.replaceChildren(message(kind, text));
  }

  run.onclick = calculate;
  grid.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      calculate();
    }
  });
  return { node, show, use, say };
}

// The calculator on its own (the Agents page uses it): inputs for each variable and a Calculate button.
export const calculatorFor = (formula) => calculator(formula).node;

// ---------------------------------------------------------------------- administrator controls on one formula
function adminControls(f) {
  const wrap = el("section", "fm-admin");
  const buttons = el("div", "fm-icons");

  if (f.status !== "documents") {
    const act = async (action, ask) => {
      if (ask && !(await confirmDialog({ title: "Remove formula", text: ask, yes: "Remove", danger: true }))) return;
      const result = await api("POST", `/api/formulas/${f.id}/${action}`, {});
      if (!result.ok) return wrap.prepend(message("bad", result.data.error || "That did not work."));
      backToList();
    };
    buttons.appendChild(iconButton("edit", "Edit this formula", () => openEditor(f)));
    if (f.status === "suggested") buttons.appendChild(iconButton("check", "Approve this suggestion", () => act("approve"), "primary"));
    if (f.active) buttons.appendChild(iconButton("trash", "Remove this formula (it can be restored)", () => act("delete", `Remove "${f.name}"? Engineers will no longer see it. It can be restored.`), "danger"));
    else buttons.appendChild(iconButton("undo", "Restore this formula", () => act("restore")));
  }
  wrap.append(buttons);
  return wrap;
}

// ---------------------------------------------------------------------- the editor
// A name, what it is used for (optional), one expression, the result's symbol and unit, and a symbol and unit for each variable; the source is optional.
// Every symbol used in the expression must be in the table, and every symbol in the table must be used.
const FUNCTIONS = new Set(["sqrt", "abs", "min", "max", "log", "log10", "exp"]);

function symbolsIn(expression) {
  const found = new Set();
  for (const m of expression.matchAll(/([A-Za-z_][A-Za-z0-9_]*)(\s*\()?/g)) {
    if (!(m[2] && FUNCTIONS.has(m[1]))) found.add(m[1]);
  }
  return found;
}

function variableRow(v, onChange, remove) {
  const node = el("div", "fe-var");
  const symbol = el("input");
  symbol.placeholder = "Symbol";
  symbol.title = "The letter or short name used in the expression, e.g. I";
  symbol.value = v.symbol || "";
  const unit = el("input");
  unit.placeholder = "Unit";
  unit.title = "The unit of this value, e.g. A (write - if it has none)";
  unit.value = v.unit || "";
  symbol.oninput = onChange;
  node.append(symbol, unit, iconButton("trash", "Remove this symbol", remove, "danger"));
  return { node, read: () => ({ symbol: symbol.value.trim(), unit: unit.value.trim() }) };
}

// One equation: the symbol it gives, its expression and unit; a later equation may use an earlier result.
function equationRow(e, onChange, remove) {
  const node = el("div", "fe-result");
  const symbol = el("input");
  symbol.placeholder = "Result, e.g. Q";
  symbol.title = "The symbol for the result, e.g. Q";
  symbol.value = e.symbol || "";
  const expression = el("input");
  expression.placeholder = "Expression, e.g. I**2 * R * t";
  expression.title = "Use + - * / ** ( ) and sqrt(...) abs() min() max() log() log10() exp()";
  expression.value = e.expression || "";
  const unit = el("input");
  unit.placeholder = "Unit, e.g. J";
  unit.title = "The unit of the result, e.g. J";
  unit.value = e.unit || "";
  const label = e.label || "";
  [symbol, expression].forEach((n) => (n.oninput = onChange));
  node.append(symbol, el("span", "fe-eq", "="), expression, unit, iconButton("trash", "Remove this equation", remove, "danger"));
  return { node, read: () => ({ label, symbol: symbol.value.trim(), unit: unit.value.trim(), expression: expression.value.trim() }) };
}

const field = (label, node, hint) => {
  const wrap = el("label", "field");
  wrap.append(el("span", null, label), node);
  if (hint) wrap.appendChild(el("small", null, hint));
  return wrap;
};

// The text of a document, to select the formula's passage in it. `blocks` are the document's paragraphs, headings, list items and tables (GET /api/doc); the passage `anchor` is
// marked wherever it is found, whitespace aside, so what the formula will highlight in the source is what is seen here.
function drawDocument(preview, blocks, anchor) {
  const pieces = [];
  blocks.forEach((b) => {
    if (b.t === "table") (b.rows || []).forEach((cells) => pieces.push({ tag: "p", text: cells.join("  "), cls: "fe-preview__table" }));
    else pieces.push({ tag: b.t === "h" ? "h4" : b.t === "li" ? "li" : "p", text: b.x });
  });
  const squash = (t) => t.replace(/\s+/g, " ").trim();
  const full = pieces.map((p) => p.text).join(" ");
  const flat = [];                                                  // the whitespace-squashed text, each character with its place in `full`
  let lastSpace = true;
  for (let i = 0; i < full.length; i++) {
    const space = /\s/.test(full[i]);
    if (space && lastSpace) continue;
    flat.push([space ? " " : full[i], i]);
    lastSpace = space;
  }
  const want = squash(anchor || "");
  const at = want ? flat.map((x) => x[0]).join("").indexOf(want) : -1;
  const from = at < 0 ? -1 : flat[at][1];
  const to = at < 0 ? -1 : flat[at + want.length - 1][1] + 1;
  preview.replaceChildren();
  let pos = 0;
  pieces.forEach((p) => {
    const node = el(p.tag, p.cls || null);
    const a = Math.max(from, pos);
    const b = Math.min(to, pos + p.text.length);
    if (from >= 0 && a < b) {
      node.append(p.text.slice(0, a - pos), el("mark", "fe-mark", p.text.slice(a - pos, b - pos)), p.text.slice(b - pos));
    } else node.append(p.text);
    preview.appendChild(node);
    pos += p.text.length + 1;
  });
  const marked = preview.querySelector("mark");
  if (marked) marked.scrollIntoView({ block: "nearest" });
  return at >= 0;
}

// The optional source: a document from the library and the sentence the formula comes from. Open the document with the eye, select the sentence and press Highlight, or paste it.
// A tick says the sentence really is in that document.
function sourcePicker(data) {
  const wrap = el("div", "fe-source");
  const select = el("select");
  select.setAttribute("aria-label", "Source document");
  const none = el("option", null, "No document (the formula will say it was added manually)");
  none.value = "";
  select.appendChild(none);
  const anchor = el("textarea");
  anchor.rows = 2;
  anchor.placeholder = "Paste the sentence or paragraph the formula comes from";
  anchor.value = (data.source && data.source.anchor) || "";
  const result = el("small", "fe-found");
  const eye = iconButton("eye", "Show the text of the document, to select the passage in it");
  eye.type = "button";
  const preview = el("div", "fe-preview");
  preview.hidden = true;
  const ink = el("button", "btn fe-ink");
  ink.type = "button";
  ink.hidden = true;
  ink.disabled = true;
  ink.onmousedown = (event) => event.preventDefault();             // pressing it must not lose the selection
  ink.append(icon("edit"), document.createTextNode("Highlight"));
  let blocks = null;
  let open = false;

  const selected = () => {
    const sel = window.getSelection();
    return sel && sel.rangeCount && !sel.isCollapsed && preview.contains(sel.getRangeAt(0).commonAncestorContainer) ? sel.toString().replace(/\s+/g, " ").trim() : "";
  };
  async function showText(on) {
    open = on;
    eye.replaceChildren(icon(on ? "eye-off" : "eye"));
    eye.title = on ? "Hide the text of the document" : "Show the text of the document, to select the passage in it";
    preview.hidden = ink.hidden = !on;
    if (!on) return;
    if (blocks === null || blocks.doc !== select.value) {
      preview.replaceChildren(el("p", "hint", "Opening the document..."));
      const got = await fetch("/api/doc?name=" + encodeURIComponent(select.value)).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      blocks = got ? { doc: select.value, list: got.blocks } : null;
      if (!blocks) return preview.replaceChildren(el("p", "hint", "The text of this document cannot be shown."));
    }
    drawDocument(preview, blocks.list, anchor.value);
  }
  document.addEventListener("selectionchange", () => {
    ink.disabled = !selected();
  });
  ink.onclick = () => {
    const text = selected();
    if (!text) return;
    anchor.value = text;
    drawDocument(preview, blocks.list, text);
    window.getSelection().removeAllRanges();
    ink.disabled = true;
    check();
  };
  eye.onclick = () => showText(!open);

  let wait = null;
  function check() {
    clearTimeout(wait);
    result.className = "fe-found";
    if (!select.value || !anchor.value.trim()) {
      result.textContent = select.value ? "Without a passage the formula links to the whole document." : "";
      return;
    }
    wait = setTimeout(async () => {
      const found = await api("POST", "/api/formulas/locate", { document: select.value, anchor: anchor.value });
      const yes = found.ok && found.data.found;
      result.className = "fe-found " + (yes ? "ok" : "warn");
      result.replaceChildren(icon(yes ? "check" : "warn"), document.createTextNode(yes ? " Found in the document: it is highlighted when someone opens the source." : " Not found in that document. It is saved, but nothing will be highlighted."));
    }, 400);
  }
  const sync = () => {
    anchor.disabled = !select.value;
    eye.disabled = !select.value;
    if (!select.value) showText(false);
    else if (open) showText(true);
    check();
  };
  fetch("/api/library").then((r) => r.json()).then((docs) => {
    docs.forEach((doc) => {
      const option = el("option", null, niceTitle(doc.source));
      option.value = doc.source;
      select.appendChild(option);
    });
    select.value = (data.source && data.source.document) || "";
    sync();
  }).catch(() => {});
  select.onchange = sync;
  anchor.oninput = () => {
    if (open && blocks) drawDocument(preview, blocks.list, anchor.value);
    check();
  };
  anchor.disabled = true;
  eye.disabled = true;
  const pick = el("div");
  pick.style.cssText = "display:flex;gap:8px;align-items:center";
  pick.append(select, eye);
  select.style.flex = "1";
  wrap.append(field("Source (optional)", pick), preview, ink, anchor, result);
  return { node: wrap, read: () => (select.value ? { document: select.value, anchor: anchor.value.trim() } : null) };
}

function openEditor(existing) {
  const editing = Boolean(existing);
  const data = existing && existing.simple ? existing.simple : {};
  const title = editing ? "Edit formula" : canEdit() ? "Add a formula" : "Suggest a formula";
  if (editing && !existing.simple) {
    openDialog({ title, body: message("warn", "This formula cannot be edited in this form. Remove it and add it again."), actions: [{ label: "Close" }] });
    return;
  }

  const form = el("div", "fe-form");
  const problems = el("div");
  const name = el("input");
  name.placeholder = "Name, e.g. Resistive heat";
  name.value = data.name || "";
  const used = el("textarea");
  used.rows = 2;
  used.maxLength = USED_FOR_MAX;
  used.placeholder = "What the formula is used for (optional)";
  used.value = data.description || "";
  const count = el("small", "fe-count", "");
  const recount = () => (count.textContent = `${used.value.length} / ${USED_FOR_MAX}`);
  used.oninput = recount;
  recount();

  const equations = [];
  const eqList = el("div", "fe-vars");

  const rows = [];
  const list = el("div", "fe-vars");
  const check = el("div", "fe-check-line");
  const source = sourcePicker(data);

  // Live check of the one rule that matters most: every symbol used, every symbol defined.
  function recheck() {
    const defined = rows.map((r) => r.read().symbol).filter(Boolean);
    const reads = equations.map((e) => e.read());
    const results = reads.map((e) => e.symbol).filter(Boolean);
    const usedSymbols = new Set();
    reads.forEach((e) => symbolsIn(e.expression).forEach((x) => usedSymbols.add(x)));
    const missing = [...usedSymbols].filter((x) => !defined.includes(x) && !results.includes(x));
    const unusedSymbols = defined.filter((x) => !usedSymbols.has(x));
    const duplicates = defined.filter((x, i) => defined.indexOf(x) !== i);
    const parts = [];
    if (missing.length) parts.push("Add to the table: " + missing.join(", "));
    if (unusedSymbols.length) parts.push("Not used in the expression: " + unusedSymbols.join(", "));
    if (duplicates.length) parts.push("Listed twice: " + [...new Set(duplicates)].join(", "));
    const ok = reads.some((e) => e.expression) && defined.length && !parts.length;
    check.className = "fe-check-line " + (ok ? "ok" : parts.length ? "bad" : "");
    check.replaceChildren(icon(ok ? "check" : "info"), document.createTextNode(ok ? " Every symbol is used and defined." : " " + (parts.join(". ") || "Every symbol in the expression must be in the table, and every symbol in the table must be used.")));
  }

  function addRow(v = {}) {
    const row = variableRow(v, recheck, () => {
      rows.splice(rows.indexOf(row), 1);
      row.node.remove();
      recheck();
    });
    rows.push(row);
    list.appendChild(row.node);
  }
  (data.variables && data.variables.length ? data.variables : [{}]).forEach(addRow);

  function addEquation(e = {}) {
    const row = equationRow(e, recheck, () => {
      equations.splice(equations.indexOf(row), 1);
      row.node.remove();
      recheck();
    });
    equations.push(row);
    eqList.appendChild(row.node);
  }
  (data.equations && data.equations.length ? data.equations : [{ symbol: data.symbol, unit: data.unit, expression: data.expression }]).forEach(addEquation);

  const eqHead = el("div", "fe-vars-head");
  eqHead.append(el("b", null, "Equations"), iconButton("plus", "Add another equation", () => { addEquation(); recheck(); }));
  const head = el("div", "fe-vars-head");
  head.append(el("b", null, "Symbols"), iconButton("plus", "Add a symbol", () => { addRow(); recheck(); }));
  form.append(field("Name", name), field("Used for (optional)", used), count, eqHead, eqList, head, list, check, source.node, problems);
  recheck();

  const label = editing ? "Save changes" : canEdit() ? "Add formula" : "Send suggestion";
  openDialog({
    title,
    body: form,
    wide: true,
    actions: [
      { label: "Cancel" },
      {
        label,
        primary: true,
        onClick: async (close) => {
          const formula = { name: name.value, description: used.value.trim(), equations: equations.map((r) => r.read()).filter((e) => e.symbol || e.expression),
                            variables: rows.map((r) => r.read()).filter((v) => v.symbol || v.unit), source: source.read() };
          const url = editing ? `/api/formulas/${existing.id}/update` : canEdit() ? "/api/formulas" : "/api/formulas/suggest";
          const saved = await api("POST", url, formula);
          if (!saved.ok) {
            if (saved.data.problems && saved.data.problems.length) {
              const items = el("ul");
              saved.data.problems.forEach((p) => items.appendChild(el("li", null, p)));
              problems.replaceChildren(message("bad", items));
            } else {
              problems.replaceChildren(message("bad", saved.data.error || "That did not work."));
            }
            return;
          }
          close();
          if (editing) await openDetail(existing.id, undefined, true);
          else {
            if (history.state && history.state.formula) history.replaceState(null, "");
            await showList();
            drawList(canEdit() ? "The formula was added." : "Thank you. An editor will review your suggestion before anyone else sees it.");
          }
        },
      },
    ],
  });
}
