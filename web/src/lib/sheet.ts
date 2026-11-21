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

/** How long a requested sheet lasts. From 30 minutes: a test preparation, in parts, for paper too. */
export const DURATIONS: [number, string][] = [
  [10, "10 minutes"],
  [20, "20 minutes"],
  [30, "30 minutes"],
  [45, "45 minutes"],
  [60, "1 heure"],
];

/** A sheet whose time has passed: its date is gone, or a week old without one. Shown apart. */
export function isPast(sheet: { visible_from: string; due_by: string | null }, today: string): boolean {
  if (sheet.due_by) return sheet.due_by < today;
  const shown = new Date(`${sheet.visible_from}T12:00:00`);
  shown.setDate(shown.getDate() + 7);
  return shown.toISOString().slice(0, 10) < today;
}

/** Subjects a parent can ask a sheet for: École Directe code, name. */
export const SUBJECTS: [string, string][] = [
  ["MATHS", "Mathématiques"],
  ["FRANC", "Français"],
  ["HI-GE", "Histoire-géographie"],
  ["AGL1", "Anglais"],
  ["ESP", "Espagnol"],
  ["SVT", "SVT"],
  ["PH-CH", "Physique-chimie"],
  ["TECHN", "Technologie"],
  ["LCALA", "Latin"],
];

export const subjectName = (code: string): string => SUBJECTS.find(([c]) => c === code)?.[1] ?? code;
