// Document viewer: shows a whole document and jumps to the passage that was cited.

import { $, el, icon, niceTitle, documentName } from "../core/dom.js";
import { highlight, passageTable } from "./render.js";

let viewer = null;
let body = null;
let jumpButton = null;

// The viewer is built the first time it is needed, so any page can open a document.
function ensureViewer() {
  if (viewer) return;
  const scrim = el("div", "scrim");
  scrim.id = "scrim";
  viewer = el("section", "viewer");
  viewer.id = "viewer";
  viewer.setAttribute("aria-label", "Document preview");
  viewer.setAttribute("aria-hidden", "true");

  const head = el("div", "viewer-head");
  const title = el("div", "viewer-title");
  const name = el("b");
  name.id = "vTitle";
  const sub = el("span");
  sub.id = "vSub";
  title.append(name, sub);
  jumpButton = el("button", "btn ghost", "Jump to passage");
  jumpButton.type = "button";
  jumpButton.hidden = true;
  const download = el("a", "btn");
  download.id = "vDl";
  download.href = "#";
  download.append(icon("download"), document.createTextNode("Download .docx"));
  const close = el("button", "btn ghost");
  close.type = "button";
  close.setAttribute("aria-label", "Close preview");
  close.appendChild(icon("close"));
  close.onclick = closeViewer;
  scrim.onclick = closeViewer;
  head.append(title, jumpButton, download, close);

  body = el("div", "viewer-body");
  viewer.append(head, body);
  document.body.append(scrim, viewer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeViewer();
  });
}

// Opening the viewer adds a history step, so the Back button closes it before it leaves the page.
function hide() {
  if (!viewer) return;
  document.body.classList.remove("vopen");
  viewer.setAttribute("aria-hidden", "true");
}

export function closeViewer() {
  if (!viewer || viewer.getAttribute("aria-hidden") === "true") return;
  if (history.state && history.state.viewer) history.back();
  else hide();
}

window.addEventListener("popstate", () => {
  if (!(history.state && history.state.viewer)) hide();
});

function buildBlock(block, words) {
  let node;

  if (block.t === "h") {
    node = el("h" + Math.min(block.l + 1, 4));
    highlight(node, block.x, words);
  } else if (block.t === "li") {
    node = el("ul");
    const item = el("li");
    highlight(item, block.x, words);
    node.appendChild(item);
  } else if (block.t === "table") {
    node = passageTable(block.rows, words, block.hit_rows);
  } else {
    node = el("p");
    highlight(node, block.x, words);
  }

  if (block.hit) node.classList.add("hit");
  return node;
}

// source: {source, chunk_id, heading}. words: terms to highlight.
export async function openViewer(source, words = []) {
  ensureViewer();
  const path = (source.heading || "").split(" > ");
  const isDocumentTitle = path.slice(-1)[0] === documentName(source.source);

  const manual = source.source === "Added by hand";                  // a passage written in the app: there is no .docx behind it, so nothing to download
  $("#vTitle").textContent = manual ? "Manually added" : niceTitle(source.source);
  $("#vSub").textContent = path.length && !isDocumentTitle ? path.join(" / ") : "";
  $("#vDl").href = "/docs/" + encodeURIComponent(source.source);
  $("#vDl").hidden = manual;
  jumpButton.hidden = true;

  body.textContent = "";
  body.appendChild(el("div", "viewer-msg", "Opening document..."));
  if (viewer.getAttribute("aria-hidden") === "true") history.pushState({ ...(history.state || {}), viewer: true }, "");
  document.body.classList.add("vopen");
  viewer.setAttribute("aria-hidden", "false");

  if (manual) {                                                      // no document to open: the passage itself, as written
    body.textContent = "";
    const doc = el("article", "doc");
    (source.text || "").split(/\n{2,}/).filter(Boolean).forEach((text) => {
      const node = el("p");
      highlight(node, text, words);
      doc.appendChild(node);
    });
    body.appendChild(doc);
    return;
  }

  try {
    const response = await fetch(`/api/doc?name=${encodeURIComponent(source.source)}&chunk=${source.chunk_id}`);
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "error");

    body.textContent = "";
    const doc = el("article", "doc");
    let firstHit = null;

    data.blocks.forEach((block, index) => {
      const node = buildBlock(block, words);
      if (index === data.first_hit) firstHit = node;
      doc.appendChild(node);
    });
    body.appendChild(doc);

    if (firstHit) {
      jumpButton.hidden = false;
      jumpButton.onclick = () => firstHit.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => firstHit.scrollIntoView({ block: "center" }), 60);
    }
  } catch (error) {
    body.textContent = "";
    body.appendChild(el("div", "viewer-msg", manual ? "The preview is not available for this passage." : "The preview is not available for this document. You can still download it."));
  }
}

// Kept so a page can start the viewer up front; it also starts itself when first used.
export function initViewer() {
  ensureViewer();
}
