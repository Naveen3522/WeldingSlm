// "Upload Word table": pick a .docx, the server reads and checks its table (nothing is saved), and "Use this table" puts it in the editor.
// A file that fails the check shows the reason and cannot be used: not a real Word file, password protected, no table, or larger than 5 MB.

import { el } from "../core/dom.js";
import { openDialog } from "../components/dialog.js";
import { notice } from "../components/notice.js";

export function uploadWordTable(onUse) {
  const body = el("div", "wordup");
  const pick = el("input");
  pick.type = "file";
  pick.accept = ".xlsx";
  pick.setAttribute("aria-label", "Excel file");
  const out = el("div", "wordup__out");
  body.append(el("p", "hint", "Choose an Excel file. The first row is the column titles and the first column holds the field names."), pick, out);
  let found = null;
  let use = null;

  pick.onchange = async () => {
    found = null;
    if (use) use.disabled = true;
    const file = pick.files[0];
    if (!file) return;
    out.replaceChildren(el("span", "hint", "Checking the file..."));
    const form = new FormData();
    form.append("file", file, file.name);
    let result;
    try {
      const response = await fetch("/api/templates/upload", { method: "POST", body: form });
      result = { ok: response.ok, data: await response.json().catch(() => ({})) };
    } catch (error) {
      result = { ok: false, data: { error: "Server not reachable." } };
    }
    if (!result.ok) return out.replaceChildren(notice("error", result.data.error || "That file cannot be used."));
    found = result.data;
    out.replaceChildren(notice("success", found.report.found), ...found.report.warnings.map((w) => notice("warn", w)));
    if (use) use.disabled = false;
  };

  const dialog = openDialog({
    title: "Upload an Excel table",
    body,
    actions: [
      { label: "Cancel" },
      { label: "Use this table", primary: true, onClick: (close) => found && (onUse(found.heads, found.rows), close()) },
    ],
  });
  use = [...dialog.node.querySelectorAll("button")].find((b) => b.textContent === "Use this table");
  use.disabled = true;
  return dialog;
}
