import { describe, expect, it } from "vitest";

import { fromForm, newRuleId, parseOffsets, toForm } from "./rules";

const PROPOSAL = {
  id: "hi-ge-before-quick-quiz",
  version: 1,
  subject: "HI-GE",
  trigger: "before_each_lesson",
  scope: "previous_lesson",
  format: "quick_quiz",
  style: "oral_quiz",
  suggested_mode: "together" as const,
  target_minutes: 5,
  items: null,
  replaces_default: true,
  origin: { text: "Le prof d'histoire interroge…", author: "parent", on: "2026-11-13" },
};

describe("rule form", () => {
  it("gives back the rule it was filled with, without its version", () => {
    const rule = fromForm(toForm(PROPOSAL), PROPOSAL);
    expect(rule.version).toBeUndefined();
    expect(rule).toMatchObject({ subject: "HI-GE", style: "oral_quiz", suggested_mode: "together", status: "active" });
    expect(rule.origin?.text).toBe("Le prof d'histoire interroge…");
  });

  it("keeps only what each trigger and format need", () => {
    const form = { ...toForm(PROPOSAL), format: "exercises", items: 4, trigger: "before_test", offsets: "7, 2 et 1" };
    expect(fromForm(form, PROPOSAL)).toMatchObject({ items: 4, offsets_days: [7, 2, 1] });
    const quiz = fromForm({ ...toForm(PROPOSAL), items: 8 }, PROPOSAL);
    expect(quiz.items).toBeNull(); // a quick quiz has 5 questions
    const limit = fromForm({ ...toForm(null), id: "max_evening_load", trigger: "load_limit", max_minutes: 30 }, null);
    expect(limit).toEqual({ id: "max_evening_load", status: "active", trigger: "load_limit", subject: null, max_minutes: 30 });
  });

  it("reads the days before a test as the parent types them", () => {
    expect(parseOffsets("4, 2, 1")).toEqual([4, 2, 1]);
    expect(parseOffsets("1 2 2 30 7")).toEqual([7, 2, 1]);
  });

  it("names a new rule after its subject, trigger and format", () => {
    expect(newRuleId({ ...toForm(null), subject: "AGL1" })).toBe("agl1-before-quick-quiz");
  });
});
