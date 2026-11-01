// A notice (see css/components/notice.css): notice(kind, text, { closable }) -> the element. kind is "info", "success", "warn" or "error". A closable one has a cross that removes it.
// The text is put in with textContent, so a message can never carry markup.

import { el, icon } from "../core/dom.js";

const ICONS = { info: "info", success: "circle-check", warn: "warn", error: "circle-error" };

export function notice(kind, text, { closable = false } = {}) {
  const node = el("div", "notice notice--" + kind);
  node.setAttribute("role", kind === "error" || kind === "warn" ? "alert" : "status");
  node.append(icon(ICONS[kind] || "info"), el("span", null, text));
  if (closable) {
    const close = el("button", "notice__close");
    close.type = "button";
    close.title = "Close";
    close.setAttribute("aria-label", "Close");
    close.appendChild(icon("close"));
    close.onclick = () => node.remove();
    node.appendChild(close);
  }
  return node;
}
