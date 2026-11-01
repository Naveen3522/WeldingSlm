// Create an account: username, email, password twice. Always a User. The recovery key is shown once, then the person is already signed in.

import { addEyes } from "../components/password-eye.js";
import { watchField } from "../components/field-note.js";
import { showKeyScreen } from "../components/key-screen.js";
import { $ } from "../core/dom.js";
import { authCall } from "../core/session.js";

const error = (text) => ($("#authError").textContent = text);
const username = watchField($("#authUser"), { rule: "username" });
const email = watchField($("#authEmail"), { rule: "email" });
const password = watchField($("#authNew"), { rule: "password", hint: "8+ characters, 1 uppercase, 1 number or symbol" });

$("#authForm").onsubmit = async (event) => {
  event.preventDefault();
  error("");
  const fine = (await Promise.all([username.valid(), email.valid(), password.valid()])).every(Boolean);
  if (!fine) return;
  if ($("#authNew").value !== $("#authNew2").value) return error("The two passwords do not match.");
  $("#authSubmit").disabled = true;
  const result = await authCall("/api/auth/signup", { username: $("#authUser").value.trim(), email: $("#authEmail").value.trim(), password: $("#authNew").value });
  $("#authSubmit").disabled = false;
  if (!result.ok) return error(result.data.error || "Could not create the account.");
  showKeyScreen($("#authForm"), result.data.recovery_key, { title: "Save your recovery key", next: "/", nextLabel: "I saved it, continue" });
};

$("#authUser").focus();
addEyes();
