// The screen that shows a new recovery key once: the key is hidden behind dots until the eye is pressed, copy works while it is hidden, a warning says it is lost
// when the person leaves, and one button is the only way forward. showKeyScreen(card, key, { title, next, nextLabel }) replaces what is in the auth card.

import { el, icon, iconButton } from "../core/dom.js";
import { notice } from "./notice.js";

const DOTS = "••••-••••-••••-••••";

export function showKeyScreen(card, key, { title, next, nextLabel }) {
  const brand = card.querySelector(".auth-brand");
  const shown = el("code", "keybox__key", DOTS);
  let visible = false;
  const eye = iconButton("eye", "Show the key", () => {
    visible = !visible;
    shown.textContent = visible ? key : DOTS;
    eye.title = visible ? "Hide the key" : "Show the key";
    eye.setAttribute("aria-label", eye.title);
    eye.replaceChildren(icon(visible ? "eye-off" : "eye"));
  }, "plain");
  const copy = iconButton("copy", "Copy the key", async () => {
    try {
      await navigator.clipboard.writeText(key);
      copy.title = "Copied";
    } catch (error) {
      copy.title = "Show the key and copy it by hand";
    }
    copy.setAttribute("aria-label", copy.title);
  }, "plain");
  const box = el("div", "keybox");
  box.append(shown, el("span", "keybox__tools"));
  box.lastChild.append(eye, copy);

  const go = el("a", "btn primary auth-submit", nextLabel);
  go.href = next;
  const actions = el("div", "auth-actions");
  actions.appendChild(go);
  card.replaceChildren(...(brand ? [brand] : []), el("h1", null, title), box, notice("warn", "Save this key now. It is lost when you leave this page."),
    el("p", "auth-note", "If you forget your password, this key lets you set a new one."), actions);
}
