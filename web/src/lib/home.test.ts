import { describe, expect, it } from "vitest";

import {
  dayTitle,
  difference,
  fileSize,
  gap,
  gradeBar,
  isoDay,
  kindOf,
  nextSchoolDay,
  number,
  suggestionsFor,
  testsSoon,
} from "./home";
import type { HomeGrade, HomePage, HomeSuggestion } from "./types";

const slot = (day: string, subject_code = "MATHS", cancelled = false) => ({
  day, starts: "08:20", ends: "09:15", subject_code, subject: subject_code, room: "", cancelled,
});

const suggestion = (subject_code: string, reason: "test" | "lesson", for_day: string): HomeSuggestion => ({
  subject_code, subject: subject_code, reason, for_day, lessons: [], sheet_ids: [],
  request: { kind: reason === "test" ? "revision" : "extra", focus: "" },
});

const PAGE: HomePage = {
  generated_at: "2026-11-15T23:00:00+01:00",
  collected_at: "2026-11-15T23:00:00+01:00",
  // Friday 20, then Monday 23 cancelled, Tuesday 24 a holiday, Wednesday 25 a school day.
  slots: [slot("2026-11-20"), slot("2026-11-23", "MATHS", true), slot("2026-11-24"), slot("2026-11-25", "FRANC")],
  holidays: ["2026-11-24"],
  tests: [
    { day: "2026-11-19", subject_code: "MATHS", subject: "Maths", what: "", reason: "flag" },
    { day: "2026-11-23", subject_code: "FRANC", subject: "Français", what: "", reason: "keywords" },
    { day: "2026-11-24", subject_code: "SVT", subject: "SVT", what: "", reason: "model" },
  ],
  grades: [],
  suggestions: [
    suggestion("FRANC", "test", "2026-11-23"),
    suggestion("FRANC", "lesson", "2026-11-25"),
    suggestion("MATHS", "lesson", "2026-11-25"),
    suggestion("SVT", "test", "2026-11-24"),
  ],
};

const grade = (fields: Partial<HomeGrade>): HomeGrade => ({
  day: "2026-11-06", kind: "Contrôle", title: "", value: "15", score: 15, out_of: 20, coefficient: 1,
  class_average: 12.5, class_min: 5, class_max: 19, ...fields,
});

describe("home page", () => {
  it("takes the local day, not the UTC one", () => {
    expect(isoDay(new Date(2026, 10, 16, 0, 30))).toBe("2026-11-16");
  });

  it("finds the next school day: Monday on a Friday, past cancelled lessons and holidays", () => {
    expect(nextSchoolDay(PAGE, "2026-11-19")).toBe("2026-11-20");
    expect(nextSchoolDay(PAGE, "2026-11-20")).toBe("2026-11-25");
    expect(nextSchoolDay(PAGE, "2026-11-26")).toBeNull();
  });

  it("names the days the way the family says them", () => {
    expect(dayTitle("2026-11-16", "2026-11-15")).toBe("Demain");
    expect(dayTitle("2026-11-15", "2026-11-15")).toBe("Aujourd'hui");
    expect(dayTitle("2026-11-23", "2026-11-20")).toMatch(/^Lundi 23 novembre$/);
  });

  it("shows the tests from today to D+3", () => {
    expect(testsSoon(PAGE, "2026-11-20").map((t) => t.subject_code)).toEqual(["FRANC"]);
    expect(testsSoon(PAGE, "2026-11-21").map((t) => t.subject_code)).toEqual(["FRANC", "SVT"]);
    expect(testsSoon(PAGE, "2026-11-19").map((t) => t.subject_code)).toEqual(["MATHS"]);
  });

  it("suggests the tests first, then the next lessons, a subject once", () => {
    const shown = suggestionsFor(PAGE, "2026-11-21", "2026-11-25");
    expect(shown.map((s) => `${s.subject_code} ${s.reason}`)).toEqual(["FRANC test", "SVT test", "MATHS lesson"]);
  });

  it("draws a grade against its class", () => {
    expect(gradeBar(grade({}))).toEqual({ low: 25, high: 95, average: 62.5, score: 75 });
    expect(gradeBar(grade({ score: null, value: "Abs" }))).toBeNull();
    expect(gradeBar(grade({ class_min: null, class_max: null, class_average: null }))).toEqual(
      { low: 75, high: 75, average: null, score: 75 });
    expect(gap(grade({}))).toEqual({ text: "+2,5", above: true });
    expect(gap(grade({ score: 12 }))?.text).toBe("−0,5");
    expect(number(14.25)).toBe("14,25");
  });
});

describe("teachers' files and averages", () => {
  it("names the kind of a file and its size", () => {
    expect(kindOf("Symétrie centrale.PDF")).toBe("PDF");
    expect(kindOf("ex 28 p 209.png")).toBe("Image");
    expect(kindOf("activité.odp")).toBe("Diaporama");
    expect(kindOf("figure.ggb")).toBe("GeoGebra");
    expect(kindOf("sans-extension")).toBe("Fichier");
    expect(fileSize(146_877)).toBe("147 ko");
    expect(fileSize(3_437_194)).toBe("3,4 Mo");
  });

  it("says the gap between two averages", () => {
    expect(difference(16.27, 16.11)).toEqual({ text: "+0,2", above: true });
    expect(difference(12.1, 14.98)).toEqual({ text: "−2,9", above: false });
    expect(difference(15, null)).toBeNull();
  });
});
