// Activity: the audit history. Everyone's questions, newest first: when (date and AM/PM), who (name and email, because names can repeat), the question and what
// answered it. Search, filter by person and by what answered, and Export for Excel (a CSV of every matching row). No answer text is shown here.

import { el, icon, iconButton, pageHeading } from "../core/dom.js";
import { can } from "../core/permissions.js";
import { openDialog } from "../components/dialog.js";
import { call } from "../core/api.js";
import { when } from "../core/format.js";
import { mountPager } from "../components/pager.js";
import { notice } from "../components/notice.js";

const AREAS = ["General", "Fault Finder", "Procedure Advisor", "Formulas", "WPS Builder"];

export async function activityPage(container) {
  const title = pageHeading("Activity", "Everyone's questions and what answered them. Search and filter, export for Excel, or clear old entries.");
  const summary = el("p", "hint");
  const text = el("input");
  text.type = "search";
  text.placeholder = "Search questions and answers";
  text.setAttribute("aria-label", "Search");
  const person = el("input");
  person.type = "search";
  person.placeholder = "Person (name or email)";
  person.setAttribute("aria-label", "Filter by person");
  const area = el("select");
  area.setAttribute("aria-label", "Answered by");
  const any = el("option", null, "Answered by anyone");
  any.value = "";
  area.appendChild(any);
  AREAS.forEach((name) => area.appendChild(el("option", null, name)));
  const download = el("a", "btn");
  download.append(icon("download"), document.createTextNode(" Export for Excel"));
  const tools = el("div", "activity__tools");
  const spacer = el("span");
  spacer.style.flex = "1";
  const clear = iconButton("trash", "Clear activity: delete old entries, or the ones this search matches", () => clearDialog(), "danger");
  tools.append(text, person, area, spacer, download);
  if (can("11.2", "write")) tools.appendChild(clear);
  const status = el("div");
  const list = el("div", "table-wrap");
  const pagerBox = el("div");
  container.append(title, summary, tools, status, list, pagerBox);

  let offset = 0;
  const pager = mountPager(pagerBox, { onChange: ({ offset: o }) => ((offset = o), load()) });
  const filters = () => new URLSearchParams({ user: person.value.trim(), q: text.value.trim(), area: area.value });

  async function load() {
    const query = filters();
    download.href = "/api/audit/export.xlsx?" + query;
    query.set("offset", String(offset));
    query.set("limit", String(pager.limit()));
    const result = await call("GET", "/api/audit?" + query);
    if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Could not load the activity."));
    status.replaceChildren();
    pager.setTotal(result.data.total);
    list.replaceChildren(table(result.data.rows));
  }

  function table(rows) {
    const grid = el("table", "table activity");
    const header = el("tr");
    ["Time", "Person", "Question", "Answered by"].forEach((label) => header.appendChild(el("th", null, label)));
    grid.appendChild(el("thead")).appendChild(header);
    const body = el("tbody");
    grid.appendChild(body);
    if (!rows.length) {
      const empty = el("tr");
      const cell = el("td", "hint", "Nothing matches.");
      cell.colSpan = 4;
      empty.appendChild(cell);
      body.appendChild(empty);
    }
    rows.forEach((row) => {
      const who = el("td");
      who.append(el("b", null, row.username), el("br"), el("span", "hint", row.email || ""));
      const line = el("tr");
      line.append(el("td", "activity__time", when(row.asked)), who, el("td", "activity__question", row.question), el("td", null, row.area || "General"));
      body.appendChild(line);
    });
    return grid;
  }

  // The window that deletes entries. It names what will go and says how many, before anything is deleted.
  function clearDialog() {
    const filtered = Boolean(text.value.trim() || person.value.trim() || area.value);
    const form = el("div", "fe-form");
    const choices = [["30", "Older than 30 days"], ["90", "Older than 90 days"]];
    if (filtered) choices.push(["filter", "Everything the search and filters above match"]);
    choices.push(["all", "Everything"]);
    const body = (value) => {
      const filters = { user: person.value.trim(), q: text.value.trim(), area: area.value };
      if (value === "filter") return filters;
      if (value === "all") return { everything: true };
      return { days: Number(value) };
    };
    const count = el("p", "hint");
    const group = el("fieldset", "fe-choice");
    group.appendChild(el("legend", null, "What should be deleted?"));
    let picked = choices[0][0];
    const confirm = { button: null };
    const refresh = async () => {
      count.textContent = "Counting...";
      const result = await call("POST", "/api/audit/clear", { ...body(picked), dry_run: true });
      const n = result.ok ? result.data.would_delete : 0;
      count.textContent = result.ok ? `${n} entr${n === 1 ? "y" : "ies"} will be deleted. This cannot be undone.` : result.data.error || "Could not count.";
      if (confirm.button) confirm.button.disabled = !n;
    };
    choices.forEach(([value, label], i) => {
      const line = el("label", "switch-line");
      const input = el("input");
      input.type = "radio";
      input.name = "clear-what";
      input.checked = i === 0;
      input.onchange = () => {
        picked = value;
        refresh();
      };
      line.append(input, el("span", null, " " + label));
      group.appendChild(line);
    });
    form.append(group, count, el("p", "hint", "Export for Excel first if you may need these entries. Their feedback is deleted with them. A person's own History menu is not affected by hiding, but deleted entries leave it."));
    const dialog = openDialog({
      title: "Clear activity",
      body: form,
      actions: [
        { label: "Cancel" },
        {
          label: "Delete",
          danger: true,
          onClick: async (close) => {
            const result = await call("POST", "/api/audit/clear", body(picked));
            if (!result.ok) return status.replaceChildren(notice("error", result.data.error || "Nothing was deleted.", { closable: true }));
            close();
            offset = 0;
            pager.reset();
            await load();
            status.replaceChildren(notice("success", `Deleted ${result.data.deleted} entr${result.data.deleted === 1 ? "y" : "ies"}.`, { closable: true }));
          },
        },
      ],
    });
    confirm.button = [...dialog.node.querySelectorAll("button")].find((b) => b.textContent === "Delete");
    refresh();
  }

  let timer = 0;
  const refilter = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      offset = 0;
      pager.reset();
      load();
    }, 250);
  };
  text.oninput = person.oninput = refilter;
  area.onchange = refilter;

  const usage = await call("GET", "/api/activity/summary");
  if (usage.ok) summary.textContent = `${usage.data.questions} question${usage.data.questions === 1 ? "" : "s"} from ${usage.data.people} ${usage.data.people === 1 ? "person" : "people"}.`;
  load();
}
