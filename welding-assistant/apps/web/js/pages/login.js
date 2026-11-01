// The sign-in page: the email is the account, the username is only a display name.

import { addEyes } from "../components/password-eye.js";
import { watchField } from "../components/field-note.js";
import { $ } from "../core/dom.js";
import { authCall, safeNext, whoAmI } from "../core/session.js";

const next = () => safeNext(new URLSearchParams(location.search).get("next"));
const error = (text) => ($("#authError").textContent = text);

function note(text) {
  $("#authNote").textContent = text;
  $("#authNote").hidden = !text;
}

const email = watchField($("#authEmail"), { rule: "email" });

async function start() {
  const { user, setup, offline } = await whoAmI();
  if (user && !user.must_change) return location.replace(next());
  if (user) return location.replace("/password.html?force=1");
  if (offline) error("Server not reachable.");
  if (setup) note("No accounts exist yet. Create an account, or on the server computer run: python scripts/create_admin.py");
  else if (new URLSearchParams(location.search).get("reset")) note("Your password was changed. Sign in with the new one.");
  $("#authEmail").focus();
}

// After too many wrong passwords the account waits (15 seconds, then longer): show the seconds counting down and keep the button off until it ends.
let countdown = null;
function lockFor(seconds) {
  clearInterval(countdown);
  const until = Date.now() + seconds * 1000;
  $("#authSubmit").disabled = true;
  const tick = () => {
    const left = Math.ceil((until - Date.now()) / 1000);
    if (left <= 0) {
      clearInterval(countdown);
      error("");
      $("#authSubmit").disabled = false;
      return;
    }
    error(`Too many wrong attempts. Try again in ${left} second${left === 1 ? "" : "s"}.`);
  };
  tick();
  countdown = setInterval(tick, 500);
}

$("#authForm").onsubmit = async (event) => {
  event.preventDefault();
  error("");
  if (!(await email.valid())) return;
  $("#authSubmit").disabled = true;
  const result = await authCall("/api/auth/login", { email: $("#authEmail").value.trim(), password: $("#authPass").value });
  $("#authSubmit").disabled = false;
  if (!result.ok && result.data.wait) return lockFor(result.data.wait);
  if (!result.ok) return error(result.data.error || "Could not sign in.");
  location.replace(result.data.user.must_change ? "/password.html?force=1" : next());
};

start();
addEyes();
