// The password, email and username rules the server uses (GET /api/auth/rules), so a page can say what is wrong while the person types.
// The server checks everything again; this is only for the message under the field.

let loaded = null;

function build(spec) {
  try {
    return { ...spec, regex: new RegExp(spec.pattern) };
  } catch (error) {
    return { ...spec, regex: null };
  }
}

export function loadRules() {
  if (!loaded) {
    loaded = fetch("/api/auth/rules")
      .then((response) => response.json())
      .then((data) => Object.fromEntries(Object.entries(data.rules || {}).map(([name, spec]) => [name, build(spec)])))
      .catch(() => ({}));
  }
  return loaded;
}

// -> "" when the value is fine, otherwise the rule's message.
export async function problem(name, value) {
  const rule = (await loadRules())[name];
  if (!rule) return "";
  const text = String(value || "").trim();
  if (!text) return rule.required || "";
  return rule.regex && !rule.regex.test(name === "password" ? String(value) : text) ? rule.message : "";
}

export async function message(name) {
  const rule = (await loadRules())[name];
  return rule ? rule.message : "";
}
