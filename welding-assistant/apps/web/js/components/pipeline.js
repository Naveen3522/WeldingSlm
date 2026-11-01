// How an answer is made, shown as it happens and kept afterwards.
//
// While the answer is on its way: ONE line with a spinner that names the step the server is on ("Understanding the question...", "Finding the right section...",
// "Searching the tables...", "Writing the answer..."). It starts as "Fetching the answer..." and appears when the answer has taken longer than SHOW_AFTER_MS, so instant
// answers do not flash it. Nothing here is a timed fake: every step comes from a server event.
//
// Afterwards: "How this was answered" is kept for the Sources panel, with what the question was understood to be (its tasks and asks) and every step with the time it took.

import { el, icon } from "../core/dom.js";

const SHOW_AFTER_MS = 250;

const ms = (n) => (n < 1000 ? Math.max(1, Math.round(n)) + " ms" : (n / 1000).toFixed(1) + " s");

export function createPipeline() {
  const box = el("div", "pipeline");
  box.hidden = true;
  box.setAttribute("role", "status");
  box.setAttribute("aria-live", "polite");
  const steps = [];                       // {id, label, detail, started, ended}
  const line = el("div", "pipeline__line");
  const words = el("span", null, "Fetching the answer...");
  line.append(el("i", "pipeline__spin"), words);
  box.appendChild(line);
  let late = false;                        // true once the answer has taken longer than SHOW_AFTER_MS
  let plan = null;
  const timer = setTimeout(() => {
    late = true;
    box.hidden = false;
  }, SHOW_AFTER_MS);

  const finishCurrent = () => {
    const last = steps[steps.length - 1];
    if (last && last.ended == null) last.ended = performance.now();
  };

  return {
    node: box,
    // A step started: the one before it is finished and the line names the new one. The same step named again only updates what it says.
    stage(id, label, detail = "") {
      const known = steps.find((s) => s.id === id);
      if (known) {
        if (detail) known.detail = detail;
        return;
      }
      finishCurrent();
      steps.push({ id, label, detail, started: performance.now(), ended: null });
      words.textContent = label.replace(/\.*$/, "") + "...";
      if (late) box.hidden = false;
    },
    // How the question was understood (the server's "plan" event).
    plan(value) {
      plan = value;
    },
    // The answer has started or ended: the strip is no longer needed. -> what happened, for the trace.
    stop() {
      clearTimeout(timer);
      finishCurrent();
      box.remove();
      return { plan, steps: steps.map((s) => ({ label: s.label, detail: s.detail, took: (s.ended ?? performance.now()) - s.started })) };
    },
  };
}

// The "How this was answered" disclosure: the tasks and asks the question was understood as, and every step with its time.
export function traceNode(trace, seconds) {
  const { plan, steps } = trace;
  if (!steps.length && !plan) return null;
  const box = el("details", "trace");
  const tasks = plan && plan.tasks.length ? plan.tasks.join(", ") : "";
  const summary = el("summary");
  summary.append(icon("list"), document.createTextNode(" How this was answered"));
  if (tasks) summary.append(el("span", "trace-tasks", " · " + tasks));
  box.appendChild(summary);

  if (plan && plan.asks.length) {
    const list = el("ol", "trace-asks");
    plan.asks.forEach((ask) => {
      const item = el("li");
      const what = ask.facets.map((f) => f.label).join(", ");
      item.append(el("b", null, ask.entity || ask.text), document.createTextNode(what ? ": " + what : ""));
      if (ask.carried) item.appendChild(el("small", null, " (same subject as before)"));
      list.appendChild(item);
    });
    box.append(el("h4", null, plan.asks.length === 1 ? "Understood as" : `Understood as ${plan.asks.length} asks`), list);
  }
  if (steps.length) {
    const list = el("ul", "trace-steps");
    steps.forEach((step) => {
      const item = el("li");
      item.append(icon("check"), el("span", null, step.label), el("small", null, step.detail ? " " + step.detail : ""), el("em", null, ms(step.took)));
      list.appendChild(item);
    });
    box.append(el("h4", null, "Steps"), list);
  }
  if (seconds) box.appendChild(el("p", "trace-total", "Total " + ms(seconds * 1000)));
  return box;
}
