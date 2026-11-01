// Forgot password, in two steps: the email and the recovery key saved earlier, then a new password. A fresh key is shown afterwards.
// A wrong key says so and, only then, that an Admin can reset the password.

import { addEyes } from "../components/password-eye.js";
import { watchField } from "../components/field-note.js";
import { showKeyScreen } from "../components/key-screen.js";
import { notice } from "../components/notice.js";
import { $ } from "../core/dom.js";
import { authCall } from "../core/session.js";

const error = (text) => ($("#authError").textContent = text);
const email = watchField($("#authEmail"), { rule: "email" });
const password = watchField($("#authNew"), { rule: "password", hint: "8+ characters, 1 uppercase, 1 number or symbol" });
let token = "";

async function stepOne() {
  if (!(await email.valid())) return;
  if (!$("#authKey").value.trim()) return error("Enter your recovery key.");
  $("#authSubmit").disabled = true;
  const result = await authCall("/api/auth/check-key", { email: $("#authEmail").value.trim(), key: $("#authKey").value });
  $("#authSubmit").disabled = false;
  if (!result.ok) {
    const wrong = result.data.code === "bad_key" && !/Too many/.test(result.data.error || "");
    error(wrong ? "That key is not right. You can try again." : result.data.error || "Could not check the key.");
    if (wrong) $("#authHelp").replaceChildren(notice("info", "No key? Contact an Admin. They can reset your password."));
    return;
  }
  token = result.data.token;
  $("#authHelp").replaceChildren(notice("success", "Key accepted."));
  $("#authTitle").textContent = "Choose a new password";
  $("#authNote").textContent = "You will be given a new recovery key after this.";
  $("#stepOne").hidden = true;
  $("#stepTwo").hidden = false;
  $("#authSubmit").textContent = "Save password";
  $("#authNew").focus();
}

async function stepTwo() {
  if (!(await password.valid())) return;
  if ($("#authNew").value !== $("#authNew2").value) return error("The two passwords do not match.");
  $("#authSubmit").disabled = true;
  const result = await authCall("/api/auth/reset-token", { token, password: $("#authNew").value });
  $("#authSubmit").disabled = false;
  if (!result.ok) return error(result.data.error || "Could not save the password.");
  showKeyScreen($("#authForm"), result.data.recovery_key, { title: "Save your new recovery key", next: "/login.html?reset=1", nextLabel: "I saved it, sign in" });
}

$("#authForm").onsubmit = async (event) => {
  event.preventDefault();
  error("");
  await (token ? stepTwo() : stepOne());
};

$("#authEmail").focus();
addEyes();
