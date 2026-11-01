// The pager under a long list. mountPager(container, { sizes: [25, 50, 100], size: 25, onChange({ offset, limit }) }) -> { setTotal(total), reset() }.
// The page box takes digits only; a number outside 1 to N, or an empty box, is refused with "Enter 1 to N" and the box goes back to the current page.
// Up and Down in the box step a page; first and previous are off on page 1, next and last on the last page. Changing the rows per page returns to page 1.

import { el, icon, iconButton } from "../core/dom.js";

export function mountPager(container, { sizes = [25, 50, 100], size = 25, page: start = 1, onChange }) {
  let page = Math.max(1, start);
  let total = 0;
  let rows = size;
  const pages = () => Math.max(1, Math.ceil(total / rows));

  const select = el("select");
  select.setAttribute("aria-label", "Rows per page");
  sizes.forEach((n) => select.appendChild(el("option", null, String(n))));
  if (!sizes.includes(size)) sizes = [...sizes, size].sort((a, b) => a - b);
  select.replaceChildren(...sizes.map((n) => el("option", null, String(n))));
  select.value = String(size);
  const sizeBox = el("label", "pager__size");
  sizeBox.append("Rows per page", select);

  const first = iconButton("first-page", "First page", () => go(1));
  const prev = iconButton("chevron-left", "Previous page", () => go(page - 1));
  const next = iconButton("chevron-right", "Next page", () => go(page + 1));
  const last = iconButton("last-page", "Last page", () => go(pages()));
  const box = el("input", "pager__box");
  box.type = "text";
  box.inputMode = "numeric";
  box.autocomplete = "off";
  box.setAttribute("aria-label", "Page number");
  const of = el("span", "pager__of");
  const message = el("span", "pager__message");
  message.setAttribute("role", "status");
  const nav = el("div", "pager__nav");
  nav.append(first, prev, box, of, next, last);
  const root = el("div", "pager");
  root.append(sizeBox, nav, message);
  container.replaceChildren(root);

  function show() {
    const n = pages();
    box.value = String(page);
    of.textContent = "of " + n;
    first.disabled = prev.disabled = page <= 1;
    next.disabled = last.disabled = page >= n;
  }

  function go(n) {
    const target = Math.min(Math.max(1, n), pages());
    message.textContent = "";
    if (target === page) return show();
    page = target;
    show();
    onChange({ offset: (page - 1) * rows, limit: rows });
  }

  function commit() {
    const n = parseInt(box.value, 10);
    if (!n || n < 1 || n > pages()) {
      message.textContent = "Enter 1 to " + pages();
      box.value = String(page);
      return;
    }
    go(n);
  }

  box.addEventListener("input", () => {
    box.value = box.value.replace(/\D/g, "").replace(/^0+/, "");
    message.textContent = box.value && Number(box.value) > pages() ? "Enter 1 to " + pages() : "";
  });
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter") commit();
    else if (event.key === "ArrowUp") (event.preventDefault(), go(page + 1));
    else if (event.key === "ArrowDown") (event.preventDefault(), go(page - 1));
  });
  box.addEventListener("blur", () => {
    if (box.value !== String(page)) commit();
  });
  select.onchange = () => {
    rows = Number(select.value);
    page = 1;
    show();
    onChange({ offset: 0, limit: rows });
  };

  show();
  return {
    setTotal(count) {
      total = count;
      if (page > pages()) page = pages();
      show();
    },
    reset() {
      page = 1;
      show();
    },
    limit: () => rows,
    page: () => page,
  };
}
