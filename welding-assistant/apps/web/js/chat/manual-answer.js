// "Add an answer by hand": a Weld Engineer or Admin writes the answer to a question the documents do not answer. From then on everyone who asks it gets that answer,
// marked "Added manually. Not from the documents. It may not be correct."

import { el } from "../core/dom.js";
import { call } from "../core/api.js";
import { openDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";

export const AREAS = ["General", "Fault Finder", "Procedure Advisor", "Formulas"];

// -> opens the dialog. onSaved() runs after the answer was saved.
export function openManualAnswer({ question = "", area = "General", onSaved = () => {} }) {
  const field = (text, node) => {
    const wrap = el("label", "field");
    wrap.append(el("span", null, text), node);
    return wrap;
  };
  const asked = el("input");
  asked.maxLength = 300;
  asked.value = question;
  const answer = el("textarea");
  answer.rows = 4;
  answer.maxLength = 1500;
  const where = el("select");
  AREAS.forEach((name) => {
    const option = el("option", null, name);
    option.value = name;
    where.appendChild(option);
  });
  where.value = AREAS.includes(area) ? area : "General";
  const problem = el("div");
  const form = el("div", "fe-form");
  form.append(notice("warn", "Not from the documents. It may not be correct."), field("Question", asked), field("Answer", answer), field("Area", where), problem);
  openDialog({
    title: "Add an answer by hand",
    body: form,
    actions: [
      { label: "Cancel" },
      {
        label: "Save",
        primary: true,
        onClick: async (close) => {
          const result = await call("POST", "/api/answers/manual", { question: asked.value, answer: answer.value, area: where.value });
          if (!result.ok) return problem.replaceChildren(notice("error", result.data.error || "That did not work."));
          close();
          onSaved();
        },
      },
    ],
  });
}
