// Same cases as test_progress_rules in tests/test_api_together.py.

import { expect, it } from "vitest";

import { soloDone, toReview, type Counters } from "./progress";
import type { ItemType } from "./types";

const c = (solo_right: number, solo_wrong: number, rating: Counters["rating"] = null): Counters => ({
  solo_right,
  solo_wrong,
  rating,
});

const cases: [Counters, ItemType, boolean, boolean][] = [
  [c(0, 0), "number", false, false],
  [c(0, 1), "number", false, false],
  [c(1, 1), "number", true, false],
  [c(0, 2), "number", true, true],
  [c(0, 2, "partial"), "number", true, false],
  [c(1, 0), "open", true, true],
  [c(1, 0, "good"), "open", true, false],
  [c(0, 0, "bad"), "choice", false, false],
];

it.each(cases)("%o on a %s item: done %s, to review %s", (counters, type, done, review) => {
  expect(soloDone(counters, type)).toBe(done);
  expect(toReview(counters, type)).toBe(review);
});
