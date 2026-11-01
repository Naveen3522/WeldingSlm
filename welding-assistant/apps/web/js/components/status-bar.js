// The banner under the top bar for Managers and administrators: the documents being indexed, the training that starts by itself afterwards (stage,
// finish time, Cancel for administrators) and how it ended. Both run on the server, so this only redraws their saved state whenever a page loads.

import { can } from "../core/permissions.js";
import { $, el } from "../core/dom.js";
import { call } from "../core/api.js";
import { load, save } from "../core/store.js";
import { confirmDialog } from "./dialog.js";

const FAST_MS = 4000;
const SLOW_MS = 30000;
const SEEN_KEY = "weldassist.retrainSeen";
const SEEN_INDEX = "weldassist.indexSeen";

let me = null;
let timer = null;
let wasRunning = false;
let wasIndexing = false;

const bar = () => $("#retrainBar");
const clock = (seconds) => new Date(seconds * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });

function show(tone, ...rows) {
  bar().className = "retrain-bar " + tone;
  bar().replaceChildren(...rows);
  bar().hidden = false;
}

function hide() {
  bar().hidden = true;
  bar().replaceChildren();
}

function button(label, onClick, className = "btn") {
  const node = el("button", className, label);
  node.type = "button";
  node.onclick = onClick;
  return node;
}

function row(...parts) {
  const node = el("div", "retrain-row");
  node.append(...parts);
  return node;
}

function indexing(job) {
  show("run", row(el("b", null, "Indexing the documents"), el("span", null, (job.stage || "Starting") + "...")),
    row(el("span", "hint", "The new documents become searchable when this finishes. You can close this page: it carries on.")));
}

function training(job) {
  const stepping = job.stage === "train" && job.max_steps;
  const where = stepping ? `Training: step ${job.step} of ${job.max_steps}${job.loss ? `, loss ${job.loss}` : ""}` : job.label || "Starting";
  const meter = el("progress");
  meter.max = 100;
  meter.value = job.percent || 0;
  const left = job.eta_end ? `Done about ${clock(job.eta_end)}${job.human_left ? ` (${job.human_left} left)` : ""}.` : "";
  const cancel = can("6.4", "write") && job.cancellable !== false ? button("Cancel", stop) : null;
  show("run", row(el("b", null, "Training the model"), el("span", null, where)), meter,
    row(el("span", "hint", `${left} AI summaries are paused while this runs; every other answer keeps working. You can close this page: it carries on.`), ...(cancel ? [cancel] : [])));
}

// How the last training ended. -> false when there is nothing new to say about it.
function trained(job) {
  if (load(SEEN_KEY) === String(job.started)) return false;
  const tone = job.status === "finished" ? (job.promoted ? "ok" : "warn") : job.status === "failed" ? "bad" : "info";
  const title = { finished: "Training finished", failed: "Training failed", cancelled: "Training cancelled" }[job.status];
  const more = can("6", "read") ? el("a", "btn", "Open model management") : null;
  if (more) more.href = "/admin/model.html";
  const dismiss = button("Dismiss", () => {
    save(SEEN_KEY, String(job.started));
    hide();
  });
  show(tone, row(el("b", null, title), el("span", null, job.message || "")), row(...[more, dismiss].filter(Boolean)));
  return true;
}

function indexed(job) {
  if (load(SEEN_INDEX) === String(job.finished)) return false;
  const report = job.report || {};
  const failed = job.state === "failed";
  const text = failed ? job.message : [`The documents are indexed.`, report.pairs ? `${report.pairs} new question-and-answer pair${report.pairs === 1 ? "" : "s"} made.` : "", report.hand_pairs_deleted ? `${report.hand_pairs_deleted} question pair${report.hand_pairs_deleted === 1 ? "" : "s"} written by hand ${report.hand_pairs_deleted === 1 ? "was" : "were"} deleted.` : "", report.retrain || ""].filter(Boolean).join(" ");
  const dismiss = button("Dismiss", () => {
    save(SEEN_INDEX, String(job.finished));
    hide();
  });
  show(failed ? "bad" : "ok", row(el("b", null, failed ? "Indexing did not finish" : "Documents updated"), el("span", null, text)), row(dismiss));
  return true;
}

async function stop() {
  const yes = await confirmDialog({ title: "Cancel training", text: "The model in use stays as it is and the progress so far is lost.", yes: "Cancel training", danger: true });
  if (!yes) return;
  const result = await call("POST", "/api/refresh/cancel");
  if (!result.ok) await confirmDialog({ title: "It could not be cancelled", text: result.data.error || "Try again in a moment.", yes: "Close" });
  poll();
}

export async function poll() {
  clearTimeout(timer);
  const result = await call("GET", "/api/refresh/check");
  if (!result.ok) return;
  const { job, index_job: index } = result.data;
  let wait = SLOW_MS;
  if (index.state === "running") {
    indexing(index);
    wait = FAST_MS;
  } else if (job.running) {
    training(job);
    wait = FAST_MS;
  } else if (["finished", "failed", "cancelled"].includes(job.status) && trained(job)) {
    wait = SLOW_MS;                                    // how the training ended matters more than the indexing before it
  } else if (["done", "failed"].includes(index.state) && indexed(index)) {
    wait = SLOW_MS;
  } else {
    hide();
  }
  if (Boolean(job.running) !== wasRunning || (index.state === "running") !== wasIndexing) {
    wasRunning = Boolean(job.running);
    wasIndexing = index.state === "running";
    window.dispatchEvent(new Event("weld:refresh"));
    window.dispatchEvent(new Event("weld:status"));             // AI summaries are switched off while training and back on afterwards
  }
  timer = setTimeout(poll, wait);
}

// Starts watching straight away (for example after an upload).
export function watchNow() {
  if (me) poll();
}

export function initStatusBar(user) {
  me = user;
  clearTimeout(timer);
  if (!can("6.1", "read") || !bar()) return;
  poll();
}
