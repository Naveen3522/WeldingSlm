// The editable table, used for templates and drafts. It works like a spreadsheet:
//   click selects one cell, a row number or a column title; Ctrl-click adds to the selection; Shift-click selects a range; the corner (or Ctrl+A) selects everything;
//   double-click (or Enter, or F2) edits a cell; right-click opens insert row above or below, insert column left or right, and delete row or column;
//   the Delete key removes the selected rows, and on selected columns or cells only clears their text.
// `renameHeads` (default: the same as `structure`) says whether a column title may be renamed: a draft keeps the titles of its template. `structure` says whether this person may add and remove rows and columns. Without it the rows and columns are fixed, and a cell that has text in `base`
// (the template's own cell) cannot be changed: only the empty cells are filled in. Every cell is put in with textContent, so nothing typed can become markup.
//
//   const grid = mountGrid(box, { structure: true, onChange(dirty) {}, onUpload() {} });
//   grid.load(heads, rows, base)   grid.data() -> { heads, rows }   grid.markSaved()   grid.isDirty()

import { el, icon, iconButton } from "../core/dom.js";

const MAX_ROWS = 300;
const MAX_COLS = 12;

export function mountGrid(container, { structure = false, renameHeads = structure, onChange = () => {}, onUpload = null } = {}) {
  let heads = [];
  let rows = [];
  let base = null;
  let saved = "";
  let S = { rows: [], cols: [], cells: [], all: false };
  const last = { r: -1, c: -1 };
  let editing = null;
  let menuAt = { r: -1, c: -1 };

  const toolbar = el("div", "grid__tools");
  const remove = iconButton("minus", "Delete the selected rows or columns (Delete key)", () => wipe());
  toolbar.appendChild(remove);
  if (onUpload) {
    const up = el("button", "btn", "Upload Excel table");
    up.type = "button";
    up.prepend(icon("file-upload"));
    up.onclick = onUpload;
    toolbar.appendChild(up);
  }
  const hint = el("span", "hint grid__hint");
  hint.hidden = true;                                           // the selection count only; no instructions
  toolbar.appendChild(hint);
  if (!structure && !onUpload) remove.hidden = true;
  const holder = el("div", "grid__holder");
  holder.tabIndex = 0;
  const menu = el("div", "grid__menu");
  menu.hidden = true;
  menu.setAttribute("role", "menu");
  container.replaceChildren(toolbar, holder, menu);

  const snapshot = () => JSON.stringify([heads, rows]);
  const dirty = () => snapshot() !== saved;
  const clearSelection = () => (S = { rows: [], cols: [], cells: [], all: false });
  const has = (list, x) => list.indexOf(x) >= 0;
  let readOnly = false;                                          // a draft that is someone else's: every cell is fixed
  const fixed = (i, j) => readOnly || !structure && base && base[i] && (base[i][j] || "") !== "";
  const changed = () => onChange(dirty());

  // ------------------------------------------------------------------ drawing
  function draw() {
    if (!rows.length) {
      const empty = el("div", "grid__empty");
      empty.append(icon("file-upload"), el("b", null, "No table"), el("span", "hint", structure ? "Start with a blank table or upload an Excel table." : "Nothing to show."));
      if (structure) {
        const blank = el("button", "btn primary", "Add a blank table");
        blank.type = "button";
        blank.onclick = () => {
          heads = ["Field", "Value", "Remarks"];
          rows = [["", "", ""], ["", "", ""], ["", "", ""]];
          clearSelection();
          draw();
          changed();
        };
        empty.appendChild(blank);
        if (onUpload) {
          const up = el("button", "btn", "Upload Excel table");
          up.type = "button";
          up.onclick = onUpload;
          empty.appendChild(up);
        }
      }
      holder.replaceChildren(empty);
      return updateHint();
    }
    const table = el("table", "grid");
    const head = el("tr");
    const corner = el("th", "grid__corner" + (S.all ? " grid__sel" : ""));
    corner.dataset.all = "1";
    corner.title = "Select all";
    head.appendChild(corner);
    heads.forEach((title, j) => {
      const th = el("th", "grid__col" + (S.all || has(S.cols, j) ? " grid__sel" : ""));
      th.dataset.c = String(j);
      th.title = renameHeads ? "Click to select. Ctrl-click to add. Double-click to rename." : "Click to select. Ctrl-click to add.";
      if (editing && editing.h === j) th.appendChild(editor(title, (v) => (heads[j] = v)));
      else th.textContent = title;
      head.appendChild(th);
    });
    table.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    rows.forEach((line, i) => {
      const tr = el("tr");
      tr.dataset.r = String(i);
      const num = el("th", "grid__row" + (S.all || has(S.rows, i) ? " grid__sel" : ""), String(i + 1));
      num.dataset.rn = String(i);
      num.title = "Click to select. Ctrl-click to add. Shift-click for a range.";
      tr.appendChild(num);
      line.forEach((value, j) => {
        const td = el("td", "grid__cell");
        td.dataset.i = String(i);
        td.dataset.j = String(j);
        if (fixed(i, j) || (!structure && base && j === 0)) td.classList.add("grid__fixed");
        if (S.all || has(S.rows, i) || has(S.cols, j)) td.classList.add("grid__in");
        if (has(S.cells, i + "," + j)) td.classList.add("grid__pick");
        if (editing && editing.i === i && editing.j === j) td.appendChild(editor(value, (v) => (line[j] = v)));
        else td.textContent = value;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    holder.replaceChildren(table);
    const input = holder.querySelector("input");
    if (input) {
      input.focus();
      input.select();
    }
    updateHint();
  }

  function editor(value, apply) {
    const input = el("input", "grid__input");
    input.type = "text";
    input.value = value;
    input.dataset.apply = "1";
    input._apply = apply;
    return input;
  }

  function updateHint() {
    const n = S.all ? "Everything" : S.rows.length ? `${S.rows.length} row${S.rows.length > 1 ? "s" : ""}` : S.cols.length ? `${S.cols.length} column${S.cols.length > 1 ? "s" : ""}` : S.cells.length ? `${S.cells.length} cell${S.cells.length > 1 ? "s" : ""}` : "";
    const canRemove = structure && (S.all || S.rows.length > 0);
    remove.disabled = !canRemove;
    remove.style.opacity = canRemove ? "1" : "0.4";
    hint.textContent = n ? `${n} selected.` : "";
    hint.hidden = !n;
  }

  // ------------------------------------------------------------------ changing the table
  function commitEdit() {
    if (!editing) return;
    const input = holder.querySelector("input.grid__input");
    if (input) input._apply(input.value);
    editing = null;
    draw();
    changed();
  }

  function pick(list, n, event, key, min) {
    if (event.shiftKey && last[key] >= 0) {
      S[list] = [];
      for (let x = Math.min(last[key], n); x <= Math.max(last[key], n); x++) if (x >= min) S[list].push(x);
    } else if (event.ctrlKey || event.metaKey) {
      const at = S[list].indexOf(n);
      if (at >= 0) S[list].splice(at, 1);
      else S[list].push(n);
    } else {
      S[list] = [n];
    }
    last[key] = n;
  }

  function insertRow(at) {
    if (!structure || rows.length >= MAX_ROWS) return;
    rows.splice(at, 0, heads.map(() => ""));
    if (base) base.splice(at, 0, heads.map(() => ""));
    clearSelection();
    S.rows = [at];
    draw();
    changed();
  }

  function insertColumn(at) {
    if (!structure || heads.length >= MAX_COLS) return;
    at = Math.max(1, at);
    heads.splice(at, 0, "New column");
    rows.forEach((line) => line.splice(at, 0, ""));
    if (base) base.forEach((line) => line.splice(at, 0, ""));
    clearSelection();
    S.cols = [at];
    draw();
    changed();
  }

  function deleteRows(list) {
    [...list].sort((a, b) => b - a).forEach((i) => {
      rows.splice(i, 1);
      if (base) base.splice(i, 1);
    });
    clearSelection();
    draw();
    changed();
  }

  // Removes the columns in `list` (never the field-name column; a table keeps at least two columns). A refusal is said in the hint line.
  function deleteColumns(list) {
    const wanted = [...new Set(list)].filter((j) => j >= 1).sort((a, b) => b - a);
    if (!structure || !wanted.length) return;
    if (heads.length - wanted.length < 2) {
      hint.textContent = "A table needs at least two columns, so the last column stays. Clear its cells instead.";
      return;
    }
    wanted.forEach((j) => {
      heads.splice(j, 1);
      rows.forEach((line) => line.splice(j, 1));
      if (base) base.forEach((line) => line.splice(j, 1));
    });
    clearSelection();
    draw();
    changed();
  }
  const deleteColumn = (j) => deleteColumns([j]);

  // Delete: rows go (when this person may restructure); columns and cells only lose their text.
  function wipe() {
    if (S.all && structure) {
      rows = [];
      base = null;
      clearSelection();
      draw();
      return changed();
    }
    if (S.rows.length && structure) return deleteRows(S.rows);
    if (S.cols.length && structure) return deleteColumns(S.cols);
    const clear = (i, j) => {
      if (!fixed(i, j) && !(j === 0 && !structure)) rows[i][j] = "";
    };
    if (S.all) rows.forEach((line, i) => line.forEach((_v, j) => clear(i, j)));
    else if (S.rows.length) S.rows.forEach((i) => rows[i].forEach((_v, j) => clear(i, j)));
    else if (S.cols.length) S.cols.forEach((j) => rows.forEach((_line, i) => clear(i, j)));
    else S.cells.forEach((key) => clear(...key.split(",").map(Number)));
    draw();
    changed();
  }

  // ------------------------------------------------------------------ events
  holder.addEventListener("click", (event) => {
    hideMenu();
    if (event.detail >= 2 && !event.ctrlKey && !event.metaKey && !event.shiftKey) {       // the first click redrew the table, so the browser may not send "dblclick": the second click counts
      editAt(event);
      return;
    }
    if (editing && !event.target.closest("input")) commitEdit();
    const all = event.target.closest("[data-all]");
    const rn = event.target.closest("[data-rn]");
    const col = event.target.closest("th[data-c]");
    const cell = event.target.closest("td[data-i]");
    if (all) {
      const was = S.all;
      clearSelection();
      S.all = !was;
    } else if (rn) {
      S.all = false;
      S.cols = [];
      S.cells = [];
      pick("rows", Number(rn.dataset.rn), event, "r", 0);
    } else if (col && !event.target.closest("input")) {
      S.all = false;
      S.rows = [];
      S.cells = [];
      pick("cols", Number(col.dataset.c), event, "c", 0);
    } else if (cell && !event.target.closest("input")) {
      const key = cell.dataset.i + "," + cell.dataset.j;
      S.all = false;
      S.rows = [];
      S.cols = [];
      if (event.ctrlKey || event.metaKey) {
        const at = S.cells.indexOf(key);
        if (at >= 0) S.cells.splice(at, 1);
        else S.cells.push(key);
      } else {
        S.cells = [key];
      }
    } else return;
    draw();
  });

  // Double-click: edit the cell (or the column title) under the pointer.
  function editAt(event) {
    const cell = event.target.closest("td[data-i]");
    const col = event.target.closest("th[data-c]");
    if (cell && !event.target.closest("input")) {
      const i = Number(cell.dataset.i);
      const j = Number(cell.dataset.j);
      if (fixed(i, j) || (j === 0 && !structure)) return;
      commitEdit();
      editing = { i, j };
      draw();
    } else if (col && renameHeads && !event.target.closest("input")) {
      commitEdit();
      editing = { h: Number(col.dataset.c) };
      draw();
    }
  }
  holder.addEventListener("dblclick", editAt);

  holder.addEventListener("keydown", (event) => {
    if (event.target.tagName === "INPUT") {
      if (event.key === "Enter") (event.preventDefault(), commitEdit());
      else if (event.key === "Escape") {
        editing = null;
        draw();
      }
      return;
    }
    const something = S.all || S.rows.length || S.cols.length || S.cells.length;
    if (event.key === "Delete" && something) (event.preventDefault(), wipe());
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a" && rows.length) {
      event.preventDefault();
      clearSelection();
      S.all = true;
      draw();
    } else if ((event.key === "F2" || event.key === "Enter") && S.cells.length === 1) {
      event.preventDefault();
      const [i, j] = S.cells[0].split(",").map(Number);
      if (!fixed(i, j) && !(j === 0 && !structure)) {
        editing = { i, j };
        draw();
      }
    }
  });

  function hideMenu() {
    menu.hidden = true;
  }

  function menuItem(label, onClick, danger = false) {
    const node = el("button", "grid__menu-item" + (danger ? " grid__menu-item--danger" : ""), label);
    node.type = "button";
    node.setAttribute("role", "menuitem");
    node.onclick = () => {
      hideMenu();
      onClick();
    };
    return node;
  }

  holder.addEventListener("contextmenu", (event) => {
    if (!structure) return;
    const tr = event.target.closest("tr[data-r]");
    const cell = event.target.closest("[data-j]");
    const col = event.target.closest("th[data-c]");
    if (!tr && !col) return;
    event.preventDefault();
    menuAt = { r: tr ? Number(tr.dataset.r) : -1, c: cell ? Number(cell.dataset.j) : col ? Number(col.dataset.c) : -1 };
    const r = menuAt.r < 0 ? rows.length - 1 : menuAt.r;
    const c = menuAt.c < 0 ? 1 : menuAt.c;
    menu.replaceChildren(
      menuItem("Insert row above", () => insertRow(r)),
      menuItem("Insert row below", () => insertRow(r + 1)),
      el("div", "grid__menu-line"),
      menuItem("Insert column left", () => insertColumn(c)),
      menuItem("Insert column right", () => insertColumn(c + 1)),
      el("div", "grid__menu-line"),
      menuItem("Delete row", () => deleteRows(has(S.rows, menuAt.r) ? S.rows : [menuAt.r]), true),
      menuItem("Delete column", () => deleteColumns(has(S.cols, menuAt.c) ? S.cols : [menuAt.c]), true),
    );
    menu.hidden = false;                                         // fixed to the window, so it opens right at the pointer
    menu.style.left = Math.max(4, Math.min(event.clientX, window.innerWidth - menu.offsetWidth - 8)) + "px";
    menu.style.top = Math.max(4, Math.min(event.clientY, window.innerHeight - menu.offsetHeight - 8)) + "px";
  });
  // The selection is let go with Escape, or by clicking anywhere outside the table and its buttons.
  const selected = () => S.all || S.rows.length || S.cols.length || S.cells.length;
  const letGo = () => {
    if (!selected() || editing) return;
    clearSelection();
    draw();
  };
  document.addEventListener("click", (event) => {
    if (!menu.contains(event.target)) hideMenu();
    if (!container.contains(event.target) && document.body.contains(event.target)) letGo();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      hideMenu();
      if (event.target.tagName !== "INPUT") letGo();
    }
  });

  // ------------------------------------------------------------------ the outside
  return {
    setReadOnly(on) {
      readOnly = on;
      if (on) (structure = false), (renameHeads = false);
      draw();
    },
    load(newHeads, newRows, newBase = null) {
      heads = newHeads.map(String);
      rows = newRows.map((line) => line.map(String));
      base = newBase ? newBase.map((line) => line.map(String)) : null;
      clearSelection();
      editing = null;
      saved = snapshot();
      draw();
    },
    data: () => ({ heads: [...heads], rows: rows.map((line) => [...line]) }),
    isDirty: dirty,
    markSaved() {
      saved = snapshot();
      changed();
    },
    isEmpty: () => rows.length === 0,
  };
}
