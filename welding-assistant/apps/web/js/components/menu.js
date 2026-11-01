// A menu that opens under a button: click the button to open or close it, a click anywhere else or Escape closes it. One open menu at a time.

const open = new Set();

function closeAll(except) {
  open.forEach((menu) => {
    if (menu !== except) menu.close();
  });
}

document.addEventListener("click", (event) => {
  open.forEach((menu) => {
    if (!menu.wrap.contains(event.target)) menu.close();
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeAll();
});

// -> { close() }. `wrap` holds both the button and the panel.
export function attachMenu(wrap, button, panel) {
  const menu = {
    wrap,
    close() {
      panel.hidden = true;
      button.setAttribute("aria-expanded", "false");
      button.classList.remove("on");
      open.delete(menu);
    },
  };
  panel.hidden = true;
  button.setAttribute("aria-haspopup", "true");
  button.setAttribute("aria-expanded", "false");
  button.onclick = () => {
    if (!panel.hidden) return menu.close();
    closeAll(menu);
    panel.hidden = false;
    button.setAttribute("aria-expanded", "true");
    button.classList.add("on");
    open.add(menu);
  };
  return menu;
}
