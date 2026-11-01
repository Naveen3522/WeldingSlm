// Puts a note under a field and keeps it up to date while the person types.
//   watchField(input, { rule: "password", hint: "8+ characters, 1 uppercase, 1 number or symbol" })
// Empty field: the hint (if any). Something typed that breaks the rule: the rule's message, in the error colour. -> { check(), valid() }

import { el } from "../core/dom.js";
import { problem } from "../core/rules.js";

export function watchField(input, { rule, hint = "" }) {
  const note = el("small", "field-note", hint);
  input.closest("label").appendChild(note);
  let bad = "";

  async function check({ force = false } = {}) {
    bad = input.value.length || force ? await problem(rule, input.value) : "";
    note.textContent = bad || hint;
    note.classList.toggle("field-note--bad", Boolean(bad));
    input.classList.toggle("bad", Boolean(bad));
    return !bad;
  }
  input.addEventListener("input", () => check());
  input.addEventListener("blur", () => check());
  return { check, valid: () => check({ force: true }) };
}
