// Sources column: the passages behind an answer, and the reference numbers that point to them.

import { $, el, button, icon, niceTitle, lastSection, documentName } from "../core/dom.js";
import { renderPassage } from "./render.js";
import { openViewer } from "./viewer.js";
import { save } from "../core/store.js";

const PANEL_KEY = "weldassist.panel";
const panel = () => $("#panelBody");        // only the Ask page has a sources column

let currentMessage = null;

const isWide = () => window.matchMedia("(min-width: 1041px)").matches;

export function isPanelOpen() {
  const body = document.body.classList;
  return isWide() ? !body.contains("nopanel") : body.contains("popen");
}

export function setPanel(open) {
  if (isWide()) {
    document.body.classList.toggle("nopanel", !open);
    save(PANEL_KEY, open ? "1" : "0");
  } else {
    document.body.classList.toggle("popen", open);
  }
}

function relevanceLabel(source, data, dimmed) {
  if (dimmed) return "Not sent to the AI (limit)";
  if (data.route === "faq") return "Passage cited by this answer";
  if (source.coverage != null) return `Relevance ${Math.round(source.coverage * 100)}%`;
  return "";
}

function sourceCard(source, number, data, dimmed) {
  const card = el("div", "source-card" + (dimmed ? " dim" : ""));
  card.dataset.n = number;

  const head = el("div", "source-head");
  head.appendChild(el("div", "source-num", number));
  const title = el("div", "source-title");
  title.appendChild(el("b", null, source.source === "Added by hand" ? "Manually added" : niceTitle(source.source)));
  const section = lastSection(source.heading);
  if (section && section !== documentName(source.source)) {
    title.appendChild(el("span", null, section));
  }
  head.appendChild(title);
  card.appendChild(head);

  const body = el("div", "source-body");
  renderPassage(body, source.text, data.words);
  card.appendChild(body);

  const foot = el("div", "source-foot");
  foot.appendChild(el("span", "source-note", relevanceLabel(source, data, dimmed)));

  const more = button("Show more", { className: "btn ghost" });
  more.onclick = () => {
    const open = body.classList.toggle("open");
    more.textContent = open ? "Show less" : "Show more";
  };
  foot.appendChild(more);

  // A passage written in the app has no .docx behind it: nothing to open in the viewer and nothing to download. Its whole text is in the card ("Show more").
  if (source.source !== "Added by hand") {
    foot.appendChild(button("Preview", {
      className: "btn primary",
      iconName: "eye",
      title: "Open the document at this passage",
      onClick: () => openViewer(source, data.words),
    }));

    const link = el("a", "btn");
    link.href = "/docs/" + encodeURIComponent(source.source);
    link.title = "Download the original .docx";
    link.append(icon("download"), document.createTextNode("Download"));
    foot.appendChild(link);
  }
  card.appendChild(foot);

  // Short passages need no "Show more"
  requestAnimationFrame(() => {
    if (body.scrollHeight <= body.clientHeight + 2) {
      body.classList.add("open");
      more.hidden = true;
    }
  });
  return card;
}

function emptyPanel() {
  const box = el("div", "panel-empty");
  const badge = el("div", "panel-empty-icon");
  badge.appendChild(icon("quote"));
  box.appendChild(badge);
  box.appendChild(document.createTextNode("No source passages for this reply."));
  return box;
}

export function showSources(message) {
  if (!panel()) return;
  currentMessage = message;
  panel().textContent = "";
  document.querySelectorAll(".bot-msg.selected").forEach((node) => node.classList.remove("selected"));

  const data = message && message.data;
  if (!data || !data.sources || !data.sources.length) {
    panel().appendChild(emptyPanel());
    $("#srcSub").textContent = "";
    return;
  }

  const used = data.used || data.sources.length;
  $("#srcSub").textContent = `${used} passage${used === 1 ? "" : "s"} used`;
  data.sources.slice(0, used).forEach((source, index) => {
    panel().appendChild(sourceCard(source, index + 1, data, false));
  });

  const extra = data.sources.length - used;
  if (extra > 0) {
    const details = el("details", "more-sources");
    details.appendChild(el("summary", null, `${extra} more passage${extra === 1 ? "" : "s"} found`));
    const wrap = el("div");
    data.sources.slice(used).forEach((source, index) => {
      wrap.appendChild(sourceCard(source, used + index + 1, data, true));
    });
    details.appendChild(wrap);
    panel().appendChild(details);
  }
  message.root.classList.add("selected");
}

// Scroll the panel to source number n of a message and flash it.
export function focusSource(message, n) {
  if (!panel()) return;
  if (currentMessage !== message) showSources(message);
  setPanel(true);

  const card = panel().querySelector(`.source-card[data-n="${n}"]`);
  if (!card) return;
  const details = card.closest("details");
  if (details) details.open = true;

  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 1500);
}

export function initSources() {
  $("#panelClose").onclick = () => setPanel(false);
  document.body.classList.add("nopanel");                                    // the sources open from an answer's Sources button

  // Clicking a reference number in an answer opens its source card
  $("#thread").addEventListener("click", (event) => {
    const cite = event.target.closest(".cite");
    if (!cite) return;
    const root = cite.closest(".bot-msg");
    if (root && root.message) focusSource(root.message, Number(cite.dataset.n));
  });
}
