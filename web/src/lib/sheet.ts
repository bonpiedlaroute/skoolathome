// What a sheet says about itself: its format, where it comes from.

import type { Item, Sheet } from "./types";

export const FORMAT_LABELS: Record<string, string> = {
  exercises: "Exercices",
  lesson_recap: "Cours en clair",
  quick_quiz: "Interro flash",
  test_revision: "Révision",
};

export const formatLabel = (type: string): string => FORMAT_LABELS[type] ?? "Fiche";

/** « D'après ton cours du mardi 4/11 et ton manuel p. 86 », or "" for a sheet written by hand. */
export function basedOn(sheet: Pick<Sheet, "sources" | "source_labels">): string {
  const labels = sheet.sources.map((key) => sheet.source_labels?.[key]).filter((label): label is string => !!label);
  const unique = [...new Set(labels)];
  if (unique.length === 0) return "";
  const listed = unique.length === 1 ? unique[0] : `${unique.slice(0, -1).join(", ")} et ${unique[unique.length - 1]}`;
  return `D'après ${listed}`;
}

/** Where a question's quote comes from: « ton manuel p. 86 », « ton cours du mardi 4/11 ». */
export function quoteSource(sheet: Pick<Sheet, "source_labels">, item: Pick<Item, "quote">): string {
  return (item.quote && sheet.source_labels?.[item.quote.source_id]) || "ton cours";
}
