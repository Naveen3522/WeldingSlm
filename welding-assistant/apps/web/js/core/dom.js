// Small helpers for building DOM nodes. Text is always set as text, never as markup.

import { iconGlyph } from "./icons.js";
import { CODEPOINTS } from "./icon-codepoints.js";

export const $ = (selector) => document.querySelector(selector);

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

// An icon (Google Material Symbols, served locally). `name` is one of the names in icons.js; `solid` fills it in.
export function icon(name, solid = false) {
  const node = document.createElement("i");
  const glyph = iconGlyph(name);
  node.className = "ms" + (solid ? " fill" : "");
  node.dataset.icon = glyph;
  node.textContent = CODEPOINTS[glyph] ? String.fromCodePoint(CODEPOINTS[glyph]) : "";
  node.setAttribute("aria-hidden", "true");
  return node;
}

// A <button type="button"> with optional icon, class name, title and click handler.
export function button(label, { onClick, className, title, iconName, solid } = {}) {
  const node = el("button", className);
  node.type = "button";
  if (iconName) node.appendChild(icon(iconName, solid));
  node.appendChild(document.createTextNode(label));
  if (title) node.title = title;
  if (onClick) node.onclick = onClick;
  return node;
}

export function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// "37_Welding_Safety.docx" -> "Welding Safety"
export function niceTitle(fileName) {
  return fileName.replace(/\.[^.]+$/, "").replace(/^\d+_/, "").replace(/_/g, " ");
}

// "Chapter > Section" -> "Section"
export function lastSection(heading) {
  return (heading || "").split(" > ").slice(-1)[0];
}

export function documentName(source) {
  return source.replace(/\.[^.]+$/, "");
}

// A small icon-only button with a tooltip (title) and a label for screen readers.
// The top of a page: its heading, one description (full sentences, a full stop between them), and a line under both.
export function pageHeading(title, description) {
  const node = el("header", "page-heading");
  node.append(el("h1", null, title), el("p", "page-desc", description));
  return node;
}

export function iconButton(iconName, tip, onClick, className = "") {
  const node = el("button", "ibtn " + className);
  node.type = "button";
  node.title = tip;
  node.setAttribute("aria-label", tip);
  node.appendChild(icon(iconName));
  if (onClick) node.onclick = onClick;
  return node;
}
