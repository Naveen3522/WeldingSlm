// Turns passage text and answer text into DOM nodes (text nodes only, no innerHTML).

import { el, escapeRegExp, niceTitle } from "../core/dom.js";

const BULLET = /^\s*([-*\u2022]|\d+[.)])\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;
const HEADING = /^\s*#{2,3}\s+(.+?)\s*$/;
const TABLE_SEPARATOR = /^\|[\s:|-]+\|?$/;

// Appends text to parent, wrapping every match of the given words in <mark>.
export function highlight(parent, text, words) {
  const usable = (words || []).filter((word) => word.length >= 3 || /\d/.test(word));
  if (!usable.length) {
    parent.appendChild(document.createTextNode(text));
    return;
  }

  const pattern = new RegExp("(?:" + usable.map(escapeRegExp).join("|") + ")[a-z0-9]*", "gi");
  let last = 0;
  let match;
  while ((match = pattern.exec(text))) {
    if (match.index > last) {
      parent.appendChild(document.createTextNode(text.slice(last, match.index)));
    }
    parent.appendChild(el("mark", null, match[0]));
    last = match.index + match[0].length;
    if (!match[0].length) pattern.lastIndex++;
  }
  if (last < text.length) {
    parent.appendChild(document.createTextNode(text.slice(last)));
  }
}

// A small table (first row is the header). hitRows are row numbers to mark.
export function passageTable(rows, words, hitRows) {
  const wrap = el("div", "passage-table");
  const table = el("table");

  rows.forEach((cells, index) => {
    const row = el("tr", hitRows && hitRows.includes(index) ? "hit-row" : "");
    cells.forEach((cell) => {
      const node = el(index === 0 ? "th" : "td");
      highlight(node, cell, words);
      row.appendChild(node);
    });
    table.appendChild(row);
  });

  wrap.appendChild(table);
  return wrap;
}

// Passage text with "| a | b |" lines rebuilt as tables and other lines as paragraphs.
export function renderPassage(container, text, words) {
  const lines = text.split("\n");
  let i = 0;

  while (i < lines.length) {
    if (lines[i].trim().startsWith("|")) {
      const tableLines = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        tableLines.push(lines[i].trim());
        i++;
      }
      const rows = tableLines
        .filter((line) => !TABLE_SEPARATOR.test(line))
        .map((line) => line.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
      if (rows.length) container.appendChild(passageTable(rows, words));
    } else {
      if (lines[i].trim()) {
        const paragraph = el("p");
        highlight(paragraph, lines[i].replace(/^#+\s*/, ""), words);
        container.appendChild(paragraph);
      }
      i++;
    }
  }
}

// **bold**, *italic*, `code` and reference numbers like [1][2].
export function renderInline(parent, text) {
  const pattern = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[\d+\](?:\[\d+\])*)/g;
  let last = 0;
  let match;

  while ((match = pattern.exec(text))) {
    if (match.index > last) {
      parent.appendChild(document.createTextNode(text.slice(last, match.index)));
    }
    const token = match[0];

    if (token.startsWith("**")) {
      parent.appendChild(el("strong", null, token.slice(2, -2)));
    } else if (token.startsWith("*")) {
      parent.appendChild(el("em", null, token.slice(1, -1)));
    } else if (token.startsWith("`")) {
      parent.appendChild(el("code", null, token.slice(1, -1)));
    } else {
      for (const number of token.match(/\d+/g)) {
        const cite = el("button", "cite", number);
        cite.type = "button";
        cite.dataset.n = number;
        cite.title = "Show source " + number;
        parent.appendChild(cite);
      }
    }
    last = match.index + token.length;
  }

  if (last < text.length) {
    parent.appendChild(document.createTextNode(text.slice(last)));
  }
}

// A table sent by the server as its own block of the answer.
export function tableCard(event) {
  const card = el("div", "table-card");
  const title = (event.title || "").replace(/^\d+\.\s*/, "");

  if (title || event.source) {
    const heading = el("div", "table-title", title);
    if (event.source) {
      const count = event.rows.length + (event.rows.length === 1 ? " row" : " rows");
      heading.appendChild(el("small", null, niceTitle(event.source) + ", " + count));
    }
    card.appendChild(heading);
  }

  const scroll = el("div", "table-scroll");
  const table = el("table");

  const head = el("thead");
  const headRow = el("tr");
  event.header.forEach((label) => headRow.appendChild(el("th", null, label)));
  head.appendChild(headRow);
  table.appendChild(head);

  const body = el("tbody");
  event.rows.forEach((cells) => {
    const row = el("tr");
    cells.forEach((cell) => row.appendChild(el("td", null, cell)));
    body.appendChild(row);
  });
  table.appendChild(body);

  scroll.appendChild(table);
  card.appendChild(scroll);
  if (event.note) card.appendChild(el("div", "table-note", event.note));
  return card;
}

// Answer text: paragraphs, bullet lists and numbered lists. The first paragraph is the lead.
export function renderAnswer(container, text) {
  container.textContent = "";
  const lines = text.split("\n").filter((line) => line.trim());
  let paragraph = [];
  let list = null;
  let first = true;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const node = el("p", first ? "lead" : "");
    paragraph.forEach((line, index) => {
      if (index) node.appendChild(el("br"));
      renderInline(node, line);
    });
    container.appendChild(node);
    first = false;
    paragraph = [];
  };

  const flushList = () => {
    if (!list) return;
    container.appendChild(list);
    list = null;
    first = false;
  };

  for (const line of lines) {
    const heading = HEADING.exec(line);
    if (heading) {                                   // "## Title": the heading of one part of an answer to a question that asks several things
      flushParagraph();
      flushList();
      const node = el("h3", "ans-h");
      renderInline(node, heading[1]);
      container.appendChild(node);
      first = false;
      continue;
    }
    if (!BULLET.test(line)) {
      flushList();
      paragraph.push(line.trim());
      continue;
    }

    flushParagraph();
    const numbered = NUMBERED.test(line);
    if (list && (list.tagName === "OL") !== numbered) flushList();
    list = list || el(numbered ? "ol" : "ul");

    const item = el("li");
    renderInline(item, line.replace(BULLET, ""));
    list.appendChild(item);
  }

  flushParagraph();
  flushList();
}
