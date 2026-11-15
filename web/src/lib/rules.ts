// The rule form (T21): « le formulaire fait foi ». A rule written or changed here is saved by the
// laptop as it is, without the model. The laptop validates it again (schema/rule.py).

import type { RuleBody } from "./types";

export const TRIGGERS: [string, string][] = [
  ["after_each_lesson", "Le soir de chaque cours"],
  ["before_each_lesson", "La veille de chaque cours"],
  ["before_test", "Avant chaque contrôle"],
  ["after_feedback", "Le lendemain de questions ratées"],
  ["load_limit", "Une limite de temps par soir"],
];

export const SCOPES: [string, string][] = [
  ["this_lesson", "la séance du jour"],
  ["previous_lesson", "le cours précédent"],
  ["last_n_lessons", "les derniers cours"],
  ["since_last_test", "tout depuis le dernier contrôle"],
  ["missed_items", "les notions ratées"],
];

export const FORMATS: [string, string][] = [
  ["exercises", "Exercices"],
  ["quick_quiz", "Interro flash (5 questions)"],
  ["lesson_recap", "Cours en clair"],
  ["test_revision", "Révision"],
];

/** What the form edits, as plain fields. */
export interface RuleForm {
  id: string;
  subject: string; // "" for every subject
  trigger: string;
  scope: string;
  format: string;
  oral: boolean;
  together: boolean;
  target_minutes: number;
  items: number | null;
  replaces_default: boolean;
  offsets: string; // "4, 2, 1"
  n_lessons: number;
  max_minutes: number;
  active: boolean;
}

export function toForm(rule: RuleBody | null): RuleForm {
  return {
    id: rule?.id ?? "",
    subject: rule?.subject ?? "",
    trigger: rule?.trigger ?? "before_each_lesson",
    scope: rule?.scope ?? "previous_lesson",
    format: rule?.format ?? "quick_quiz",
    oral: rule?.style === "oral_quiz",
    together: rule?.suggested_mode === "together",
    target_minutes: rule?.target_minutes ?? 8,
    items: rule?.items ?? null,
    replaces_default: rule?.replaces_default ?? false,
    offsets: (rule?.offsets_days ?? [4, 2, 1]).join(", "),
    n_lessons: rule?.n_lessons ?? 3,
    max_minutes: rule?.max_minutes ?? 20,
    active: (rule?.status ?? "active") !== "archived",
  };
}

/** The rule to send: the fields of the form over the rule it started from (its sentence is kept). */
export function fromForm(form: RuleForm, base: RuleBody | null): RuleBody {
  const { version: _version, ...kept } = base ?? ({} as RuleBody);
  const rule: RuleBody = {
    ...kept,
    id: form.id.trim(),
    status: form.active ? "active" : "archived",
    trigger: form.trigger,
    subject: form.subject || null,
  };
  if (form.trigger === "load_limit") {
    return { ...rule, subject: null, max_minutes: form.max_minutes };
  }
  return {
    ...rule,
    scope: form.scope,
    format: form.format,
    style: form.oral ? "oral_quiz" : "",
    suggested_mode: form.together ? "together" : "solo",
    target_minutes: form.target_minutes,
    items: form.format === "quick_quiz" || form.format === "lesson_recap" ? null : form.items,
    replaces_default: form.subject ? form.replaces_default : false,
    offsets_days: form.trigger === "before_test" ? parseOffsets(form.offsets) : [],
    n_lessons: form.n_lessons,
  };
}

/** « 4, 2, 1 » → [4, 2, 1]: days before the test, between 1 and 14. */
export function parseOffsets(text: string): number[] {
  const days = text
    .split(/[^0-9]+/)
    .filter(Boolean)
    .map(Number)
    .filter((d) => d >= 1 && d <= 14);
  return [...new Set(days)].sort((a, b) => b - a);
}

/** A rule id from a subject and a trigger, for a new rule: hi-ge-before-quick-quiz. */
export function newRuleId(form: RuleForm): string {
  const short: Record<string, string> = {
    before_each_lesson: "before",
    after_each_lesson: "after",
    before_test: "test",
    after_feedback: "missed",
    load_limit: "limit",
  };
  return `${(form.subject || "all").toLowerCase()}-${short[form.trigger] ?? "rule"}-${form.format.replace(/_/g, "-")}`;
}
