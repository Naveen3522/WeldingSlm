// What the signed-in person may see and do, as the server worked it out from their level (GET /api/auth/me).
// The server checks every request; this is only for showing a link or a control: one the person cannot use is left out, never greyed.
//   can("4")             may they open screen 4 (Drafts)?          can("4.5", "write")   may they use control 4.5 (Delete a draft)?

let current = { screens: {}, controls: {} };

export function setPermissions(permissions) {
  current = permissions && permissions.screens ? permissions : { screens: {}, controls: {} };
}

export function can(id, mode = "read") {
  const entry = current.screens[id] || current.controls[id];
  return Boolean(entry && entry[mode]);
}
