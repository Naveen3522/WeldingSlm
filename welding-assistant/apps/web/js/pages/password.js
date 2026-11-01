// Change password: for anyone signed in, and the one screen a person with a one-time password is sent to. A fresh recovery key is shown afterwards.

import { addEyes } from "../components/password-eye.js";
import { watchField } from "../components/field-note.js";
import { showKeyScreen } from "../components/key-screen.js";
import { $ } from "../core/dom.js";
import { authCall, whoAmI } from "../core/session.js";

const forced = new URLSearchParams(location.search).get("force") === "1";
const error = (text) => ($("#authError").textContent = text);
const password = watchField($("#authNew"), { rule: "password", hint: "8+ characters, 1 uppercase, 1 number or symbol" });

async function start() {
  const { user } = await whoAmI();
  if (!user) return location.replace("/login.html?next=/password.html");
  if (forced || user.must_change) {
    $("#authTitle").textContent = "Choose a new password";
    $("#authPassLabel").textContent = "One-time password";
    $("#authNote").textContent = "You are using a one-time password from an Admin. Choose your own to continue.";
    $("#authNote").hidden = false;
    $("#authCancel").hidden = true;
  }
  $("#authPass").focus();
}

$("#authForm").onsubmit = async (event) => {
  event.preventDefault();
  error("");
  if (!(await password.valid())) return;
  if ($("#authNew").value !== $("#authNew2").value) return error("The two passwords do not match.");
  $("#authSubmit").disabled = true;
  const result = await authCall("/api/auth/change-password", { old: $("#authPass").value, new: $("#authNew").value });
  $("#authSubmit").disabled = false;
  if (!result.ok) return error(result.data.error || "Could not change the password.");
  if (!result.data.recovery_key) return location.replace("/");
  showKeyScreen($("#authForm"), result.data.recovery_key, { title: "Save your new recovery key", next: "/", nextLabel: "I saved it, continue" });
};

start();
addEyes();
