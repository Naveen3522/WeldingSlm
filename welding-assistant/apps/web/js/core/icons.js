// The icon names the pages use, and the Google Material Symbols icon each one is. The font is served from /vendor/material-symbols (no internet needed).
// An unknown name is taken to be a Material Symbols name already, so a new page can use one directly after it is added to the font (see vendor/material-symbols/README.md).

const NAMES = {
  brand: "bolt", bolt: "bolt", info: "info", warn: "warning", check: "check", "circle-check": "check_circle", "circle-error": "error", close: "close",
  plus: "add", minus: "remove", trash: "delete", edit: "edit", pen: "edit", undo: "undo", refresh: "refresh", history: "history", play: "play_arrow",
  download: "download", upload: "upload", "file-download": "file_download", "file-upload": "upload_file", copy: "content_copy", list: "list", doc: "description",
  word: "article", quote: "format_quote", link: "link", search: "search", eye: "visibility", "eye-off": "visibility_off", lock: "lock", unlock: "lock_open",
  key: "key", shield: "shield", ban: "block", user: "person", "user-plus": "person_add", users: "group", account: "account_circle", signout: "logout", menu: "menu",
  sun: "light_mode", moon: "dark_mode", theme: "contrast", robot: "smart_toy", brain: "psychology", book: "menu_book", agents: "grid_view", chat: "forum",
  calculator: "calculate", table: "table_chart", wrench: "build", compass: "explore", "file-pen": "edit_document", scale: "balance",
  "arrow-up": "arrow_upward", "arrow-right": "arrow_forward", "arrow-left": "arrow_back", "chevron-down": "expand_more", "chevron-right": "chevron_right",
  "chevron-left": "chevron_left", "first-page": "first_page", "last-page": "last_page", sparkles: "auto_awesome", gear: "settings", stop: "stop", pause: "pause",
  save: "save", forward: "skip_next", spinner: "progress_activity", tune: "tune", badge: "badge", cube: "deployed_code", science: "science", checklist: "checklist",
  stethoscope: "stethoscope", troubleshoot: "troubleshoot", sync: "sync", cloud: "cloud_upload", dot: "circle", sliders: "tune", "square-check": "check_box", square: "check_box_outline_blank",
  lightbulb: "lightbulb", draft: "draft", message: "chat_bubble", task: "task", equal: "equal", "view-column": "view_column", "table-rows": "table_rows",
  "unfold": "unfold_more",
};

// -> the Material Symbols name (the text of the icon element), e.g. "delete".
export function iconGlyph(name) {
  return NAMES[name] || name;
}
