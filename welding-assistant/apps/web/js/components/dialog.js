// A window over the page: a title, a body, and buttons. Escape or a click outside closes it.

import { el, icon } from "../core/dom.js";

// options: title, body (a node), actions [{label, primary, danger, keepOpen, onClick(close)}], wide. -> {close, node}
export function openDialog({ title, body, actions = [], wide = false, onClose = null }) {
  const back = el("div", "dialog-back");
  const box = el("div", "dialog" + (wide ? " wide" : ""));
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", title);

  const head = el("div", "dialog-head");
  const closeButton = el("button", "ibtn plain");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "Close");
  closeButton.appendChild(icon("close"));
  head.append(el("b", null, title), closeButton);

  const content = el("div", "dialog-body");
  content.appendChild(body);
  const foot = el("div", "dialog-foot");

  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  function close() {
    document.removeEventListener("keydown", onKey);
    back.remove();
    if (onClose) onClose();
  }

  actions.forEach((action) => {
    const node = el("button", "btn" + (action.primary ? " primary" : ""), action.label);
    node.type = "button";
    if (action.danger) node.classList.add("danger");
    node.onclick = async () => {
      node.disabled = true;
      try {
        await (action.onClick ? action.onClick(close) : close());
      } finally {
        node.disabled = false;
      }
    };
    foot.appendChild(node);
  });

  closeButton.onclick = close;
  back.addEventListener("mousedown", (event) => {
    if (event.target === back) close();
  });
  document.addEventListener("keydown", onKey);
  box.append(head, content);
  if (actions.length) box.appendChild(foot);
  back.appendChild(box);
  document.body.appendChild(back);
  const first = box.querySelector("input, select, textarea");
  if (first) first.focus();
  return { close, node: box };
}

// A yes/no question. -> a promise that is true when the person confirms.
export function confirmDialog({ title, text, yes = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    openDialog({
      title,
      body: el("p", null, text),
      onClose: () => resolve(false),                           // closing it any other way is a "no"
      actions: [
        { label: "Cancel" },
        { label: yes, primary: true, danger, onClick: (close) => { resolve(true); close(); } },
      ],
    });
  });
}
