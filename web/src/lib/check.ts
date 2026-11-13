// Deterministic correction of an answer. No network, no model.
//
// Same logic as src/skoolathome/correction.py. Both are run against
// tests/correction/*.json and must give exactly the same results.

import type { Answer, CheckMode, Response } from "./types";

const SPACES = /\s+/g;
const DECIMAL = /^([+-]?)(\d+)(?:[.,](\d+))?$/;
const FRACTION = /^([+-]?\d+)\/([+-]?\d+)$/;

/** Ignore accents, case, extra spaces and the kind of apostrophe. */
export function normalize(text: string): string {
  return text
    .replace(/’/g, "'")
    .replace(/œ/g, "oe")
    .replace(/Œ/g, "OE")
    .normalize("NFD")
    .replace(/\p{Mn}/gu, "")
    .replace(SPACES, " ")
    .trim()
    .toLowerCase();
}

/** An exact rational number: numerator / denominator, denominator > 0. */
type Rational = { n: bigint; d: bigint };

/** '3', '-3', '0,75', '1 250', '3/4' as an exact value; null if it is not a number. */
export function parseNumber(text: string): Rational | null {
  const compact = text.replace(SPACES, "").replace(/−/g, "-"); // U+2212: the typographic minus
  const decimal = DECIMAL.exec(compact);
  if (decimal) {
    const [, sign, whole, fractional = ""] = decimal;
    const n = BigInt(whole + fractional);
    return { n: sign === "-" ? -n : n, d: 10n ** BigInt(fractional.length) };
  }
  const fraction = FRACTION.exec(compact);
  if (fraction) {
    const n = BigInt(fraction[1]);
    const d = BigInt(fraction[2]);
    if (d === 0n) return null;
    return d < 0n ? { n: -n, d: -d } : { n, d };
  }
  return null;
}

const abs = (x: bigint) => (x < 0n ? -x : x);

function sameText(response: Response, accepted: Answer[]): boolean {
  if (Array.isArray(response)) {
    const given = response.map(normalize);
    return accepted.some(
      (a) => Array.isArray(a) && a.length === given.length && a.every((part, i) => normalize(part) === given[i]),
    );
  }
  if (typeof response !== "string") return false;
  const given = normalize(response);
  return accepted.some((a) => typeof a === "string" && normalize(a) === given);
}

/** Is `response` right? `mode` is the item's check mode. */
export function check(
  mode: CheckMode,
  answer: Answer,
  response: Response,
  options: { variants?: Answer[]; tolerance?: string } = {},
): boolean {
  if (mode === "self_assessment") return response === true;
  if (mode === "exact" || mode === "variants" || mode === "choice") {
    return sameText(response, [answer, ...(mode === "choice" ? [] : (options.variants ?? []))]);
  }
  if (mode === "number" || mode === "fraction") {
    if (typeof response !== "string" || typeof answer !== "string") return false;
    const given = parseNumber(response);
    const expected = parseNumber(answer);
    if (given === null || expected === null) return false;
    // given - expected = (gn*ed - en*gd) / (gd*ed), compared without any rounding.
    const difference = abs(given.n * expected.d - expected.n * given.d);
    if (mode === "fraction") return difference === 0n;
    const tolerance = parseNumber(options.tolerance ?? "0") ?? { n: 0n, d: 1n };
    return difference * tolerance.d <= tolerance.n * given.d * expected.d;
  }
  throw new Error(`unknown check mode: ${mode}`);
}
