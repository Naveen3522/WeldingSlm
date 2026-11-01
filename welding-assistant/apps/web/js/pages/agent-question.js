// The guided question on the Agents page: what a person picks in the dropdowns (every one starts empty) and types under "Details" becomes one plain question.
// Only what is chosen is used, so "problem: cracking" alone gives "What are the causes and corrective actions of cracking?". Nothing chosen and nothing typed gives "" (the button stays off).

const list = (parts) => parts.filter(Boolean);
const tail = (details) => (details && details.trim() ? " Additional details: " + details.replace(/\s+/g, " ").trim().replace(/[.?!]+$/, "") + "." : "");

export const FIELDS = {
  "fault-finder": [
    { id: "process", label: "Process", options: ["MIG", "TIG", "stick (MMA)", "spot", "submerged arc"] },
    { id: "material", label: "Material", options: ["steel", "stainless steel", "aluminium"] },
    { id: "problem", label: "Problem", options: ["porosity", "cracking", "spatter", "undercut", "distortion", "lack of fusion"] },
  ],
  "procedure-advisor": [
    { id: "task", label: "I need to", options: [["process", "Choose a welding process"], ["wps", "Write a procedure"], ["standard", "Find the standard that applies"], ["qualify", "Qualify a welder or procedure"]] },
    { id: "material", label: "Material", options: ["steel", "stainless steel", "aluminium"] },
    { id: "thickness", label: "Thickness (mm)", number: true },
    { id: "industry", label: "Industry", options: ["structural steel", "pressure equipment", "automotive", "rail"] },
  ],
};

// The choices made from the documents at the last reindex (GET /api/agents/options) replace the built-in ones above; a list the documents gave nothing for keeps the built-in one.
export function applyOptions(found) {
  const set = (agent, id, names) => {
    const spec = FIELDS[agent].find((f) => f.id === id);
    if (spec && Array.isArray(names) && names.length) spec.options = names;
  };
  set("fault-finder", "process", found.processes);
  set("fault-finder", "material", found.materials);
  set("fault-finder", "problem", found.problems);
  set("procedure-advisor", "material", found.materials);
  set("procedure-advisor", "industry", found.industries);
}

export function buildQuestion(agent, f) {
  const details = tail((f.details || "").trim());
  const nothing = !list(Object.entries(f).filter(([k]) => k !== "details").map(([, v]) => v)).length;
  if (nothing && !details.trim()) return "";
  if (agent === "fault-finder") {
    const name = (f.process || "").replace(/\s*\(.*?\)/g, "").trim();         // "stick (MMA)" is asked as "stick": the brackets only confuse the search
    const where = (name ? ` in ${name} welding` : "") + (f.material ? (name ? ` of ${f.material}` : ` in ${f.material}`) : "");
    // one question, not two: "and how do I stop it" would be searched as a second question on its own, and the table of causes and corrective actions holds both together
    if (f.problem) return `What are the causes and corrective actions of ${f.problem}${where}?${details}`;
    if (!(f.details || "").trim()) return `What are the common weld defects${where}, with their causes and corrective actions?`;          // nothing said about the problem: the table of defects
    return `I have a weld problem${where}. What could be causing it?${details}`;
  }
  const mat = f.material ? ` for ${f.material}${f.thickness ? ` ${f.thickness} mm thick` : ""}` : f.thickness ? ` for ${f.thickness} mm material` : "";
  const area = f.industry ? ` in the ${f.industry} industry` : "";
  const ask = {
    process: `Which welding process should I use${mat}${area}?`,
    wps: `What should a welding procedure specification${mat}${area} contain?`,
    standard: `Which standard applies to welding${mat}${area}?`,
    qualify: `How do I qualify a welder or a procedure${mat}${area}?`,
  }[f.task];
  return (ask || `What should I know about welding${mat}${area}?`) + details;
}
