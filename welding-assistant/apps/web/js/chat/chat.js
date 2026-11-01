// The conversation: sends a question, streams the reply and builds one answer block. An answer is either "From the document" (the document's own text),
// a "Summary" (a short AI summary, with the document text it is based on one click away) or "Calculated" (a formula). Sources are numbered under it.
// runAnswer() is the part any page can use (the Agents page shows its results with it); ask() is the Ask page's chat thread.

import { $, el, button, icon, iconButton, niceTitle, lastSection } from "../core/dom.js";
import { renderAnswer, tableCard } from "./render.js";
import { showSources, focusSource, setPanel } from "./sources.js";
import { openViewer } from "./viewer.js";
import { notice } from "../components/notice.js";
import { answer as fillAnswer, filling, startFill, tableIcon } from "./template-fill.js";
import { createPipeline, traceNode } from "../components/pipeline.js";
import { chosenModel } from "../components/user-menu.js";

const NO_MEMORY_ROUTES = ["intent", "guard_injection", "guard_empty", "wrong_agent"];
const NO_ANSWER_ROUTES = ["no_direct_answer", "not_found", "faq"];
const AREA_OF = { "fault-finder": "Troubleshooter Agent", "procedure-advisor": "Procedure Advisor Agent" };

const thread = () => $("#thread");
const scroller = () => $("#scroller");
const questionBox = () => $("#q");

let busy = false;
let controller = null;
let conversation = [];
let chosenAgent = "";                        // the agent this page talks to ("" = the ordinary chat)

/* ---------- small helpers ---------- */

function scrollDown(force) {
  const box = scroller();
  if (!box) return;
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 160;
  if (force || nearBottom) box.scrollTop = box.scrollHeight;
}

// While answering, the send button becomes "Stop answering" (pause); when the answer is done it is the send arrow again.
function setBusy(value) {
  busy = value;
  const send = $("#send");
  send.disabled = value ? false : !questionBox().value.trim();
  send.replaceChildren(icon(value ? "pause" : "arrow-up"));
  send.title = value ? "Stop answering" : "Send";
  send.setAttribute("aria-label", send.title);
  send.type = value ? "button" : "submit";
  if (!value) questionBox().focus();
}

function autosize() {
  const box = questionBox();
  box.style.height = "auto";
  box.style.height = Math.min(box.scrollHeight, 150) + "px";
  if (!busy) $("#send").disabled = !box.value.trim();
}

// One line under the answer: a tinted notice with a bare icon.
function note(kind, text) {
  return notice(kind === "bad" ? "error" : kind, text);
}

const stripCitations = (text) => text.replace(/\[\d+\]/g, "");

function copyButton(tip, getText) {
  const node = iconButton("copy", tip, null, "plain small");
  node.onclick = async () => {
    try {
      await navigator.clipboard.writeText(getText());
      node.replaceChildren(icon("check"));
    } catch (error) {
      const range = document.createRange();                  // the browser blocked the clipboard: select the text so Ctrl+C works
      const target = node.closest(".user-msg, .bot-body");
      if (target) {
        range.selectNodeContents(target);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
      }
    }
    setTimeout(() => node.replaceChildren(icon("copy")), 1200);
  };
  return node;
}

function formatSeconds(seconds) {
  return seconds < 1 ? Math.round(seconds * 1000) + " ms" : seconds.toFixed(1) + " s";
}

/* ---------- pieces shown under an answer ---------- */

function downloadLinks(files) {
  const box = el("div", "downloads");
  files.forEach((file) => {
    const link = el("a", "btn primary");
    link.href = file.url;
    link.setAttribute("download", "");
    link.append(icon("download"), document.createTextNode(file.label));
    box.appendChild(link);
  });
  return box;
}

function clarifyChips(clarify, again) {
  const box = el("div", "chips");
  box.appendChild(el("span", "chips__note", "Pick one. Your earlier question is kept."));
  clarify.options.forEach((option) => {
    box.appendChild(button(option.label, { onClick: () => again(option.query) }));
  });
  return box;
}

// The numbers of the passages the answer cites, in order (all the used ones when it names none).
function citedIndexes(turn) {
  const { data, cites } = turn;
  const used = data.used || data.sources.length;
  const indexes = new Set();
  if (cites && cites.items && cites.items.length) {
    cites.items.forEach((cite) => {
      const at = data.sources.findIndex((source) => source.chunk_id === cite.chunk);
      if (at >= 0) indexes.add(at);
    });
  }
  if (!indexes.size) data.sources.slice(0, used).forEach((_source, at) => indexes.add(at));
  return [...indexes].sort((a, b) => a - b);
}

