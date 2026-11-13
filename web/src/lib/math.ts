// Text with formulas: what is between two $ signs is typeset by KaTeX.

import katex from "katex";

const escapeHtml = (text: string) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** Safe HTML for a prompt: plain text is escaped, formulas are rendered. */
export function renderText(text: string): string {
  return text
    .split("$")
    .map((part, index) => {
      if (index % 2 === 0) return escapeHtml(part).replace(/\n/g, "<br>");
      try {
        return katex.renderToString(part, { throwOnError: false, output: "html" });
      } catch {
        return escapeHtml(part);
      }
    })
    .join("");
}
