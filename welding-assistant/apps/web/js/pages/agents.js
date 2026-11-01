// The Agents page: Fault Finder and Procedure Advisor, one card each. A card is not a chat: it offers the agent's basic questions, the questions an administrator added for it
// (under "More questions"), dropdowns that start empty and build a question, and a box for your own words. Asking opens the main chat with this agent chosen and the question sent.

import { $, el, icon, pageHeading } from "../core/dom.js";
import { call } from "../core/api.js";
import { startPage } from "../components/navbar.js";
import { FIELDS, applyOptions, buildQuestion } from "./agent-question.js";

const AGENTS = [
  { id: "fault-finder", name: "Troubleshooter Agent", icon: "troubleshoot", area: "Fault Finder", about: "Find the cause of a weld problem and how to fix it, and check welding safety.",
    basic: ["What causes porosity in MIG welding and how do I stop it?", "Why does expulsion happen in spot welding?"], form: "Describe the problem", button: "Ask Troubleshooter" },
  { id: "procedure-advisor", name: "Procedure Advisor Agent", icon: "checklist", area: "Procedure Advisor", about: "Choose a welding process, follow a procedure and find the standard that applies.",
    basic: ["How do I choose the right welding process for a job?", "Which standards apply to welder and procedure qualification?"], form: "Set up the job", button: "Ask Procedure Advisor" },
];
const SEARCH_FROM = 6;                                   // the list of added questions gets a search box once it is this long

const go = (agent, question) => {
  if (question.trim()) location.href = `/?agent=${encodeURIComponent(agent.id)}&q=${encodeURIComponent(question.trim())}`;
};

function starter(agent, question) {
  const button = el("button");
  button.type = "button";
  button.append(icon("lightbulb"), document.createTextNode(question));
  button.onclick = () => go(agent, question);
  return button;
}

function field(spec) {
  const wrap = el("label", "field");
  wrap.appendChild(el("span", null, spec.label));
  let input;
  if (spec.options) {
    input = el("select");
    const none = el("option", null, "");
    none.value = "";
    input.appendChild(none);
    spec.options.forEach((o) => {
      const [value, text] = Array.isArray(o) ? o : [o, o];
      const option = el("option", null, text);
      option.value = value;
      input.appendChild(option);
    });
  } else {
    input = el("input");
    input.type = "number";
    input.min = "0";
    input.step = "0.5";
    input.placeholder = "Optional";
  }
  input.dataset.f = spec.id;
  wrap.appendChild(input);
  return wrap;
}

function moreQuestions(agent, questions) {
  if (!questions.length) return null;
  const box = el("details", "more");
  const summary = el("summary");
  const chev = icon("expand_more");
  chev.classList.add("chev");
  summary.append(chev, document.createTextNode("More questions"), el("span", "count", String(questions.length)));
  const list = el("div", "starters");
  questions.forEach((q) => list.appendChild(starter(agent, q)));
  box.appendChild(summary);
  if (questions.length >= SEARCH_FROM) {
    const find = el("input", "find");
    find.type = "search";
    find.placeholder = "Search more questions";
    find.setAttribute("aria-label", "Search more questions");
    find.oninput = () => [...list.children].forEach((b) => (b.hidden = !b.textContent.toLowerCase().includes(find.value.toLowerCase())));
    box.appendChild(find);
  }
  box.appendChild(list);
  return box;
}

async function card(agent) {
  const section = el("section", "agent-card");
  section.setAttribute("aria-label", agent.name);
  const head = el("header", "agent-card__head");
  const badge = el("span", "agent-card__icon");
  badge.appendChild(icon(agent.icon));
  head.append(badge, el("h2", null, agent.name), el("p", null, agent.about));

  const basics = el("div");
  basics.appendChild(el("h3", null, "Basic questions"));
  const list = el("div", "starters");
  agent.basic.forEach((q) => list.appendChild(starter(agent, q)));
  basics.appendChild(list);
  const added = await call("GET", "/api/agents/questions?area=" + encodeURIComponent(agent.area));
  const more = moreQuestions(agent, added.ok ? added.data.questions : []);
  if (more) basics.appendChild(more);

  const form = el("form", "guided-form");
  form.appendChild(el("h3", null, agent.form));
  const grid = el("div", "guided");
  FIELDS[agent.id].forEach((spec) => grid.appendChild(field(spec)));
  const detail = el("label", "field wide");
  const detailInput = el("input");
  detailInput.type = "text";
  detailInput.dataset.f = "details";
  detailInput.placeholder = "Add other details...";
  detail.append(el("span", null, "Details (optional)"), detailInput);
  const ask = el("button", "btn primary");
  ask.type = "submit";
  ask.append(icon("arrow-right"), document.createTextNode(agent.button));
  const askBox = el("div", "wide");
  askBox.appendChild(ask);
  grid.append(detail, askBox);
  form.appendChild(grid);

  const values = () => Object.fromEntries([...form.querySelectorAll("[data-f]")].map((n) => [n.dataset.f, n.value.trim()]));
  const refresh = () => {
    const q = buildQuestion(agent.id, values());
    ask.disabled = !q;
  };
  form.addEventListener("input", refresh);
  form.addEventListener("change", refresh);
  form.onsubmit = (event) => {
    event.preventDefault();
    go(agent, buildQuestion(agent.id, values()));
  };
  refresh();

  const own = el("form", "own-form");
  own.appendChild(el("h3", null, "Or ask in your own words"));
  const compose = el("div", "compose");
  const wrap = el("label", "field");
  const box = el("input");
  box.type = "text";
  box.placeholder = "Type a question and press Enter";
  box.setAttribute("aria-label", "Your question to " + agent.name);
  wrap.appendChild(box);
  const send = el("button", "agent-send");
  send.type = "submit";
  send.setAttribute("aria-label", "Ask");
  send.title = "Ask";
  send.appendChild(icon("arrow_upward"));
  compose.append(wrap, send);
  own.appendChild(compose);
  own.onsubmit = (event) => {
    event.preventDefault();
    go(agent, box.value);
  };

  section.append(head, basics, form, own);
  return section;
}

startPage({ active: "agents", screen: "12" }).then(async () => {
  document.title = "Agents - Weld Assist";
  const page = $("#pageBody");
  const intro = pageHeading("Agents", "Pick the agent for your job and set up your question. The answer opens in the main chat.");
  const grid = el("div", "agent-grid");
  page.append(intro, grid);
  const found = await call("GET", "/api/agents/options");
  if (found.ok) applyOptions(found.data);
  for (const agent of AGENTS) grid.appendChild(await card(agent));
});
