// The Formulas page.

import { initFormulas, showFormulas } from "../formulas/formulas.js";
import { startPage } from "../components/navbar.js";

startPage({ active: "formulas", screen: "3" }).then((user) => {
  initFormulas(user);
  showFormulas(true);
});
