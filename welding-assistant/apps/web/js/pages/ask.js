// The Ask page: the chat, its sources column and the example questions. The top bar, Library, chat history and the user menu come from components/navbar.js.

import { $, el } from "../core/dom.js";
import { initChat, ask, setVia } from "../chat/chat.js";
import { initSources } from "../chat/sources.js";
import { openViewer } from "../chat/viewer.js";
import { mountHistory } from "../components/history-panel.js";
import { notice } from "../components/notice.js";
import { startPage } from "../components/navbar.js";

const OFFLINE_BANNER = "AI model is off, so answers can only show the passages found in the documents.";
const NO_MODEL_BANNER = "No AI model is installed, so answers can only show the passages found in the documents.";
const EXAMPLES = 4;

// What the server says about the model decides which banner shows. Every answer is written by the AI model, so with no model running the page says answers are only the passages found.
function applyStatus(status) {
  const banner = $("#banner");
  if (!status) return;
  banner.hidden = status.ollama && status.models.length > 0;
  if (!banner.hidden) banner.replaceChildren(notice("warn", status.ollama ? NO_MODEL_BANNER : OFFLINE_BANNER));
  if (status.ollama && status.models.length && !status.retraining) {          // loading the model would take the GPU from the training
    fetch("/api/warm", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  }
}

async function loadExamples() {
  try {
    const faqs = await (await fetch("/api/faqs")).json();
    const list = $("#pop");
    faqs.sort(() => Math.random() - 0.5).slice(0, EXAMPLES).forEach((faq) => {
      const item = el("button");
      item.type = "button";
      item.textContent = faq.question;
      item.onclick = () => ask(faq.question);
      list.appendChild(item);
    });
  } catch (error) {
    // The start screen simply has no example questions.
  }
}

// ?q=question opens with that question (and ?agent=fault-finder|procedure-advisor sends it to that agent, as the Agents page does); ?doc=name&chunk=n&words=a,b opens a document.
function openFromAddress() {
  const params = new URLSearchParams(location.search);
  if (["fault-finder", "procedure-advisor"].includes(params.get("agent"))) setVia(params.get("agent"));
  if (params.get("q")) setTimeout(() => ask(params.get("q")), 300);
  if (params.get("doc")) {
    const source = { source: params.get("doc"), chunk_id: Number(params.get("chunk")) || 0, heading: "" };
    const words = (params.get("words") || "").split(",").filter(Boolean);
    setTimeout(() => openViewer(source, words), 300);
  }
}

// "/" jumps to the question box.
function initShortcuts() {
  document.addEventListener("keydown", (event) => {
    const typing = ["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement.tagName);
    if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      $("#q").focus();
    }
  });
}

// The history panel on the left; on a narrow window it opens over the chat from the clock button.
function initHistory() {
  const host = $("#history");
  const history = mountHistory(host, {
    onPick: (question) => {
      document.body.classList.remove("history-open");
      ask(question);
    },
    onNew: () => (location.href = "/"),
  });
  window.addEventListener("weld:asked", history.refresh);
  $("#historyToggle").onclick = () => document.body.classList.toggle("history-open");
  $("#scroller").addEventListener("click", () => document.body.classList.remove("history-open"));
}

startPage({ active: "ask", screen: "1" }).then((user) => {
  initSources();
  initChat();
  initShortcuts();
  initHistory();
  window.addEventListener("weld:status-loaded", (event) => applyStatus(event.detail, user));
  loadExamples();
  openFromAddress();
});
