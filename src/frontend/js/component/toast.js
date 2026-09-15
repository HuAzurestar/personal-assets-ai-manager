import { $, $$ } from "../util/core.js";

export function toast(text, error = false) {
  $(".toast")?.remove();
  const node = document.createElement("div");
  node.className = `toast${error ? " error" : ""}`;
  node.textContent = text;
  const openDialogs = $$("dialog[open]");
  (openDialogs.at(-1) || document.body).append(node);
  setTimeout(() => node.remove(), 4500);
}
