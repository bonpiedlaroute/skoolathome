// The home page's choices, from the device's own clock: the page is computed by the laptop after a
// collection (Sunday evening), and must still be right on Monday morning.

import type { HomeGrade, HomePage, HomeSuggestion, HomeTest } from "./types";

export const TEST_DAYS = 3; // tests shown until D+3

const DAY = 86_400_000;

/** « 2026-11-16 », in local time (not UTC: at 00:30 it is already tomorrow here). */
export function isoDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const noon = (iso: string) => new Date(`${iso}T12:00:00`);

/** Whole days from `today` to `iso`: 0 today, 1 tomorrow. */
export function daysFrom(today: string, iso: string): number {
  return Math.round((noon(iso).getTime() - noon(today).getTime()) / DAY);
}

/** The first day after today with a lesson that is not cancelled: tomorrow, or Monday on a Friday. */
export function nextSchoolDay(page: HomePage, today: string): string | null {
  const off = new Set(page.holidays);
  const days = page.slots.filter((s) => s.day > today && !s.cancelled && !off.has(s.day)).map((s) => s.day);
  return days.length ? days.sort()[0] : null;
}

/** « Demain », « Aujourd'hui », else « Lundi 16 novembre ». */
export function dayTitle(iso: string, today: string): string {
  const offset = daysFrom(today, iso);
  if (offset === 0) return "Aujourd'hui";
  if (offset === 1) return "Demain";
  const text = noon(iso).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** « lun. 16 » */
export function shortDay(iso: string): string {
  return noon(iso).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric" });
}

/** « jeudi 13/11 », as the agent writes it. */
export function frDay(iso: string): string {
  const date = noon(iso);
  return `${date.toLocaleDateString("fr-FR", { weekday: "long" })} ${date.getDate()}/${date.getMonth() + 1}`;
}

/** « aujourd'hui », « demain », « dans 3 jours » */
export function when(iso: string, today: string): string {
  const offset = daysFrom(today, iso);
  return offset === 0 ? "aujourd'hui" : offset === 1 ? "demain" : `dans ${offset} jours`;
}

export function testsSoon(page: HomePage, today: string, days = TEST_DAYS): HomeTest[] {
  return page.tests.filter((t) => t.day >= today && daysFrom(today, t.day) <= days);
}

/** The tests until D+3 first, then the lessons of the next school day; a subject once. */
export function suggestionsFor(page: HomePage, today: string, next: string | null): HomeSuggestion[] {
  const tests = page.suggestions
    .filter((s) => s.reason === "test" && s.for_day >= today && daysFrom(today, s.for_day) <= TEST_DAYS)
    .sort((a, b) => a.for_day.localeCompare(b.for_day));
  const covered = new Set(tests.map((s) => s.subject_code));
  const lessons = page.suggestions.filter((s) => s.reason === "lesson" && s.for_day === next && !covered.has(s.subject_code));
  return [...tests, ...lessons];
}

/** « 14,2 »: French decimals, no trailing zeros. */
export function number(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  return value.toLocaleString("fr-FR", { maximumFractionDigits: 2 });
}

/** Where to draw a grade on its bar, in % of the scale: the class's range, its average, the grade. */
export function gradeBar(grade: HomeGrade): { low: number; high: number; average: number | null; score: number } | null {
  if (grade.score === null || !grade.out_of) return null;
  const at = (value: number) => Math.max(0, Math.min(100, (value / grade.out_of!) * 100));
  return {
    low: grade.class_min !== null ? at(grade.class_min) : at(grade.score),
    high: grade.class_max !== null ? at(grade.class_max) : at(grade.score),
    average: grade.class_average !== null ? at(grade.class_average) : null,
    score: at(grade.score),
  };
}

/** The grade against the class's average, on the same scale: « +1,4 », « −0,6 », or null. */
export function difference(
  mine: number | null | undefined,
  theirs: number | null | undefined,
): { text: string; above: boolean } | null {
  if (mine === null || mine === undefined || theirs === null || theirs === undefined) return null;
  const rounded = Math.round((mine - theirs) * 10) / 10;
  const sign = rounded > 0 ? "+" : rounded < 0 ? "−" : "±";
  return { text: `${sign}${number(Math.abs(rounded))}`, above: rounded >= 0 };
}

export function gap(grade: HomeGrade): { text: string; above: boolean } | null {
  return difference(grade.score, grade.class_average);
}

const HUES: Record<string, number> = {
  MATHS: 212, FRANC: 350, "HI-GE": 32, AGL1: 265, "PH-CH": 190, SVT: 130, TECHN: 165, LCALA: 300, ESP: 15,
  ESP2: 15, EDMUS: 280, "A-PLA": 330, EPS: 95,
};

/** A hue per subject, the same everywhere on the page. */
export function hue(code: string): number {
  if (code in HUES) return HUES[code];
  let hash = 0;
  for (const char of code) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return hash;
}

// The type of a teacher's file, as a short badge: « PDF », « Image », « Diaporama »…
export function kindOf(name: string): string {
  const suffix = name.toLowerCase().split(".").pop() ?? "";
  if (suffix === "pdf") return "PDF";
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(suffix)) return "Image";
  if (["doc", "docx", "odt"].includes(suffix)) return "Texte";
  if (["ppt", "pptx", "odp"].includes(suffix)) return "Diaporama";
  if (["xls", "xlsx", "ods"].includes(suffix)) return "Tableur";
  if (suffix === "ggb") return "GeoGebra";
  return "Fichier";
}

export function fileSize(bytes: number): string {
  if (bytes < 1_000_000) return `${Math.max(1, Math.round(bytes / 1000))} ko`;
  return `${(bytes / 1_000_000).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} Mo`;
}
