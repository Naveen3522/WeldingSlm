// The top bar every signed-in page shares: the brand and the page links on the left, then the model status, the theme button and the "Username (role)" menu.
// Below 672 px the links and the menu fold into one hamburger on the right. startPage() checks the session first, so a page's own script only runs for someone
// allowed to see it.

import { $, el, icon } from "../core/dom.js";
import { can } from "../core/permissions.js";
import { requireSession } from "../core/session.js";
import { initTheme } from "../core/theme.js";
import { attachMenu } from "./menu.js";
import { initStatusBar } from "./status-bar.js";
import { accountRows, userMenu, whoRow } from "./user-menu.js";

const STATUS_MS = 60000;

// The page links: `screen` is the permission screen the link needs. A link appears once its page exists (`ready`) and the person may read the screen.
const LINKS = [
  { key: "ask", label: "Chat", href: "/", icon: "chat", screen: "1", ready: true },
  { key: "agents", label: "Agents", href: "/agents", icon: "agents", screen: "12", ready: true },
  { key: "library", label: "Library", href: "/library", icon: "book", screen: "2", ready: true },
  { key: "formulas", label: "Formulas", href: "/formulas", icon: "calculator", screen: "3", ready: true },
  { key: "drafts", label: "Drafts", href: "/drafts", icon: "file-pen", screen: "4", ready: true },
];

const visibleLinks = () => LINKS.filter((l) => l.ready && can(l.screen));

function link(entry, active) {
  const node = el("a", "navlink" + (entry.key === active ? " on" : ""));
  node.href = entry.href;
  if (entry.key === active) node.setAttribute("aria-current", "page");
  node.append(icon(entry.icon), el("span", null, entry.label));
  return node;
}

function themeButton() {
  const node = el("button", "iconbtn square");
  node.id = "themeBtn";
  node.type = "button";
  node.title = "Switch between light and dark mode";
  node.setAttribute("aria-label", node.title);
  const sun = icon("sun");
  sun.classList.add("t-sun");
  const moon = icon("moon");
  moon.classList.add("t-moon");
  node.append(sun, moon);
  return node;
}

// The model status: a dot and a few words. Everything else on the page can listen for "weld:status-loaded".
function statusNode() {
  const node = el("span", "status");
  node.title = "The AI model that writes summaries";
  const dot = el("span", "dot");
  dot.id = "dot";
  const text = el("span", null, "Checking the model...");
  text.id = "statusTxt";
  node.append(dot, text);
  return node;
}

const MODEL_KIND = { builtin: "SLM model", trained: "Supervised fine-tuned model" };

// -> { state: "ok" | "warn" | "bad", words, tip } for the cases the server can report. Exactly one answering model should be loaded: the active one.
export function describeStatus(status) {
  if (!status) return { state: "bad", words: "Server not reachable" };
  const kindName = status.active ? MODEL_KIND[status.active.kind] : "";
  if (status.retraining) return { state: "warn", words: kindName ? `${kindName} · training` : "Model training", tip: "The model is in training mode. It cannot answer AI summaries until training ends. One model does one job at a time: training, or answering." };
  if (!status.ollama) return { state: "bad", words: "Model offline" };
  if (!status.models.length) return { state: "bad", words: "No model installed" };
  const kind = kindName;
  if (status.offload) return { state: "warn", words: "Model is using the processor", tip: "The model does not fit in graphics memory, so part of it runs on the processor and answers are slow. Close other programs that use the graphics card." };
  if (status.loaded && status.loaded.length > 1) return { state: "warn", words: "More than one model is loaded", tip: "Loaded: " + status.loaded.join(", ") + ". Only the active one should stay." };
  return { state: "ok", words: kind ? `${kind} · answering` : "Model ready", tip: status.active ? `Answering with: ${kind || "the active model"}${status.active.label ? " (" + status.active.label + ")" : ""}. Loaded now: ${(status.loaded && status.loaded.join(", ")) || "none yet, it loads on the first question"}.` : "" };
}

// Reads /api/status, paints the dot, and tells the page. -> the status, or null when the server did not answer.
export async function loadStatus() {
  let status = null;
  try {
    status = await (await fetch("/api/status")).json();
  } catch (error) {
    status = null;
  }
  const dot = $("#dot");
  if (dot) {
    const { state, words, tip } = describeStatus(status);
    dot.className = "dot " + state;
    $("#statusTxt").textContent = words;
    $("#statusTxt").parentElement.title = tip || "The AI model that writes answers";
  }
  window.dispatchEvent(new CustomEvent("weld:status-loaded", { detail: status }));
  return status;
}

function hamburger(user, active) {
  const wrap = el("div", "menu-wrap hamburger-wrap");
  const button = el("button", "iconbtn square hamburger");
  button.type = "button";
  button.title = "Menu";
  button.setAttribute("aria-label", "Menu");
  button.appendChild(icon("menu"));
  const panel = el("div", "menu-panel");
  const pages = visibleLinks().map((entry) => {
    const node = el("a", "menu-item" + (entry.key === active ? " current" : ""));
    node.href = entry.href;
    node.append(icon(entry.icon), el("span", null, entry.label));
    return node;
  });
  panel.append(whoRow(user), ...pages, el("div", "menu-line"), ...accountRows(active));
  wrap.append(button, panel);
  attachMenu(wrap, button, panel);
  return wrap;
}

// options: active (the page: "ask", "agents", "library", "formulas", "drafts", "profile", "model", "users", "activity", or "" for none),
// screen (the id of the permission screen the page is), level (the older way: the lowest access level the page is for).
export async function startPage({ active = "", level = 1, screen = "" } = {}) {
  const user = await requireSession({ level, screen });
  const top = $("#top");
  top.className = "top";

  const brand = el("a", "brand");
  brand.href = "/";
  brand.title = "Home: ask a question";
  brand.setAttribute("aria-label", "Weld Assist, ask a question");
  const mark = el("span", "mark");
  mark.appendChild(icon("brand"));
  brand.append(mark, el("b", null, "Weld Assist"));

  const left = el("div", "top-left");
  left.appendChild(brand);
  const middle = el("nav", "top-mid");
  middle.setAttribute("aria-label", "Pages");
  visibleLinks().forEach((entry) => middle.appendChild(link(entry, active)));
  const right = el("div", "top-right");
  right.append(statusNode(), themeButton(), userMenu(user, active), hamburger(user, active));

  top.replaceChildren(left, middle, right);
  if (active !== "ask" && !$("#backBar")) {                     // every page but the chat has a back arrow, in the page and not in the navbar
    const bar = el("div", "backbar");
    bar.id = "backBar";
    const back = el("button", "iconbtn");
    back.type = "button";
    back.title = "Back";
    back.setAttribute("aria-label", "Back");
    back.append(icon("arrow-left"), el("span", null, "Back"));
    back.onclick = () => (window.weldLeave ? window.weldLeave() : (location.href = "/"));       // a page inside a page goes up one level; a main page goes home
    bar.appendChild(back);
    top.after(bar);
  }
  initTheme();
  initStatusBar(user);
  loadStatus();
  setInterval(loadStatus, STATUS_MS);
  window.addEventListener("weld:status", loadStatus);
  return user;
}
