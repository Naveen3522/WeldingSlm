// Model management, Models tab: what this computer can do (optional), whether the documents are indexed and the model retrained, and the two models: the built-in one, which
// comes with the app and is never changed, and the trained one, which each retrain replaces. The one marked Active answers everyone's questions. A model can be exported to a
// file; adding one from a file is not offered for now.

import { el, icon, iconButton } from "../core/dom.js";
import { call } from "../core/api.js";
import { can } from "../core/permissions.js";
import { confirmDialog, openDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";
import { systemSection } from "./model-system.js";
import { downloadModel } from "./model-package.js";

const FIT = { fits: ["pill ok", "Fits"], slow: ["pill info", "Runs slowly"], too_large: ["pill warn", "Too large"], "": ["pill off", "-"] };
const KIND = { builtin: "SLM model", trained: "Supervised fine-tuned model" };
const day = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "");

export async function modelsTab(panel) {
  const status = el("div", "model__status");
  const tableBox = el("div");
  const say = (kind, text) => status.replaceChildren(text ? notice(kind, text) : "");
  let models = [];

  const system = systemSection({ onChange: () => load() });

  const section = el("section", "section");
  const heading = el("h2", null, "Models");
  const head = el("div", "model__cardhead");
  head.appendChild(heading);
  const train = el("button", "btn primary");
  train.type = "button";
  train.append(icon("play"), document.createTextNode("Train model"));
  section.append(head, tableBox);

  // Train the model on the training set: rows generated from the documents, and rows added here straight from them. The question pairs people add only answer at question time
  // and are never trained on, so a fact that is not in the documents can never be taught to the model.
  async function trainDialog() {
    const body = el("div", "fe-form");
    body.appendChild(el("p", "hint", "Counting the training set..."));
    let choice = "short";
    const dialog = openDialog({
      title: "Train model",
      body,
      actions: [
        { label: "Cancel" },
        {
          label: "Start training",
          primary: true,
          onClick: async (close) => {
            const started = await call("POST", "/api/refresh/start", { mode: choice });
            if (!started.ok) return body.appendChild(notice("error", started.data.error || "The training did not start.", { closable: true }));
            close();
            say("success", "Training started. You can leave this page; it carries on.");
            window.dispatchEvent(new Event("weld:refresh"));
          },
        },
      ],
    });
    const go = [...dialog.node.querySelectorAll("button")].find((b) => b.textContent === "Start training");
    go.disabled = true;
    const counted = await call("GET", "/api/training/summary");
    if (!counted.ok) return body.replaceChildren(notice("error", counted.data.error || "The training set could not be counted."));
    const c = counted.data;
    body.replaceChildren();
    const line = (text) => body.appendChild(el("p", null, text));
    line(`Training builds a fresh set of ${c.size.toLocaleString()} examples from the ${c.documents} documents, so the model only learns what the documents say.`);
    const group = el("fieldset", "fe-choice");
    group.appendChild(el("legend", null, "What to teach"));
    if (!c.new) choice = "full";
    [["short", `New examples first (${c.new} not used yet)`, !c.new], ["full", "Everything again", false]].forEach(([value, label, off]) => {
      const row = el("label", "switch-line");
      const input = el("input");
      input.type = "radio";
      input.name = "train-how";
      input.disabled = off;
      input.checked = value === choice;
      input.onchange = () => (choice = value);
      row.append(input, el("span", null, " " + label));
      group.appendChild(row);
    });
    body.appendChild(group);
    go.disabled = false;
  }
  if (can("6.4", "write")) head.appendChild(train);
  train.onclick = trainDialog;
  panel.append(el("p", "tab-desc", "The models, what this computer can run, and the training set the model learns from."), status, system.node, section);

  async function use(model) {
    const gate = model.gate || {};
    const text = model.fit === "too_large" ? `"${model.label}" is too large for this computer, so it will answer very slowly or not at all. Use it anyway?`
      : gate.passed === false ? `"${model.label}" did not pass the checks, so it may answer worse than the current model. Use it anyway?` : `Use "${model.label}" for everyone?`;
    if (!(await confirmDialog({ title: "Use this model", text, yes: "Use it" }))) return;
    const result = await call("POST", `/api/admin/models/${encodeURIComponent(model.id)}/activate`, {});
    if (!result.ok) return say("error", result.data.error || "That did not work.");
    say("success", `"${model.label}" is now the active model.`);
    window.dispatchEvent(new Event("weld:status"));
    models = result.data.models;
    draw();
  }

  // A name and a note people see instead of the technical one; only the label changes, never the model itself. An empty name goes back to the built-in one.
  function rename(model) {
    const field = (text, node) => {
      const wrap = el("label", "field");
      wrap.append(el("span", null, text), node);
      return wrap;
    };
    const name = el("input");
    name.value = model.renamed ? model.label : "";
    name.placeholder = model.builtin_label;
    name.maxLength = 60;
    const notes = el("input");
    notes.value = model.notes || "";
    notes.placeholder = "What is different about this version";
    notes.maxLength = 500;
    const problem = el("div");
    const form = el("div");
    form.append(field("Name", name), field("Notes", notes), problem);
    openDialog({
      title: "Name this model",
      body: form,
      actions: [
        { label: "Cancel" },
        {
          label: "Save",
          primary: true,
          onClick: async (close) => {
            const result = await call("POST", `/api/admin/models/${encodeURIComponent(model.id)}/rename`, { display_name: name.value, notes: notes.value });
            if (!result.ok) return problem.replaceChildren(notice("error", result.data.error || "That did not work."));
            close();
            say("success", "Saved.");
            models = result.data.models;
            draw();
          },
        },
      ],
    });
  }

  function actions(model) {
    const box = el("div", "acts");
    if (!model.active && can("6.2", "write")) box.appendChild(iconButton("check", "Use this model", () => use(model)));
    if (can("6.3", "write")) box.appendChild(iconButton("edit", "Name this model", () => rename(model)));
    if (model.kind === "trained" && model.dataset && can("6.3", "write")) {
      box.appendChild(iconButton("download", "Download this model: the trained adapter, its base model and how the training went", async () => {
        const problem = await downloadModel(model);
        say(problem ? "error" : "success", problem || "The model file was downloaded.");
      }));
    }
    return box;
  }

  function draw() {
    const listed = models.filter((m) => m.kind !== "builtin" || m.active);          // the old SLM model is only listed while it is the one in use
    const table = el("table", "table");
    const header = el("tr");
    ["Model", "Model name", "Parameters", "Size", "Fit", "Check", "Status"].forEach((label) => header.appendChild(el("th", null, label)));
    header.appendChild(el("th", "right", "Actions"));
    table.appendChild(el("thead")).appendChild(header);
    const body = el("tbody");
    table.appendChild(body);
    listed.forEach((model) => {
      const gate = model.gate || {};
      const name = el("td");
      const label = el("span", "model__name");
      label.append(el("b", null, model.renamed ? model.label : KIND[model.kind] || model.label));
      name.appendChild(label);
      const fitCell = el("td");
      fitCell.appendChild(el("span", ...FIT[model.fit || ""]));
      const state = el("td");
      state.appendChild(el("span", "pill " + (model.active ? "" : "off"), model.active ? "Active" : "Stored"));
      const acts = el("td");
      acts.appendChild(actions(model));
      const row = el("tr", model.active ? "in-use" : "");
      const code = el("td");
      code.appendChild(el("code", null, model.id));
      row.append(name, code, el("td", null, model.params_b ? `${model.params_b} B` : "-"), el("td", null, model.size_gb ? `${model.size_gb} GB` : "-"), fitCell,
        el("td", null, gate.passed === undefined ? "-" : gate.passed ? "Passed" : "Not passed"), state, acts);
      body.appendChild(row);
    });
    const wrap = el("div", "table-wrap");
    wrap.appendChild(table);
    tableBox.replaceChildren(wrap);
  }

  async function load() {
    const result = await call("GET", "/api/admin/models");
    if (!result.ok) return tableBox.replaceChildren(notice("error", result.data.error || "The models could not be loaded."));
    models = result.data.models;
    draw();
  }

  await load();
  return { reload: load, stop() {} };
}