// "[1] Document / Section" lines for the copied answer and the Word file.
function sourceLines(turn) {
  if (!turn.data || !turn.data.sources || !turn.data.sources.length) return "";
  return citedIndexes(turn).map((at) => `[${at + 1}] ${niceTitle(turn.data.sources[at].source)} / ${lastSection(turn.data.sources[at].heading).replace(/^\d+\.\s*/, "")}`).join("\n");
}

// The answer as blocks the server can lay out in a Word document: text and tables in order, then what a summary added.
function exportBlocks(turn) {
  const blocks = turn.segments.map((part) => (part.type === "text" ? { type: "text", text: part.text } : { type: "table", title: part.event.title || "", header: part.event.header, rows: part.event.rows }));
  if (turn.aiText) blocks.push({ type: "text", text: "Summary\n" + turn.aiText });
  return blocks;
}

// "Download as Word": the question, the answer with its tables, and the numbered sources, as a .docx.
function wordButton(turn) {
  const node = iconButton("download", "Download this answer as a Word document", null, "plain small");
  node.onclick = async () => {
    node.disabled = true;
    try {
      const used = turn.data ? turn.data.sources.slice(0, turn.data.used || turn.data.sources.length) : [];
      const response = await fetch("/api/export/docx", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: turn.question, label: "Answer", blocks: exportBlocks(turn), sources: used.map((s, i) => ({ n: i + 1, source: s.source, heading: s.heading })) }),
      });
      if (!response.ok) throw new Error("export failed");
      const link = document.createElement("a");
      link.href = URL.createObjectURL(await response.blob());
      link.download = "weld-assist-answer.docx";
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (error) {
      node.replaceChildren(icon("warn"));
      node.title = "The Word document could not be made.";
    }
    node.disabled = false;
  };
  return node;
}

// The quiet row under an answer: Sources (n), copy, Word, and how long it took.
function sourcesRow(turn) {
  const row = el("div", "answer-card__row");
  const { data, message } = turn;
  if (data && data.sources && data.sources.length && $("#panelBody")) {
    const count = citedIndexes(turn).length;
    const open = el("button", "answer-card__sources");
    open.type = "button";
    open.append(icon("list"), document.createTextNode(`Sources (${count})`));
    open.onclick = () => {
      showSources(message);
      setPanel(true);
    };
    row.appendChild(open);
  }
  row.append(copyButton("Copy this answer with its sources", () => turn.text.trim() + (turn.sourceText ? "\n\nSources:\n" + turn.sourceText : "")));
  if (turn.route !== "manual") row.appendChild(wordButton(turn));            // a hand-written answer has no document behind it: no Word file
  turn.files.forEach((file) => row.appendChild(tableIcon(file, (bad) => bad && (row.title = bad))));       // a template's Word file sits with the other icons
  row.appendChild(el("span", "answer-card__time", formatSeconds(turn.seconds)));
  return row;
}

/* ---------- one question and its reply ---------- */

function newTurn() {
  return { text: "", segments: [], aiText: "", exact: null, downloads: [], files: [], fill: null, data: null, done: null, failed: null, replaceReason: null, clarify: null, cites: null, message: null, route: null, seconds: 0, sourceText: "", plan: null, trace: null, question: "" };
}

function addText(turn, chunk) {
  const last = turn.segments[turn.segments.length - 1];
  if (last && last.type === "text") last.text += chunk;
  else turn.segments.push({ type: "text", text: chunk });
  turn.text = turn.segments.filter((part) => part.type === "text").map((part) => part.text).join("\n");
}

function addTable(turn, event) {
  turn.segments.push({ type: "table", event });
  const body = event.rows.map((row) => row.join(" | ")).join("\n");
  turn.text = (turn.text ? turn.text + "\n" : "") + event.header.join(" | ") + "\n" + body;
}

function paintSegments(turn, into) {
  for (const part of turn.segments) {
    if (part.type === "text") {
      const block = el("div", "answer");
      renderAnswer(block, part.text);
      into.appendChild(block);
    } else {
      into.appendChild(tableCard(part.event));
    }
  }
}

function paintAnswer(turn, target) {
  target.textContent = "";
  if (turn.done && turn.done.route === "wrong_agent") {              // "<Agent> can't answer this part. ... Use <Other>.": one line, no answer block
    target.appendChild(notice("info", turn.text.trim()));
    return;
  }
  const card = el("div", "answer-card");
  const head = el("div", "answer-card__head");
  head.append(icon("brand"), el("b", null, "WELD ASSIST"));
  if (chosenAgent && AREA_OF[chosenAgent]) head.appendChild(el("span", "answer-card__wording", AREA_OF[chosenAgent]));       // the agent that answered, as a tag
  card.appendChild(head);
  if (turn.plan && turn.plan.tasks.length) {                    // what kind of task the question was understood as
    const chips = el("div", "task-chips");
    turn.plan.tasks.forEach((task) => chips.appendChild(el("span", "task-chip", task)));
    card.appendChild(chips);
  }
  paintSegments(turn, card);
  if (turn.aiText) {
    const ai = el("div", "answer");
    ai.appendChild(document.createTextNode(turn.aiText));
    card.appendChild(ai);
  }
  if (turn.exact) {                                            // the document text a summary is based on, one click away
    const details = el("details", "based-on");
    details.appendChild(el("summary", null, "Show the document text it is based on"));
    const text = el("div", "answer");
    renderAnswer(text, turn.exact);
    details.appendChild(text);
    card.appendChild(details);
  }
  target.appendChild(card);
}

