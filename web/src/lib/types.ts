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
  trace_id?: string | null; // the generation's trace in Langfuse: sent back with each answer (T22)
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

/** A rule of the agent (src/skoolathome/schema/rule.py). The laptop validates it again. */
export interface RuleBody {
  id: string;
  version?: number;
  status?: "proposed" | "active" | "archived";
  subject?: string | null;
  teacher?: string | null;
  trigger: string;
  when?: string;
  scope?: string;
  format?: string;
  style?: string;
  suggested_mode?: "solo" | "together";
  target_minutes?: number;
  items?: number | null;
  replaces_default?: boolean;
  offsets_days?: number[];
  n_lessons?: number;
  max_minutes?: number | null;
  origin?: { text: string; author: string; on: string | null };
}

export type InstructionStatus = "pending" | "question" | "proposed" | "saved" | "rejected" | "failed";
export type InstructionReplyKind = "answer" | "approve" | "edit" | "reject";

/** An instruction in plain French (or a rule from the form), and what the agent made of it. */
export interface Instruction {
  id: string;
  text: string;
  rule: RuleBody | null;
  created_at: string;
  status: InstructionStatus;
  question: string;
  summary: string; // « J'ai compris : … », written by the laptop
  proposal: RuleBody | null;
  rule_id: string | null;
  rule_version: number | null;
  reason: string;
  waiting: boolean; // replied: the laptop has not read it yet
}

export interface PublishedRule {
  id: string;
  version: number;
  status: string;
  summary: string;
  body: RuleBody;
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

/** The weekly review (T24): indicators, alerts first, and the sheets to annotate. */
export interface Indicator {
  name: string;
  label: string;
  value: string;
  alert: boolean;
  detail: string;
}

export interface WeeklyReview {
  week: string; // 2026-W46
  start: string;
  end: string;
  title: string;
  body: string;
  indicators: Indicator[];
  to_annotate: { id: string; subject: string; title: string }[];
  annotated: string[]; // sheets already annotated
}

/** What the parent can say is wrong with a question: the judge's criteria. */
export type Problem = "faithful" | "answer_correct" | "level_ok" | "useful";

export interface ItemAnnotation {
  item_id: string;
  ok: boolean;
  problem?: Problem | null;
  comment?: string;
}
