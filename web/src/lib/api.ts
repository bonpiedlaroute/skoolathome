// Calls to the API. The session is an HttpOnly cookie: scripts never see it.
// Answers and reports go through the outbox (outbox.svelte.ts), so they survive a lost connection.

import { enqueue } from "./outbox.svelte";
import type { Counters } from "./progress";
import type {
  Device,
  Explanation,
  HomeData,
  Instruction,
  InstructionReplyKind,
  ItemAnnotation,
  PublishedRule,
  Rating,
  ReportReason,
  RequestKind,
  Response as Answer,
  RuleBody,
  Sheet,
  SheetRequest,
  SheetSummary,
  WeeklyReview,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

/** A file as it is (a photo of the notebook): not JSON. */
async function upload<T>(path: string, body: Blob, type: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "content-type": type },
    body,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return (await response.json()) as T;
}

const sheetPath = (id: string) => `/sheets/${encodeURIComponent(id)}`;
const requestPath = (id: string) => `/requests/${encodeURIComponent(id)}`;

export const api = {
  me: () => call<Device>("GET", "/me"),
  pairWithCode: (code: string, label: string) => call<Device>("POST", "/pair", { code, label }),
  pairWithToken: (token: string) => call<Device>("POST", "/pair", { token }),
  sheets: () => call<SheetSummary[]>("GET", "/sheets"),
  home: () => call<HomeData>("GET", "/home"),
  sheet: (id: string) => call<Sheet>("GET", sheetPath(id)),
  progress: (id: string) => call<Record<string, Counters>>("GET", `${sheetPath(id)}/progress`),
  devices: () => call<Device[]>("GET", "/devices"),
  revoke: (id: string) => call<void>("DELETE", `/devices/${encodeURIComponent(id)}`),
  pairingToken: (role: "parent" | "child", label: string) =>
    call<{ code: string; url: string; qr_svg: string; expires_in_minutes: number }>("POST", "/pairing-tokens", { role, label }),
  pushKey: () => call<{ public_key: string }>("GET", "/push/key"),
  pushSubscribe: (subscription: PushSubscriptionJSON) => call<{ ok: boolean }>("POST", "/push/subscription", subscription),
  pushTest: () => call<{ sent: number; failed: number }>("POST", "/push/test"),
  requests: () => call<SheetRequest[]>("GET", "/requests"),
  createRequest: (subject: string, kind: RequestKind, focus: string, minutes: number | null, photos = 0) =>
    call<SheetRequest>("POST", "/requests", { subject, kind, focus, minutes, photos }),
  uploadPhoto: (id: string, position: number, photo: Blob) =>
    upload<SheetRequest>(`${requestPath(id)}/photos/${position}`, photo, "image/jpeg"),
  reviewPages: (id: string, pages: string[]) => call<SheetRequest>("POST", `${requestPath(id)}/pages`, { pages }),
  cancelRequest: (id: string) => call<SheetRequest>("POST", `${requestPath(id)}/cancel`, {}),
  hideSheet: (id: string) => call<void>("DELETE", sheetPath(id)),
  restoreSheet: (id: string) => call<{ ok: boolean }>("POST", `${sheetPath(id)}/restore`),
  instructions: () => call<Instruction[]>("GET", "/instructions"),
  createInstruction: (fields: { text: string } | { rule: RuleBody }) => call<Instruction>("POST", "/instructions", fields),
  replyInstruction: (id: string, kind: InstructionReplyKind, fields: { text?: string; rule?: RuleBody } = {}) =>
    call<Instruction>("POST", `/instructions/${encodeURIComponent(id)}/reply`, { kind, ...fields }),
  rules: () => call<PublishedRule[]>("GET", "/rules"),
  reviews: () => call<{ week: string; title: string; received_at: string }[]>("GET", "/weekly-reviews"),
  review: (week = "latest") => call<WeeklyReview>("GET", `/weekly-reviews/${encodeURIComponent(week)}`),
  annotate: (sheetId: string, items: ItemAnnotation[]) => call<{ saved: number }>("POST", "/annotations", { sheet_id: sheetId, items }),
  explain: (sheetId: string, itemId: string, question: string) =>
    call<Explanation>("POST", "/explanations", { sheet_id: sheetId, item_id: itemId, question }),
  explanations: (sheetId?: string) =>
    call<Explanation[]>("GET", `/explanations${sheetId ? `?sheet_id=${encodeURIComponent(sheetId)}` : ""}`),
  explanation: (id: string) => call<Explanation>("GET", `/explanations/${encodeURIComponent(id)}`),
  helped: (id: string, helped: boolean) =>
    call<Explanation>("POST", `/explanations/${encodeURIComponent(id)}/helped`, { helped }),

  // Queued: these return at once, the server gets them as soon as it can.
  // traceId: the trace of the generation that made the sheet, so the answer reaches it in Langfuse (T22).
  attempt(
    sheetId: string,
    itemId: string,
    fields: { mode: "solo" | "together"; response?: Answer; rating?: Rating; hints_used?: number },
    traceId?: string | null,
  ) {
    enqueue("/attempts", { sheet_id: sheetId, item_id: itemId, answered_at: new Date().toISOString(), ...fields, trace_id: traceId ?? null });
  },
  report(sheetId: string, itemId: string | null, reason: ReportReason, comment: string, traceId?: string | null) {
    enqueue("/reports", { sheet_id: sheetId, item_id: itemId, reason, comment, trace_id: traceId ?? null });
  },
  finish(sheetId: string) {
    enqueue(`${sheetPath(sheetId)}/finish`, {});
  },
};

const PREFETCH = 10; // sheets loaded ahead, so they open without network

/** Load the device, the list and the latest sheets once, so the service worker keeps a copy. */
export async function warmCache(): Promise<void> {
  try {
    await api.me();
    const sheets = await api.sheets();
    await api.home().catch(() => undefined);
    await Promise.allSettled(sheets.slice(0, PREFETCH).flatMap((s) => [api.sheet(s.id), api.progress(s.id)]));
  } catch {
    // offline or not paired: nothing to keep
  }
}

