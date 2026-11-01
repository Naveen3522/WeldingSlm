// The window that adds a question and answer pair. Used by the administrators' Question pairs table (the pair is approved at once) and by a person's My submissions page (the pair waits
// for approval): the same window, a different address to send it to.
//
//   openPairDialog({ send: (body) => call(...), areas, onDone(message) })
//
// The answer comes from one of two places. "From documents": pick one or more documents, open each with the eye, select the answer in its text and press + to keep it as a highlight; the
// answer is the highlights in order, so it is always copied word for word. "My own passage": a title and the text, which is added to the knowledge base when the pair is approved.
// Nothing here writes into a document.

import { el, icon, iconButton } from "../core/dom.js";
import { call } from "../core/api.js";
import { openDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";
import { draftPicker } from "../pages/submit-draft.js";

const clean = (name) => name.replace(/\.docx$/, "").replace(/_/g, " ");

// Two icons from Google's Material Symbols (Apache 2.0), as inline SVG paths: the ink highlighter and the ink eraser.
const PATHS = {
  highlight: "M544-400 440-504 240-304l104 104 200-200Zm-47-161 104 104 199-199-104-104-199 199Zm-84-28 216 216-229 229q-24 24-56 24t-56-24l-2-2-26 26H60l126-126-2-2q-24-24-24-56t24-56l229-229Zm0 0 227-227q24-24 56-24t56 24l104 104q24 24 24 56t-24 56L629-373 413-589Z",
  erase: "M690-240h190v80H610l80-80Zm-500 80-85-85q-23-23-23.5-57t22.5-58l440-456q23-24 56.5-24t56.5 23l199 199q23 23 23 57t-23 57L520-160H190Zm296-80 314-322-198-198-442 456 64 64h262Zm-6-240Z",
};

function svgIcon(kind) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 -960 960 960");
  svg.setAttribute("width", "20");
  svg.setAttribute("height", "20");
  svg.setAttribute("fill", "currentColor");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", PATHS[kind]);
  svg.appendChild(path);
  return svg;
}

