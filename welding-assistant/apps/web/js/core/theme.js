// Light and dark mode. Follows the system until the user picks one with the toggle.

import { $ } from "./dom.js";
import { save } from "./store.js";

const THEME_KEY = "weldassist.theme";

function currentTheme() {
  const chosen = document.documentElement.getAttribute("data-theme");
  if (chosen) return chosen;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function initTheme() {
  $("#themeBtn").onclick = () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    save(THEME_KEY, next);
  };
}
