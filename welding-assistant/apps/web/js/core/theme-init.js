// Runs before the page paints so the saved theme never flashes the wrong colours.
try {
  const saved = localStorage.getItem("weldassist.theme");
  if (saved === "light" || saved === "dark") {
    document.documentElement.setAttribute("data-theme", saved);
  }
} catch (error) {
  // Storage can be blocked; the system theme is used then.
}
