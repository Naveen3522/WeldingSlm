// Model management, Models tab, "This system": what this computer has (graphics memory, memory, processor, disk) and what it can run and retrain. Optional: models work
// without it; running it only adds a Fit to each model in the table and a line of advice.

import { el, icon } from "../core/dom.js";
import { call } from "../core/api.js";
import { notice } from "../components/notice.js";

const when = (seconds) => new Date(seconds * 1000).toLocaleString([], { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

function tile(label, value, detail, share) {
  const box = el("div", "spec");
  box.append(el("small", null, label), el("b", null, value), el("span", null, detail));
  const meter = el("div", "meter");
  const fill = el("i");
  fill.style.width = Math.max(0, Math.min(100, share)) + "%";
  meter.appendChild(fill);
  box.appendChild(meter);
  return box;
}

// -> { node, reload } ; onChange() is called after a check so the table can show each model's fit.
export function systemSection({ onChange = () => {} } = {}) {
  const section = el("section", "section");
  const head = el("div", "model__cardhead");
  const title = el("div");
  const sub = el("p", "sub");
  title.append(el("h2", null, "This system"), sub);
  const scan = el("button", "btn primary");
  scan.type = "button";
  head.append(title, scan);
  const body = el("div");
  body.style.cssText = "display:flex;flex-direction:column;gap:12px";
  section.append(head, body);

  function draw(data) {
    scan.replaceChildren(icon("sync"), document.createTextNode(data.specs ? "Check again" : "Check this system"));
    sub.textContent = "";
    if (!data.specs) return body.replaceChildren();
    const s = data.specs;
    const card = s.gpu ? s.gpu.total_mb / 1024 : 0;
    const gpuFree = s.gpu ? s.gpu.free_mb / 1024 : 0;
    const tiles = el("div", "specs");
    tiles.append(
      tile("Graphics memory", s.gpu ? `${card.toFixed(0)} GB` : "None", s.gpu ? s.gpu.name : "No NVIDIA graphics card was found", s.gpu ? 100 * (1 - gpuFree / card) : 0),
      tile("Memory", `${s.ram_gb.toFixed(0)} GB`, `${s.ram_free_gb.toFixed(0)} GB free`, 100 * (1 - s.ram_free_gb / (s.ram_gb || 1))),
      tile("Processor", `${s.threads} threads`, s.cpu, 100),
      tile("Disk space", `${s.disk_free_gb.toFixed(0)} GB`, "free", Math.min(100, s.disk_free_gb / 5)),
    );
    const advice = el("div", "advice");
    advice.append(icon("lightbulb"), el("span", null, data.advice));
    body.replaceChildren(tiles, advice);
  }

  async function load() {
    const result = await call("GET", "/api/admin/system");
    if (!result.ok) return body.replaceChildren(notice("error", result.data.error || "The system details could not be loaded."));
    draw(result.data);
  }

  scan.onclick = async () => {
    scan.disabled = true;
    const result = await call("POST", "/api/admin/system/scan");
    scan.disabled = false;
    if (!result.ok) return body.replaceChildren(notice("error", result.data.error || "The check did not run."));
    draw(result.data);
    onChange();
  };

  load();
  return { node: section, reload: load };
}