// Applies one streamed event to the turn. Returns true when the visible answer changed.
function applyEvent(turn, event, ui) {
  switch (event.type) {
    case "stage":
      ui.pipeline.stage(event.id, event.label, event.detail);
      return false;
    case "plan":
      turn.plan = event;
      ui.pipeline.plan(event);
      return true;
    case "sources":
      turn.data = event;
      turn.message.data = event;
      showSources(turn.message);
      ui.pipeline.stage("read", "Reading the passages");
      return false;
    case "token":
      if (turn.text === "") ui.pipeline.stage("write", "Writing the answer");
      addText(turn, event.t);
      return true;
    case "exact":
      turn.exact = event.text;
      return true;
    case "download":
      turn.downloads.push(event);
      return false;
    case "template_file":
      turn.files.push(event);
      return false;
    case "template_fill":
      turn.fill = event;
      return false;
    case "replace":
      turn.exact = null;                           // the replacement is the document text itself
      turn.segments = [];
      addText(turn, event.text);
      turn.replaceReason = event.reason;
      return true;
    case "ai":
      if (!turn.aiText) ui.pipeline.stage("write", "Writing the answer");
      turn.aiText += event.t;
      return true;
    case "ai_drop":
      turn.aiText = "";
      return true;
    case "table":
      addTable(turn, event);
      return true;
    case "clarify":
      turn.clarify = event;
      return false;
    case "cite":
      turn.cites = event;
      return false;
    case "error":
      turn.failed = event.message;
      return false;
    case "done":
      turn.done = event;
      if (event.route === "llm_refusal" && turn.exact) {      // the model found nothing: do not show document text beside "not covered"
        turn.exact = null;
        return true;
      }
      return false;
    default:
      return false;
  }
}

// Reads newline-separated JSON events from the response body.
async function readStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

// The notices under an answer: one line each, simple words. They sit inside the answer card, below the Sources row.
function finishTurn(turn, ui, question, again) {
  const { data, done } = turn;
  const box = ui.body;
  turn.route = (done && done.route) || (data && data.route);
  const card = box.querySelector(".answer-card") || box;
  const hasAnswer = Boolean(turn.text && turn.route !== "intent" && turn.route !== "wrong_agent");

  if (turn.clarify && turn.clarify.options && turn.clarify.options.length) box.appendChild(clarifyChips(turn.clarify, again));
  if (turn.downloads.length) box.appendChild(downloadLinks(turn.downloads));
  if (hasAnswer && data && data.sources && data.sources.length) turn.sourceText = sourceLines(turn);
  if (hasAnswer && turn.trace) turn.message.trace = traceNode(turn.trace, done && done.timings && done.timings.total_s);

  const seconds = done && done.timings && done.timings.total_s;
  turn.seconds = seconds || (performance.now() - ui.startedAt) / 1000;
  if (hasAnswer) card.appendChild(sourcesRow(turn));

  const notes = el("div", "answer-card__notices");
  if (done && done.formula_status === "admin") notes.appendChild(note("warn", "Formula added manually. Not from the documents. It may not be correct."));
  if (done && done.formula_status === "documents") notes.appendChild(note("info", "Formula copied from the document. Check the source."));
  if (turn.route === "manual") notes.appendChild(note("warn", "Added manually. Not from the documents. It may not be correct."));
  if (turn.route === "faq") notes.appendChild(note("info", "Instant answer. This question is already verified."));
  if (turn.failed) notes.appendChild(note("bad", turn.failed));
  if (turn.replaceReason) notes.appendChild(note("info", turn.replaceReason));
  if (data) {
    (data.notes || []).forEach((text) => notes.appendChild(note("info", text)));
    const weak = data.match === "weak" && turn.text && !NO_ANSWER_ROUTES.includes(turn.route);
    if (weak) notes.appendChild(note("warn", "Weak match. Check the sources."));
    const unverified = done && done.numbers_not_in_excerpts;
    if (unverified && unverified.length && turn.route === "llm") notes.appendChild(note("warn", "Some numbers are not in the sources: " + unverified.join(", ")));
  }
  if (notes.children.length) card.appendChild(notes);
}

