// A show/hide eye on password boxes. A password that is showing turns back into dots when you switch to another browser tab or window,
// so it is never left on screen by accident. (Set HIDE_WHEN_TAB_HIDDEN to false to keep it showing until you click the eye again.)

import { iconButton } from "../core/dom.js";

const HIDE_WHEN_TAB_HIDDEN = true;
const boxes = new Set();

function paint({ input, button }, shown) {
  input.type = shown ? "text" : "password";
  button.title = shown ? "Hide password" : "Show password";
  button.setAttribute("aria-label", button.title);
  button.replaceChildren(iconButton(shown ? "eye-off" : "eye", "").firstChild);
}

// Puts the eye on a password input (wraps the input in place).
export function addEye(input) {
  const wrap = document.createElement("span");
  wrap.className = "pw";
  input.replaceWith(wrap);
  const box = { input, button: null };
  box.button = iconButton("eye", "Show password", () => paint(box, input.type === "password"), "plain");
  wrap.append(input, box.button);
  boxes.add(box);
  paint(box, false);
}

export function addEyes(root = document) {
  root.querySelectorAll('input[type="password"]').forEach((input) => {
    if (!input.parentElement.classList.contains("pw")) addEye(input);
  });
}

document.addEventListener("visibilitychange", () => {
  if (HIDE_WHEN_TAB_HIDDEN && document.hidden) boxes.forEach((box) => paint(box, false));
});
window.addEventListener("blur", () => {
  if (HIDE_WHEN_TAB_HIDDEN) boxes.forEach((box) => paint(box, false));
});
