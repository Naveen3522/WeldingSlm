// A small icon button that copies a piece of text (a username, an email, a recovery key). The text can be given as a function, so it is read at the click.

import { iconButton } from "../core/dom.js";

export function copyButton(text, tip = "Copy") {
  const button = iconButton("copy", tip, async () => {
    try {
      await navigator.clipboard.writeText(typeof text === "function" ? text() : text);
      button.title = "Copied";
    } catch (error) {
      button.title = "Copying is not allowed here: select the text and copy it by hand";
    }
    button.setAttribute("aria-label", button.title);
    setTimeout(() => {
      button.title = tip;
      button.setAttribute("aria-label", tip);
    }, 1600);
  }, "plain");
  return button;
}
