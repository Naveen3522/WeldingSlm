// The signed-in user and what they may do, for a page. A page that needs one calls requireSession(); nobody signed in goes to the sign-in page and comes back afterwards.

import { setPermissions, can } from "./permissions.js";

const realFetch = window.fetch.bind(window);

// Only a path on this site is accepted as the place to return to.
export function safeNext(value) {
  return typeof value === "string" && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/login") ? value : "/";
}

export function goToLogin() {
  location.replace("/login.html?next=" + encodeURIComponent(location.pathname + location.search));
}

// A request answered with 401 later (the session ended) sends the person back to sign in; the page they were on is remembered.
function watchSessionEnd() {
  window.fetch = async (...args) => {
    const response = await realFetch(...args);
    const url = String((args[0] && args[0].url) || args[0]);
    if (response.status === 401 && !url.startsWith("/api/auth/")) goToLogin();
    return response;
  };
}

export async function whoAmI() {
  try {
    const response = await realFetch("/api/auth/me");
    const data = await response.json().catch(() => ({}));
    return { user: response.ok ? data.user : null, permissions: response.ok ? data.permissions : null, setup: Boolean(data.setup) };
  } catch (error) {
    return { user: null, setup: false, offline: true };
  }
}

// -> the user. `screen` is the id of the screen the page is (the person must be allowed to read it) and `level` the older way: the lowest access level.
// Redirects (and never returns in practice) when the page may not be shown to this person.
export async function requireSession({ level = 1, screen = "" } = {}) {
  const { user, permissions } = await whoAmI();
  if (!user) {
    goToLogin();
    return new Promise(() => {});
  }
  if (user.must_change) {
    location.replace("/password.html?force=1");
    return new Promise(() => {});
  }
  setPermissions(permissions);
  if (user.level < level || (screen && !can(screen))) {
    location.replace("/");
    return new Promise(() => {});
  }
  watchSessionEnd();
  return user;
}

export async function signOut() {
  await realFetch("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  location.replace("/login.html");
}

// POST JSON to an /api/auth route without the session-end redirect. -> {ok, data}
export async function authCall(url, body) {
  try {
    const response = await realFetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    return { ok: response.ok, data: await response.json().catch(() => ({})) };
  } catch (error) {
    return { ok: false, data: { error: "Server not reachable." } };
  }
}
