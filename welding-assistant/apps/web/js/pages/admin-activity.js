// Administration: Activity.

import { $ } from "../core/dom.js";
import { startPage } from "../components/navbar.js";
import { activityPage } from "../admin/activity.js";

startPage({ active: "activity", screen: "11" }).then(() => activityPage($("#pageBody")));
