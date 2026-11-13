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

export interface Item {
  id: string;
  type: ItemType;
  prompt: string;
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

export interface Sheet {
  id: string;
  subject: string;
  title: string;
  type: string;
  sources: string[];
  visible_from: string;
  estimated_minutes: number;
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
