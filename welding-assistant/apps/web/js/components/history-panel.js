// The history panel on the left of a chat page: "New chat", a search box, your questions grouped Today / Yesterday / Earlier, and Clear all.
// Only the signed-in person's own questions are listed. `area` limits the list to what one agent answered (empty = everything).
// mountHistory(host, { area, onPick, onNew }) -> { refresh }.

import { el, icon } from "../core/dom.js";
import { call } from "../core/api.js";
import { confirmDialog } from "./dialog.js";

const DAY = 86400;

// -> "Today", "Yesterday" or "Earlier" for a time in seconds.
export function groupOf(seconds, now = Date.now() / 1000) {
  const startToday = new Date(now * 1000).setHours(0, 0, 0, 0) / 1000;
  if (seconds >= startToday) return "Today";
  if (seconds >= startToday - DAY) return "Yesterday";
  return "Earlier";
}

export function mountHistory(host, { area = "", onPick, onNew }) {
  host.classList.add("history");
  let items = [];

  const fresh = el("button", "history__new");
  fresh.type = "button";
  fresh.append(icon("plus"), document.createTextNode("New chat"));
  fresh.onclick = onNew;

  const search = el("input");
  search.type = "search";
  search.placeholder = "Search your questions";
  search.setAttribute("aria-label", "Search your questions");
  const searchBox = el("div", "history__search");
  searchBox.append(icon("search"), search);

  const list = el("div", "history__list");
  const foot = el("div", "history__foot");
  const only = el("span", null, "Only you can see these.");
  const clear = el("button", "history__clear", "Clear all");
  clear.type = "button";
  foot.append(only, clear);
  host.append(fresh, searchBox, list, foot);

  function draw() {
    const text = search.value.trim().toLowerCase();
    const shown = items.filter((item) => !text || item.question.toLowerCase().includes(text));
    list.replaceChildren();
    clear.hidden = !items.length;
    if (!shown.length) return list.appendChild(el("div", "history__empty", items.length ? "No question matches." : "Your questions will appear here."));
    let last = "";
    shown.forEach((item) => {
      const group = groupOf(item.asked);
      if (group !== last) list.appendChild(el("div", "history__group", group));
      last = group;
      const row = el("button", "history__row", item.question);
      row.type = "button";
      row.title = item.question;
      row.onclick = () => onPick(item.question);
      list.appendChild(row);
    });
  }

  async function refresh() {
    const result = await call("GET", "/api/history" + (area ? "?area=" + encodeURIComponent(area) : ""));
    items = result.ok ? result.data.items || [] : [];
    draw();
  }

  clear.onclick = async () => {
    const yes = await confirmDialog({ title: "Clear your history?", text: "Your questions are removed from this list. The activity log kept by the administrators is not changed.", yes: "Clear", danger: true });
    if (!yes) return;
    await call("POST", "/api/history/clear", {});
    refresh();
  };
  search.oninput = draw;
  refresh();
  return { refresh };
}
