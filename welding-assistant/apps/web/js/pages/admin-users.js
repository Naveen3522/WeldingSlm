// Administration: Users, with three tabs: People, Roles and Permissions.

import { $, el, pageHeading } from "../core/dom.js";
import { can } from "../core/permissions.js";
import { startPage } from "../components/navbar.js";
import { mountTabs } from "../components/tabs.js";
import { peoplePanel } from "../admin/users-people.js";
import { rolesPanel } from "../admin/users-roles.js";
import { permissionsPanel } from "../admin/users-permissions.js";

startPage({ active: "users", screen: "8" }).then((user) => {
  const page = $("#pageBody");
  page.appendChild(pageHeading("Users", "The people who can sign in, the roles they have, and what each role may do."));
  const tabs = [{ id: "people", label: "People", icon: "users", render: (panel) => peoplePanel(panel, user) }];
  if (can("9")) tabs.push({ id: "roles", label: "Roles", icon: "badge", render: rolesPanel });
  if (can("10")) tabs.push({ id: "permissions", label: "Permissions", icon: "tune", render: permissionsPanel });
  mountTabs(page, tabs, "people");
});
