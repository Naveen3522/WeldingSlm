// Model management, Models tab, "Reindex": which documents are new, changed or removed since the last indexing, and a Reindex button that is off until there is one. Reindexing
// reads the documents again, makes the question pairs again and deletes the pairs written by hand, so its window warns and offers them as Excel first (components/reindex.js).
// Training the model is separate and optional: a link starts it, and a line says whether question pairs are waiting. Training always starts from the built-in base model.

import { el, icon } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { openDialog, confirmDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";
import { reindexButton, waiting } from "../components/reindex.js";

const POLL_MS = 5000;
const MEASURES = [["quality", "Answer quality (overlap with the reference answer)"], ["invented", "Numbers not in the passages (invented)"], ["false_refusal", "Refusing a question the passages answer"],
                  ["refusal_recall", "Refusing when the passages are wrong or missing"], ["source_line", "Made-up “Source:” lines"]];
const STAGES = [["prepare", "Preparing the data"], ["train", "Training"], ["export", "Making the model"], ["gate", "Checking it"], ["switch", "Switching"]];
const SYMBOL = { ok: "circle-check", warn: "warn", bad: "circle-error" };
const pct = (x) => (x * 100).toFixed(1) + "%";
const show = (key, x) => (key === "quality" ? x.toFixed(3) : pct(x));
const clock = (seconds) => new Date(seconds * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
const day = (seconds) => new Date(seconds * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
const count = (x) => (Array.isArray(x) ? x.length : Number(x) || 0);

// -> { node, reload } ; onTrained() is called when a run has finished so the table can show the new model.
export function retrainSection({ onTrained = () => {} } = {}) {
  const section = el("section", "section");
  const head = el("div", "model__cardhead");
  const title = el("div");
  const sub = el("p", "sub", "Documents you add, change or delete are used in answers only after you reindex. Reindexing also makes the question pairs again and deletes the ones written by hand. Training the model on those pairs is a separate, optional step.");
  title.append(el("h2", null, "Reindex"), sub);
  const reindexer = reindexButton({ onStarted: () => { say("info", "Reindexing has started. You can leave this page; it carries on."); refresh(); }, onProblem: (why) => say("error", why) });
  const go = el("button", "linkbtn", "Train the model on the question pairs");
  go.type = "button";
  go.title = "Teaches the model the question pairs. Replaces the trained model only if the new one passes the checks.";
  const stateLine = el("div", "retrain-line");
  const state = el("div", "states");
  const options = el("div", "row-line");
  stateLine.append(state, options);
  const status = el("div");
  const progress = el("div");
  const result = el("div");
  section.append(head, stateLine, status, progress, result);
  head.append(title, reindexer.node);

  const autoLine = el("label", "switch-line");
  const auto = el("input");
  auto.type = "checkbox";
  auto.disabled = !can("6.4", "write");
  autoLine.append(auto, document.createTextNode(" Train the model by itself after reindexing"));
  const quick = el("button", "linkbtn", "Test the training setup");
  quick.type = "button";
  quick.title = "Runs the whole process on a few pairs to prove it works. The model it makes is never used.";
  options.append(autoLine);
  if (can("6.4", "write")) options.appendChild(go);
  if (can("6.5", "write")) options.appendChild(quick);

  let plans = null;
  let running = false;
  let timer = 0;
  const say = (kind, text) => status.replaceChildren(text ? notice(kind, text) : "");

  // Two lines, because two different things happen. Documents: new, changed or removed ones are used in answers once Reindex has run. Model: training is a separate, optional
  // step that teaches the model the question pairs made from the documents.
  function line(label, pill, text) {
    const row = el("div", "state");
    row.append(el("b", "state__label", label), pill, el("span", null, text));
    return row;
  }

  const names = (list) => list.slice(0, 3).map((n) => n.replace(/^\d+_/, "").replace(/\.docx$/, "").replace(/_/g, " ")).join(", ") + (list.length > 3 ? ` and ${list.length - 3} more` : "");

  function paint(check, job) {
    const doc = check.documents;
    const n = waiting({ new: doc.new, changed: doc.changed, removed: doc.removed });
    const indexing = check.index_job && check.index_job.state === "running";
    const pairs = check.qa.unused;
    const parts = [];
    if (doc.new.length) parts.push(`${doc.new.length} new (${names(doc.new)})`);
    if (doc.changed.length) parts.push(`${doc.changed.length} changed (${names(doc.changed)})`);
    if (doc.removed.length) parts.push(`${doc.removed.length} removed (${names(doc.removed)})`);
    const docs = indexing ? line("Documents", el("span", "pill warn", "Indexing"), `Reading the documents${check.index_job.stage ? ": " + check.index_job.stage : ""}. They can be used in answers when this finishes.`)
      : n ? line("Documents", el("span", "pill warn", "Not indexed"), `${parts.join(", ")}. Press Reindex to use them in answers.`)
      : line("Documents", el("span", "pill ok", "Indexed"), "Every document is indexed and used in answers. Reindex turns on when a document is added, changed or removed.");
    const model = running ? line("Model", el("span", "pill warn", "Training"), "Training the model now.")
      : pairs ? line("Model", el("span", "pill info", "New pairs waiting"), `${pairs} question pair${pairs === 1 ? " has" : "s have"} not been trained on yet. Training is optional: it teaches the model these pairs.`)
      : line("Model", el("span", "pill ok", "Up to date"), job && job.finished ? `The model was last trained on ${day(job.finished)}.` : "No question pairs are waiting to be trained on.");
    state.replaceChildren(docs, model);
    reindexer.paint({ new: doc.new, changed: doc.changed, removed: doc.removed, job: check.index_job, retraining: running, hand_pairs: 0 });
    go.disabled = running;
    quick.disabled = running;
  }

  function verdictTable(v) {
    const table = el("table", "table");
    const header = el("tr");
    ["Check", `In use (${v.active_model})`, "New model"].forEach((label) => header.appendChild(el("th", null, label)));
    table.appendChild(el("thead")).appendChild(header);
    const body = el("tbody");
    table.appendChild(body);
    MEASURES.forEach(([key, label]) => {
      const row = el("tr");
      row.append(el("td", null, label), el("td", null, show(key, v.active[key])), el("td", null, show(key, v.candidate[key])));
      body.appendChild(row);
    });
    const wrap = el("div", "table-wrap");
    wrap.appendChild(table);
    return wrap;
  }

  function drawProgress(job) {
    if (!job.running) return progress.replaceChildren();
    const at = Math.max(0, STAGES.findIndex(([id]) => id === job.stage));
    const stages = el("div", "stages");
    STAGES.forEach(([, label], i) => stages.appendChild(el("span", i < at ? "done" : i === at ? "on" : "", label)));
    const meter = el("progress");
    meter.max = 100;
    meter.value = job.percent || 0;
    const where = job.stage === "train" && job.max_steps ? `Training: step ${job.step} of ${job.max_steps}${job.loss ? `, loss ${job.loss}` : ""}` : job.label || "Starting";
    const left = job.eta_end ? `Done about ${clock(job.eta_end)}${job.human_left ? ` (${job.human_left} left)` : ""}.` : "";
    const box = el("div", "train-state run");
    box.append(el("b", null, where), stages, meter, el("span", "hint", left + " AI summaries are paused while this runs."));
    if (job.cancellable !== false && can("6.4", "write")) {
      const stop = el("button", "btn");
      stop.type = "button";
      stop.textContent = "Cancel training";
      stop.onclick = cancel;
      box.appendChild(stop);
    }
    progress.replaceChildren(box);
  }

  function drawResult(job) {
    if (job.running || !job.status || job.status === "idle") return result.replaceChildren();
    const kind = job.status === "finished" ? (job.promoted ? "success" : "warn") : job.status === "failed" ? "error" : "info";
    const parts = [notice(kind, job.message || job.status)];
    if (job.hand_pairs_deleted) parts.push(el("p", "hint", `${job.hand_pairs_deleted} question pair${job.hand_pairs_deleted === 1 ? "" : "s"} written by hand ${job.hand_pairs_deleted === 1 ? "was" : "were"} deleted when this retrain started. Their passages are still in the library.`));
    if (job.verdict) {
      const details = el("button", "linkbtn", "Show details");
      details.type = "button";
      const holder = el("div");
      details.onclick = () => {
        const open = holder.childElementCount > 0;
        const shown = [verdictTable(job.verdict)];
        if (job.verdict.reasons.length) shown.push(notice("warn", "Not promoted: " + job.verdict.reasons.join("; ") + "."));
        holder.replaceChildren(...(open ? [] : shown));
        details.textContent = open ? "Show details" : "Hide details";
      };
      parts.push(details, holder);
    }
    result.replaceChildren(...parts);
  }

  async function refresh() {
    clearTimeout(timer);
    const [check, job] = await Promise.all([call("GET", "/api/refresh/check"), call("GET", "/api/refresh/status")]);
    if (!check.ok) return say("error", check.data.error || "The training state could not be loaded.");
    plans = check.data.plans;
    auto.checked = Boolean(check.data.auto_retrain);
    const current = job.ok ? job.data : {};
    const wasRunning = running;
    running = Boolean(current.running);
    paint(check.data, current);
    drawProgress(current);
    drawResult(current);
    if (running || (check.data.index_job && check.data.index_job.state === "running")) timer = setTimeout(refresh, POLL_MS);
    else if (wasRunning) onTrained();
  }

  auto.onchange = async () => {
    const done = await call("POST", "/api/admin/retrain/auto", { on: auto.checked });
    if (!done.ok) {
      auto.checked = !auto.checked;
      return say("error", done.data.error || "That did not work.");
    }
    say("info", auto.checked ? "Retraining starts by itself after documents are added." : "Retraining starts only when you press the button.");
  };

  async function start(mode) {
    const done = await call("POST", "/api/refresh/start", { mode });
    if (!done.ok) return say("error", done.data.error || "Retraining could not start.");
    say("info", "Training has started. You can leave this page; it carries on.");
    window.dispatchEvent(new Event("weld:refresh"));
    refresh();
  }

  async function cancel() {
    if (!(await confirmDialog({ title: "Cancel retraining", text: "The model in use stays as it is and the progress so far is lost.", yes: "Cancel retraining", danger: true }))) return;
    const done = await call("POST", "/api/refresh/cancel");
    say(done.ok ? "info" : "error", done.ok ? "Retraining was cancelled." : done.data.error);
    refresh();
  }

  // The window that starts a retrain: what is checked first, and the pairs written by hand, which this deletes, offered as an Excel file.
  async function retrainDialog() {
    const [hand, check] = await Promise.all([call("GET", "/api/admin/qa/hand"), call("GET", "/api/admin/system-check")]);
    const pairs = hand.ok ? hand.data.pairs : [];
    const lines = check.ok ? check.data.lines : [];
    const blocked = check.ok && !check.data.ready;
    const mode = plans && plans.short.ready ? "short" : "full";
    const body = el("div");
    body.style.cssText = "display:flex;flex-direction:column;gap:12px";
    body.append(el("p", null, "This writes fresh question pairs from the documents and trains a new model from the base model. The base model itself is not changed."));
    if (pairs.length) {
      const word = pairs.length === 1 ? "pair" : "pairs";
      body.appendChild(notice("warn", `Retraining deletes the ${pairs.length} question ${word} written by hand. Save them as Excel first.`));
      const save = el("a", "btn");
      save.href = "/api/admin/qa/hand.xlsx";
      save.download = "by-hand-pairs.xlsx";
      save.append(icon("download"), document.createTextNode(`Save the ${pairs.length} as Excel`));
      const saved = el("span", "hint");
      saved.hidden = true;
      saved.textContent = " Saved to your Downloads folder.";
      save.onclick = () => (saved.hidden = false);
      const row = el("div");
      row.append(save, saved);
      body.appendChild(row);
    }
    const list = el("ul", "checks");
    lines.forEach((line) => {
      const item = el("li");
      const icn = icon(SYMBOL[line.state]);
      icn.classList.add(line.state);
      const text = el("span");
      text.append(el("b", null, line.label + ": "), document.createTextNode(line.text));
      item.append(icn, text);
      list.appendChild(item);
    });
    if (lines.length) body.appendChild(list);
    if (plans) body.appendChild(el("p", "hint", `${plans[mode].label}. About ${plans[mode].human} on this computer. The model in use stays active until the new one passes the checks.`));
    if (blocked) body.appendChild(notice("error", "Fix what is marked with a red cross before retraining."));
    openDialog({
      title: "Retrain",
      body,
      actions: [{ label: "Cancel" }, { label: pairs.length ? `Delete the ${pairs.length} and retrain` : "Retrain", primary: true, danger: pairs.length > 0, onClick: async (close) => { if (blocked) return; close(); start(mode); } }],
    });
  }

  go.onclick = retrainDialog;
  quick.onclick = async () => {
    if (await confirmDialog({ title: "Test the training setup", text: "This runs the whole process on a few pairs, about half an hour. The model it makes is never used.", yes: "Start the test" })) start("test");
  };

  refresh();
  return { node: section, reload: refresh, stop: () => clearTimeout(timer) };
}
