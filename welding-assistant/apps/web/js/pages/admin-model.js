// Model management: two tabs, Models (this computer, retraining and the models) and Question pairs. Each tab is drawn the first time it is opened.

import { $, el, pageHeading } from "../core/dom.js";
import { can } from "../core/permissions.js";
import { startPage } from "../components/navbar.js";
import { mountTabs } from "../components/tabs.js";
import { modelsTab } from "../admin/model-models.js";
import { pairsTab } from "../admin/model-pairs.js";

startPage({ active: "model", screen: "6" }).then(() => {
  const page = $("#pageBody");
  const head = pageHeading("Model management", "The model in use, what this computer can run, and the training set. The question pairs people add are on the second tab.");
  const holder = el("div", "model-page");
  page.append(head, holder);
  const tabs = [{ id: "models", label: "Models", icon: "cube", render: (panel) => modelsTab(panel) }];
  if (can("7")) tabs.push({ id: "pairs", label: "Question pairs", icon: "checklist", render: pairsTab });
  mountTabs(holder, tabs, "models");
});
