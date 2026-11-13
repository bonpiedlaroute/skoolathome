// Calls to the API. The session is an HttpOnly cookie: scripts never see it.
// Answers and reports go through the outbox (outbox.svelte.ts), so they survive a lost connection.

import { enqueue } from "./outbox.svelte";
import type { Counters } from "./progress";
import type { Device, Rating, ReportReason, Response as Answer, Sheet, SheetSummary } from "./types";

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

const sheetPath = (id: string) => `/sheets/${encodeURIComponent(id)}`;

export const api = {
  me: () => call<Device>("GET", "/me"),
  pairWithCode: (code: string, label: string) => call<Device>("POST", "/pair", { code, label }),
  pairWithToken: (token: string) => call<Device>("POST", "/pair", { token }),
  sheets: () => call<SheetSummary[]>("GET", "/sheets"),
  sheet: (id: string) => call<Sheet>("GET", sheetPath(id)),
  progress: (id: string) => call<Record<string, Counters>>("GET", `${sheetPath(id)}/progress`),
  devices: () => call<Device[]>("GET", "/devices"),
  revoke: (id: string) => call<void>("DELETE", `/devices/${encodeURIComponent(id)}`),
  pairingToken: (role: "parent" | "child", label: string) =>
    call<{ url: string; qr_svg: string; expires_in_minutes: number }>("POST", "/pairing-tokens", { role, label }),
  pushKey: () => call<{ public_key: string }>("GET", "/push/key"),
  pushSubscribe: (subscription: PushSubscriptionJSON) => call<{ ok: boolean }>("POST", "/push/subscription", subscription),
  pushTest: () => call<{ sent: number; failed: number }>("POST", "/push/test"),

  // Queued: these return at once, the server gets them as soon as it can.
  attempt(sheetId: string, itemId: string, fields: { mode: "solo" | "together"; response?: Answer; rating?: Rating; hints_used?: number }) {
    enqueue("/attempts", { sheet_id: sheetId, item_id: itemId, answered_at: new Date().toISOString(), ...fields });
  },
  report(sheetId: string, itemId: string | null, reason: ReportReason, comment: string) {
    enqueue("/reports", { sheet_id: sheetId, item_id: itemId, reason, comment });
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
    await Promise.allSettled(sheets.slice(0, PREFETCH).flatMap((s) => [api.sheet(s.id), api.progress(s.id)]));
  } catch {
    // offline or not paired: nothing to keep
  }
}