// Answers one question inside `host` (a container): shows the working strip, streams the reply into a block, and returns the finished turn.
// options: via (an agent chosen on purpose), history (earlier turns for follow-ups), signal (an AbortSignal). Every answer is written by the AI model from the passages found.
export async function runAnswer(host, question, options = {}) {
  const root = el("div", "bot-msg");
  const body = el("div", "bot-body");
  root.appendChild(body);
  host.appendChild(root);

  const pipeline = createPipeline();
  const skeleton = el("div", "skeleton");
  for (let i = 0; i < 3; i++) skeleton.appendChild(el("i"));
  const answerHost = el("div");
  body.append(pipeline.node, skeleton, answerHost);

  const turn = newTurn();
  turn.message = { root, data: null };
  turn.question = question;
  root.message = turn.message;
  root.addEventListener("click", (event) => {                                // a reference number inside the answer opens its source
    const cite = event.target.closest(".cite");
    if (!cite || !turn.data) return;
    if ($("#panelBody")) focusSource(turn.message, Number(cite.dataset.n));
    else if (turn.data.sources[Number(cite.dataset.n) - 1]) openViewer(turn.data.sources[Number(cite.dataset.n) - 1], turn.data.words);
  });
  const ui = { body, pipeline, startedAt: performance.now() };

  let answerNode = null;
  let frame = 0;
  const repaint = () => {
    frame = 0;
    if (!answerNode) {
      skeleton.remove();
      pipeline.stop();
      answerNode = el("div");
      answerHost.appendChild(answerNode);
    }
    paintAnswer(turn, answerNode);
    scrollDown();
  };
  const scheduleRepaint = () => {
    if (!frame) frame = requestAnimationFrame(repaint);
  };

  scrollDown(true);
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      signal: options.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: question, model: chosenModel() || undefined, mode: "answer", via: options.via || undefined, history: options.history || [] }),
    });
    if (!response.ok || !response.body) throw new Error("The server did not respond (" + response.status + ").");
    await readStream(response, (event) => {
      if (applyEvent(turn, event, ui)) scheduleRepaint();
    });
  } catch (error) {
    if (error.name !== "AbortError") turn.failed = error.message;
    else turn.text += (turn.text ? " " : "") + "(stopped)";
  }

  if (frame) cancelAnimationFrame(frame);
  skeleton.remove();
  turn.trace = pipeline.stop();
  if (turn.segments.length || turn.exact || turn.aiText) {
    if (!answerNode) {
      answerNode = el("div");
      answerHost.appendChild(answerNode);
    }
    paintAnswer(turn, answerNode);
  }
  finishTurn(turn, ui, question, (q) => options.again && options.again(q));
  return turn;
}

/* ---------- the Ask page's chat ---------- */

function userMessage(question) {
  const node = el("div", "user-msg");
  node.appendChild(el("span", "user-text", question));
  const tools = el("span", "user-tools");
  tools.appendChild(copyButton("Copy this question", () => question));
  tools.appendChild(iconButton("edit", "Edit this question and ask again", () => {
    const box = questionBox();
    box.value = question;
    autosize();
    box.focus();
  }, "plain small"));
  node.appendChild(tools);
  return node;
}

export async function ask(rawQuestion, options = {}) {
  const question = rawQuestion.trim();
  if (!question || busy) return;
  if (filling()) {                                           // the questions about a template are on: this line is the answer
    thread().appendChild(userMessage(question));
    fillAnswer(question, thread());
    scrollDown(true);
    return;
  }

  const hero = $("#hero");
  if (hero) hero.remove();
  thread().appendChild(userMessage(options.shown || question));

  setBusy(true);
  controller = new AbortController();
  const turn = await runAnswer(thread(), question, { via: options.via || chosenAgent, history: conversation, signal: controller.signal, again: (q) => ask(q) });

  if (turn.fill) startFill(turn.fill, thread());
  const keep = turn.text && !turn.failed && !NO_MEMORY_ROUTES.includes(turn.route);
  if (keep) {
    const shown = turn.data && turn.data.sources ? turn.data.sources.slice(0, turn.data.used || turn.data.sources.length) : [];
    conversation.push({ role: "user", content: question }, { role: "assistant", content: stripCitations(turn.text), chunks: shown.map((source) => source.chunk_id) });
    conversation = conversation.slice(-6);
  }
  setBusy(false);
  controller = null;
  scrollDown(true);
  window.dispatchEvent(new Event("weld:asked"));
}

// The Agents page talks to one named agent: every question is sent to it.
export function setVia(id) {
  chosenAgent = id;
}

export function initChat() {
  $("#form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = questionBox().value;
    questionBox().value = "";
    autosize();
    ask(text);
  });
  questionBox().addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("#form").requestSubmit();
    }
  });
  questionBox().addEventListener("input", autosize);
  $("#send").addEventListener("click", () => busy && controller && controller.abort());
}