export function openPairDialog({ send, areas = ["Chat"], onDone = () => {}, documentsOnly = false, title = "Add a question pair", drafts = false, saveLabel = "Save pair" }) {
  const field = (text, node) => {
    const wrap = el("label", "field");
    wrap.append(el("span", null, text), node);
    return wrap;
  };
  const question = el("input");
  question.maxLength = 300;
  question.placeholder = "What is our minimum gap for the 3 mm bracket?";
  const area = el("select");
  areas.forEach((name) => {
    const option = el("option", null, name === "Chat" ? "Chat (default)" : name);
    option.value = name;
    area.appendChild(option);
  });
  const problem = el("div");

  // where the answer comes from
  const choice = el("fieldset", "fe-choice");
  choice.appendChild(el("legend", null, "Where does the answer come from?"));
  const radio = (label) => {
    const line = el("label", "switch-line");
    const input = el("input");
    input.type = "radio";
    input.name = "pair-source";
    line.append(input, el("span", null, " " + label));
    choice.appendChild(line);
    return input;
  };
  const fromDoc = radio("From documents");
  const fromOwn = radio("My own passage");
  const fromDraft = drafts ? radio("From a draft") : null;            // only where a person sends their own pairs (My submissions)
  fromDoc.checked = true;
  if (documentsOnly) choice.hidden = true;                          // a training row must be quoted from a document

  // from documents: rows of document pickers, the text of the open one with its highlights, and the list of everything highlighted so far
  const docBox = el("div", "fe-form");
  const docRows = el("div", "fe-docrows");
  const preview = el("div", "fe-preview");
  const list = el("div", "fe-highlights");
  let library = [];
  const highlights = [];                                           // [{ doc, start, end, text }]: offsets are in the text of that document as shown
  let current = { doc: "", blocks: [] };                          // the document open in the preview
  const showHint = () => preview.replaceChildren(el("p", "hint", "Open a document with the eye to see its text."));
  showHint();

  // The ink highlighter: one button that highlights the selected text, or takes the highlight off when the selection is already highlighted.
  const ink = el("button", "btn fe-ink");
  ink.type = "button";
  ink.disabled = true;
  ink.onmousedown = (event) => event.preventDefault();             // pressing it must not lose the selection
  const inkLabel = el("span", null, "Highlight");
  ink.append(svgIcon("highlight"), inkLabel);

  function selectionRange() {
    const selection = window.getSelection();
    if (!current.doc || !selection.rangeCount || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    if (!preview.contains(range.commonAncestorContainer)) return null;
    const before = document.createRange();
    before.selectNodeContents(preview);
    before.setEnd(range.startContainer, range.startOffset);
    const full = preview.textContent;
    let start = before.toString().length;
    let end = start + range.toString().length;
    while (start < end && /\s/.test(full[start])) start += 1;          // never start or end a highlight on a space
    while (end > start && /\s/.test(full[end - 1])) end -= 1;
    return end > start ? { start, end } : null;
  }

  // What pressing the button would do for the selection: take a highlight off (the selection is inside one), or highlight (any overlapped highlight is replaced).
  function inkState() {
    const sel = selectionRange();
    if (!sel) return { mode: "none" };
    const mine = highlights.filter((h) => h.doc === current.doc);
    const inside = mine.find((h) => h.start <= sel.start && h.end >= sel.end);
    if (inside) return { mode: "remove", target: inside, sel };
    return { mode: "add", over: mine.filter((h) => h.start < sel.end && h.end > sel.start), sel };
  }

  function paintInk() {
    const state = inkState();
    ink.disabled = state.mode === "none";
    ink.classList.toggle("on", state.mode === "remove");
    inkLabel.textContent = state.mode === "remove" ? "Unhighlight" : state.over && state.over.length ? "Highlight again" : "Highlight";
    ink.replaceChildren(svgIcon(state.mode === "remove" ? "erase" : "highlight"), inkLabel);
    ink.title = state.mode === "none" ? "Select text in the document first" : state.mode === "remove" ? "Take the highlight off the selected text"
      : state.over && state.over.length ? "Highlight the selected text instead of the highlight it overlaps" : "Highlight the selected text";
  }
  const onSelection = () => paintInk();
  document.addEventListener("selectionchange", onSelection);

  ink.onclick = () => {
    const state = inkState();
    if (state.mode === "none") return;
    if (state.mode === "remove") highlights.splice(highlights.indexOf(state.target), 1);
    else {
      state.over.forEach((h) => highlights.splice(highlights.indexOf(h), 1));
      highlights.push({ doc: current.doc, start: state.sel.start, end: state.sel.end, text: preview.textContent.slice(state.sel.start, state.sel.end) });
    }
    window.getSelection().removeAllRanges();
    problem.replaceChildren();
    render();
    drawHighlights();
    paintInk();
  };

  // The document as shown, with its highlights drawn in. The offsets of a highlight count the characters of the shown text from its start.
  function render() {
    preview.replaceChildren();
    let pos = 0;
    const marks = highlights.filter((h) => h.doc === current.doc).sort((x, y) => x.start - y.start);
    const write = (parent, text) => {
      const from = pos;
      const to = pos + text.length;                            // the piece covers [from, to) and the space after it is position `to`
      let cursor = from;
      marks.forEach((m) => {
        if (m.end <= cursor || m.start >= to) return;
        const a = Math.max(m.start, cursor);
        const b = Math.min(m.end, to);
        if (a > cursor) parent.append(text.slice(cursor - from, a - from));
        parent.appendChild(el("mark", "fe-mark", text.slice(a - from, b - from)));
        cursor = b;
      });
      if (cursor < to) parent.append(text.slice(cursor - from));
      parent.append(" ");                                      // one space after each piece of text, so a highlight across two blocks never glues their words together
      pos = to + 1;
    };
    current.blocks.forEach((block) => {
      let node;
      if (block.t === "h") {
        node = el("h4");
        write(node, block.x);
      } else if (block.t === "table") {
        node = el("div", "fe-preview__table");
        (block.rows || []).forEach((cells) => {
          const line = el("p");
          write(line, cells.join("  "));
          node.appendChild(line);
        });
      } else {
        node = el(block.t === "li" ? "li" : "p");
        write(node, block.x);
      }
      preview.appendChild(node);
    });
  }

  function drawHighlights() {
    list.replaceChildren();
    highlights.forEach((h, i) => {
      const line = el("div", "fe-highlight");
      const text = el("span", null, h.text.length > 90 ? h.text.slice(0, 90) + "…" : h.text);
      text.title = `${clean(h.doc)}\nQ: ${question.value.trim() || "(question)"}\nA: ${h.text}`;
      line.append(el("b", null, clean(h.doc)), text, iconButton("trash", "Remove this highlight", () => {
        highlights.splice(i, 1);
        if (h.doc === current.doc) render();
        drawHighlights();
      }, "danger"));
      list.appendChild(line);
    });
    clearAll.hidden = !highlights.length;
  }
  question.oninput = drawHighlights;

  const eyes = [];                                                   // [{ picker, button }]: one per document row

  // The eye of the row whose document is shown is crossed out (press it to hide the text); every other eye opens its own document.
  function syncEyes() {
    eyes.forEach(({ picker, button }) => {
      const shown = Boolean(current.doc) && picker.value === current.doc;
      button.replaceChildren(icon(shown ? "eye-off" : "eye"));
      button.classList.toggle("on", shown);
      button.title = shown ? "Hide the text of this document" : "Show the text of this document, to select the answer in it";
      button.setAttribute("aria-label", button.title);
      button.setAttribute("aria-pressed", shown ? "true" : "false");
    });
  }

  function hideText() {
    current = { doc: "", blocks: [] };
    showHint();
    syncEyes();
    paintInk();
  }

  async function openText(name) {
    if (!name) return hideText();
    preview.replaceChildren(el("p", "hint", "Opening the document..."));
    const result = await call("GET", "/api/doc?name=" + encodeURIComponent(name));
    if (!result.ok) return preview.replaceChildren(el("p", "hint", "The text of this document cannot be shown."));
    current = { doc: name, blocks: result.data.blocks };
    render();
    syncEyes();
    paintInk();
  }

  const select = () => {
    const picker = el("select");
    const none = el("option", null, "Choose a document...");
    none.value = "";
    picker.appendChild(none);
    library.forEach((d) => {
      const option = el("option", null, clean(d.source));
      option.value = d.source;
      picker.appendChild(option);
    });
    return picker;
  };

  function addDocRow() {
    const row = el("div", "fe-docrow");
    const picker = select();
    const eye = iconButton("eye", "Show the text of this document", () => (current.doc && picker.value === current.doc ? hideText() : openText(picker.value)));
    const entry = { picker, button: eye };
    eyes.push(entry);
    row.append(picker, eye,
      iconButton("trash", "Remove this document and its highlights", () => {
        for (let i = highlights.length - 1; i >= 0; i--) if (highlights[i].doc === picker.value) highlights.splice(i, 1);
        row.remove();
        eyes.splice(eyes.indexOf(entry), 1);
        if (current.doc === picker.value) hideText();
        drawHighlights();
        paintInk();
      }, "danger"));
    picker.onchange = () => (picker.value ? openText(picker.value) : current.doc ? hideText() : syncEyes());
    docRows.appendChild(row);
  }

  const more = iconButton("plus", "Add another document", addDocRow);
  const docHead = el("div", "fe-vars-head");
  docHead.append(el("b", null, "Documents"), more);
  const clearAll = el("button", "btn ghost");
  clearAll.type = "button";
  clearAll.hidden = true;
  clearAll.append(icon("trash"), document.createTextNode("Clear all"));
  clearAll.title = "Take every highlight off, in every document";
  clearAll.onclick = () => {
    highlights.length = 0;
    window.getSelection().removeAllRanges();
    if (current.doc) render();
    drawHighlights();
    paintInk();
  };
  const hlTools = el("div", "fe-tools");
  hlTools.append(clearAll, ink);
  const hlHead = el("div", "fe-vars-head");
  hlHead.append(el("b", null, "Highlights"), hlTools);
  // The documents and their text on the left, the highlights made from them on the right, side by side
  const left = el("div", "fe-col");
  left.append(docHead, docRows, preview);
  const right = el("div", "fe-col");
  right.append(hlHead, list);
  const split = el("div", "fe-split");
  split.append(left, right);
  docBox.append(split);
  drawHighlights();

  call("GET", "/api/library").then((r) => {
    library = (Array.isArray(r.data) ? r.data : []).filter((d) => d.source.endsWith(".docx"));
    addDocRow();
  });

  // my own passage
  const ownBox = el("div", "fe-form");
  const passageTitle = el("input");
  passageTitle.maxLength = 120;
  passageTitle.placeholder = "P91 post-weld heat treatment";
  const passageText = el("textarea");
  passageText.rows = 4;
  passageText.placeholder = "The text as it should be found and quoted in answers";
  const ownAnswer = el("textarea");
  ownAnswer.rows = 2;
  ownAnswer.placeholder = "The answer, as the model should give it";
  ownBox.append(field("Passage title", passageTitle), field("Passage text", passageText), field("Answer", ownAnswer));
  ownBox.hidden = true;
  const draftBox = draftPicker({ onPick: (text) => (question.value = text) });
  const show = () => {
    docBox.hidden = !fromDoc.checked;
    ownBox.hidden = !fromOwn.checked;
    draftBox.node.hidden = !(fromDraft && fromDraft.checked);
    if (fromDraft && fromDraft.checked) draftBox.load();
  };
  fromDoc.onchange = show;
  fromOwn.onchange = show;
  if (fromDraft) fromDraft.onchange = show;

  const form = el("div", "fe-form");
  form.append(field("Question", question), choice, docBox, ownBox, draftBox.node, ...(documentsOnly ? [] : [field("Shown under More questions in", area)]), problem);
  openDialog({
    title,
    body: form,
    wide: true,
    onClose: () => document.removeEventListener("selectionchange", onSelection),
    actions: [
      { label: "Cancel" },
      {
        label: saveLabel,
        primary: true,
        onClick: async (close) => {
          if (fromDraft && fromDraft.checked) {                               // one filled cell of a draft: the cell is the answer, its row is the passage
            const cell = draftBox.chosen();
            if (!cell) return problem.replaceChildren(notice("error", "Click the cell that holds the answer first.", { closable: true }));
            const sent = await call("POST", "/api/my/pairs/from-draft", { ...cell, question: question.value, area: area.value });
            if (!sent.ok) return problem.replaceChildren(notice("error", sent.data.error || "That did not work.", { closable: true }));
            close();
            return onDone(sent.data);
          }
          const body = fromDoc.checked
            ? { question: question.value, refs: [...highlights].sort((x, y) => (x.doc === y.doc ? x.start - y.start : 0)).map((h) => ({ doc: h.doc, text: h.text })), area: area.value }
            : { question: question.value, answer: ownAnswer.value, passage_title: passageTitle.value, passage_text: passageText.value, area: area.value };
          const result = await send(body);
          if (!result.ok) return problem.replaceChildren(notice("error", result.data.error || "That did not work.", { closable: true }));
          close();
          onDone(result.data);
        },
      },
    ],
  });
}
