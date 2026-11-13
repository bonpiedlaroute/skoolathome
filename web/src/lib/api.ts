// Calls to the API. The session is an HttpOnly cookie: scripts never see it.

import type { Device, Response as Answer, Sheet, SheetSummary } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
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

export const api = {
  me: () => call<Device>("GET", "/me"),
  pairWithCode: (code: string, label: string) => call<Device>("POST", "/pair", { code, label }),
  pairWithToken: (token: string) => call<Device>("POST", "/pair", { token }),
  sheets: () => call<SheetSummary[]>("GET", "/sheets"),
  sheet: (id: string) => call<Sheet>("GET", `/sheets/${encodeURIComponent(id)}`),
  attempt: (sheet_id: string, item_id: string, response: Answer, hints_used: number) =>
    call<{ correct: boolean }>("POST", "/attempts", { sheet_id, item_id, mode: "solo", response, hints_used }),
  devices: () => call<Device[]>("GET", "/devices"),
  revoke: (id: string) => call<void>("DELETE", `/devices/${encodeURIComponent(id)}`),
  pairingToken: (role: "parent" | "child", label: string) =>
    call<{ url: string; qr_svg: string; expires_in_minutes: number }>("POST", "/pairing-tokens", { role, label }),
};
