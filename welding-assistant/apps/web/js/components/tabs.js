// A row of tabs over a panel. mountTabs(container, [{ id, label, icon, render(panel) }], startId) -> { select(id) }.
// A tab's panel is drawn the first time it is opened and kept after that. The chosen tab is remembered in the address (#roles), so a reload or a link opens it.

import { el, icon } from "../core/dom.js";

export function mountTabs(container, tabs, startId) {
  const bar = el("div", "tabs");
  bar.setAttribute("role", "tablist");
  const panels = el("div");
  const parts = {};

  function select(id, remember = true) {
    Object.entries(parts).forEach(([key, part]) => {
      const on = key === id;
      part.tab.setAttribute("aria-selected", on ? "true" : "false");
      part.tab.tabIndex = on ? 0 : -1;
      part.panel.hidden = !on;
      if (on && !part.drawn) {
        part.drawn = true;
        part.render(part.panel);
      }
    });
    if (remember) history.replaceState(null, "", "#" + id);
  }

  tabs.forEach(({ id, label, icon: iconName, render }) => {
    const tab = el("button", "tabs__tab");
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.append(icon(iconName), el("span", null, label));
    tab.onclick = () => select(id);
    tab.onkeydown = (event) => {
      const at = tabs.findIndex((t) => t.id === id);
      const to = event.key === "ArrowRight" ? at + 1 : event.key === "ArrowLeft" ? at - 1 : -1;
      if (to >= 0 && to < tabs.length) {
        event.preventDefault();
        select(tabs[to].id);
        parts[tabs[to].id].tab.focus();
      }
    };
    const panel = el("div", "tabs__panel");
    panel.setAttribute("role", "tabpanel");
    parts[id] = { tab, panel, render, drawn: false };
    bar.appendChild(tab);
    panels.appendChild(panel);
  });
  container.append(bar, panels);
  const wanted = location.hash.slice(1);
  select(tabs.some((t) => t.id === wanted) ? wanted : startId || tabs[0].id, false);
  return { select };
}
