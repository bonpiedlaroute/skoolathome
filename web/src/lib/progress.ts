// Progress on one item. Same rules as src/skoolathome/progress.py.

import type { ItemType, Rating } from "./types";

export const MAX_SOLO_TRIES = 2; // a wrong answer can be tried again once

export interface Counters {
  solo_right: number;
  solo_wrong: number;
  rating: Rating | null; // latest rating given together
}

export const noProgress = (): Counters => ({ solo_right: 0, solo_wrong: 0, rating: null });

export function soloDone(c: Counters, type: ItemType): boolean {
  if (type === "open") return c.solo_right + c.solo_wrong > 0;
  return c.solo_right > 0 || c.solo_wrong >= MAX_SOLO_TRIES;
}

/** To see together: open questions, and questions missed twice, until rated together. */
export function toReview(c: Counters, type: ItemType): boolean {
  if (c.rating !== null) return false;
  if (type === "open") return soloDone(c, type);
  return c.solo_right === 0 && c.solo_wrong >= MAX_SOLO_TRIES;
}
