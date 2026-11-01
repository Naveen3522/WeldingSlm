// "From a draft" source of the Add-a-question window: choose a draft, click the filled cell that holds the answer, and the question is filled in for you to check.
// The pair waits for an administrator like any other, and once approved it is only looked up when someone asks: a draft is a person's own entry, so it is never trained on.
//
//   const picker = draftPicker({ onPick(question) {} })      picker.node (to show)     picker.chosen() -> { draft_id, row, col } or null     picker.load()

import { el } from "../core/dom.js";
import { call } from "../core/api.js";
import { notice } from "../components/notice.js";

// The same wording the server uses when no question is given (my_pairs_routes.draft_question).
function askAbout(draft, field, column) {
  const name = draft.name.trim().replace(/\s+draft$/i, "") || draft.name.trim();
  const where = `in the ${name} draft`;
  if (/^[\d.]+$/.test(field)) return `What is the ${column} at ${draft.heads[0]} ${field} ${where}?`;
  if (!column || /^values?$/i.test(column)) return `What is the ${field} ${where}?`;
  return `What is the ${column} for ${field} ${where}?`;
}

export function draftPicker({ onPick }) {
  const node = el("div", "fe-form");
  const pick = el("select");
  pick.setAttribute("aria-label", "Draft");
  const grid = el("div", "table-wrap");
  let chosen = null;
  let loaded = false;

  async function show() {
    chosen = null;
    const result = await call("GET", "/api/drafts/" + pick.value);
    if (!result.ok) return grid.replaceChildren(notice("error", result.data.error || "That draft could not be loaded."));
    const draft = result.data.draft;
    const table = el("table", "table");
    const head = el("tr");
    draft.heads.forEach((h) => head.appendChild(el("th", null, h)));
    table.appendChild(el("thead")).appendChild(head);
    const tbody = el("tbody");
    draft.rows.forEach((row, r) => {
      const tr = el("tr");
      row.forEach((value, c) => {
        const td = el("td", null, value);
        if (c > 0 && String(value).trim()) {                                 // a filled cell that is not the field name
          td.style.cursor = "pointer";
          td.title = "Send this value as the answer";
          td.onclick = () => {
            chosen = { draft_id: draft.id, row: r, col: c };
            tbody.querySelectorAll("td").forEach((x) => (x.style.outline = ""));
            td.style.outline = "2px solid var(--accent, #7bd)";
            onPick(askAbout(draft, String(row[0]).trim(), String(draft.heads[c] || "").trim()));
          };
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    grid.replaceChildren(table);
  }

  async function load() {
    if (loaded) return;
    loaded = true;
    const listed = await call("GET", "/api/drafts?limit=100");
    if (!listed.ok || !listed.data.drafts.length) {
      return node.replaceChildren(el("p", "hint", listed.ok ? "There is no draft yet. Fill in a draft first, then send one of its cells here." : listed.data.error || "The drafts could not be loaded."));
    }
    listed.data.drafts.forEach((d) => {
      const option = el("option", null, `${d.name}${d.template ? ` (${d.template})` : ""}`);
      option.value = d.id;
      pick.appendChild(option);
    });
    const wrap = el("label", "field");
    wrap.append(el("span", null, "Draft"), pick);
    node.append(wrap, el("p", "hint", "Click the cell that holds the answer."), grid);
    pick.onchange = show;
    show();
  }

  node.hidden = true;
  return { node, chosen: () => chosen, load };
}
