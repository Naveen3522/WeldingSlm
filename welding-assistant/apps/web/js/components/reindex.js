// Reindex: read the documents again so new, changed and removed ones are used in answers, and make the question pairs again. It also clears the question pairs written by
// hand, so before it starts a window says how many, offers them as an Excel file, and lets the person cancel. Used by Model management and by the Library.
//
//   const state = await documentState()          what is waiting: { pending, new, changed, removed, job, hand_pairs, retraining }
//   const done = await reindex()                 asks (with the warning), then starts it. -> { started: true } | { started: false, reason: "cancelled" | the server's message }
//   reindexButton({ onStarted })                 -> { node, paint(state) }: a button that is off until something is waiting

import { el, icon } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { openDialog } from "./dialog.js";
import { notice } from "./notice.js";

export const waiting = (state) => (state ? (state.new || []).length + (state.changed || []).length + (state.removed || []).length : 0);

export async function documentState() {
  const result = await call("GET", "/api/admin/documents");
  return result.ok ? result.data : null;
}

// The window: -> true when the person chose to go on.
function confirm(state) {
  const pairs = state.hand_pairs || 0;
  return new Promise((resolve) => {
    let chosen = false;
    const body = el("div");
    body.style.cssText = "display:flex;flex-direction:column;gap:12px";
    const n = waiting(state);
    body.appendChild(el("p", null, `This reads the documents again so the ${n} new, changed or removed document${n === 1 ? "" : "s"} can be used in answers, and makes the question pairs again. The built-in model is never changed.`));
    if (pairs) {
      body.appendChild(notice("warn", `Reindexing deletes the ${pairs} question pair${pairs === 1 ? "" : "s"} written by hand. Save ${pairs === 1 ? "it" : "them"} as Excel first. Their passages stay in the library, and you can add the pairs again by hand afterwards.`));
      const save = el("a", "btn");
      save.href = "/api/admin/qa/hand.xlsx";
      save.download = "by-hand-pairs.xlsx";
      save.append(icon("download"), document.createTextNode(`Save the ${pairs} as Excel`));
      const saved = el("span", "hint");
      saved.hidden = true;
      saved.textContent = " Saved to your Downloads folder.";
      save.onclick = () => (saved.hidden = false);
      const row = el("div");
      row.append(save, saved);
      body.appendChild(row);
    }
    if (state.retraining) body.appendChild(notice("error", "A retrain is running. Reindex when it has finished."));
    openDialog({
      title: "Reindex the documents",
      body,
      onClose: () => resolve(chosen),
      actions: [
        { label: "Cancel" },
        { label: pairs ? `Delete the ${pairs} and reindex` : "Reindex", primary: true, danger: pairs > 0, onClick: async (close) => { if (state.retraining) return; chosen = true; close(); } },
      ],
    });
  });
}

export async function reindex() {
  const state = await documentState();
  if (!state) return { started: false, reason: "The documents could not be checked." };
  if (!waiting(state)) return { started: false, reason: "Every document is already indexed." };
  if (!(await confirm(state))) return { started: false, reason: "cancelled" };
  const result = await call("POST", "/api/admin/documents/refresh");
  if (!result.ok) return { started: false, reason: result.data.error || "Reindexing could not start." };
  window.dispatchEvent(new Event("weld:refresh"));
  return { started: true };
}

// A button that stays off until a document is waiting. Not shown at all to people who may not index.
export function reindexButton({ onStarted = () => {}, onProblem = () => {} } = {}) {
  const node = el("button", "btn primary");
  node.type = "button";
  node.append(icon("sync"), document.createTextNode("Reindex"));
  node.disabled = true;
  node.title = "Nothing to reindex: every document is indexed.";
  node.hidden = !can("2.4", "write");
  node.onclick = async () => {
    node.disabled = true;
    const done = await reindex();
    if (done.started) onStarted();
    else if (done.reason !== "cancelled") onProblem(done.reason);
    if (!done.started) paint();
  };
  let last = null;
  function paint(state = last) {
    last = state;
    const n = waiting(state);
    const running = Boolean(state && state.job && state.job.state === "running");
    node.disabled = !n || running || Boolean(state && state.retraining);
    node.title = running ? "The documents are being indexed." : state && state.retraining ? "A retrain is running." : n ? `${n} document${n === 1 ? "" : "s"} waiting to be indexed.` : "Nothing to reindex: every document is indexed.";
  }
  return { node, paint };
}
