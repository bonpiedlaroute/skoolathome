// The sheet format, as sent by the API. Defined in src/skoolathome/schema/sheet.py.

export type CheckMode = "exact" | "variants" | "number" | "fraction" | "choice" | "self_assessment";
export type ItemType = "choice" | "number" | "fraction" | "short_answer" | "fill_blank" | "matching" | "open";
export type Answer = string | string[];
export type Response = string | string[] | boolean;
export type Rating = "good" | "partial" | "bad";
export type ReportReason = "wrong_answer" | "not_covered" | "unclear" | "too_hard" | "too_easy";

export interface ParentGuide {
  explanation: string;
  common_mistake: string;
  oral_followup: string;
}

export interface Quote {
  source_id: string;
  excerpt: string; // copied word for word from the source
}

export interface Item {
  id: string;
  type: ItemType;
  prompt: string;
  quote?: Quote | null;
  answer: Answer;
  variants: Answer[];
  check: { mode: CheckMode; tolerance: string };
  choices: string[];
  left: string[];
  why_wrong: Record<string, string>;
  hints: string[];
  solution: string;
  parent_guide?: ParentGuide | null;
  concept: string;
  difficulty: number;
}

export type SheetType = "exercises" | "lesson_recap" | "quick_quiz" | "test_revision";

/** The « cours en clair » of a lesson_recap sheet, read before its questions. */
export interface Recap {
  summary: string;
  example: { statement: string; solution: string; quote?: Quote | null };
  key_points: string[];
}

export interface Sheet {
  id: string;
  subject: string;
  title: string;
  type: SheetType | string;
  sources: string[];
  source_labels?: Record<string, string>; // "lesson:2026-11-04:HI-GE" -> "ton cours du mardi 4/11"
  visible_from: string;
  due_by?: string | null;
  estimated_minutes: number;
  recap?: Recap | null;
  items: Item[];
}

export interface SheetSummary {
  id: string;
  subject: string;
  title: string;
  type: string;
  visible_from: string;
  due_by: string | null;
  estimated_minutes: number;
  items: number;
  answered: number;
  correct: number;
  to_review: number;
}

export interface Device {
  id: string;
  role: "parent" | "child";
  label: string;
  created_at?: string;
  last_seen_at?: string | null;
  is_this_device?: boolean;
}

export type RequestKind = "revision" | "extra";

/** A sheet asked for by the parent, made at the laptop's next run. */
export interface SheetRequest {
  id: string;
  subject: string; // École Directe code
  kind: RequestKind;
  focus: string;
  items: number | null;
  created_at: string;
  status: "pending" | "done" | "abstained";
  sheet_id: string | null;
  reason: string;
}
