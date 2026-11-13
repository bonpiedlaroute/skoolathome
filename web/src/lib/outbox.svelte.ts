// Answers, reports and "finished" messages wait here until the server has them.
//
// Each entry is written to localStorage first, then sent. Without network it stays there and
// is sent when the connection comes back. Each body carries a client_id: if the server stored
// it but the reply was lost, sending it again does not create a duplicate.

const KEY = "skoolathome-outbox";
const RETRY_EVERY_MS = 30_000;

interface Entry {
  id: string;
  path: string;
  body: Record<string, unknown>;
}

function load(): Entry[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "[]") as Entry[];
  } catch {
    return [];
  }
}

function save(entries: Entry[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(entries));
  } catch {
    // storage full or blocked: the entry is lost, the page still works
  }
}

export const outbox = $state({ pending: load().length });
let sending = false;

export function newId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Queue a POST to /api{path}. The body gets a client_id. */
export function enqueue(path: string, body: Record<string, unknown>): void {
  const id = newId();
  const entries = load();
  entries.push({ id, path, body: { client_id: id, ...body } });
  save(entries);
  outbox.pending = entries.length;
  void flush();
}

export async function flush(): Promise<void> {
  if (sending) return;
  sending = true;
  try {
    for (;;) {
      const first = load()[0];
      if (!first) break;
      let response: Response;
      try {
        response = await fetch(`/api${first.path}`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(first.body),
        });
      } catch {
        break; // no network: try again later
      }
      // Not paired any more, or the server is down: keep it, try again later.
      if (response.status === 401 || response.status === 503 || response.status >= 500) break;
      // Stored, or refused for good (4xx): either way, sending it again would not help.
      save(load().filter((entry) => entry.id !== first.id));
    }
  } finally {
    outbox.pending = load().length;
    sending = false;
  }
}

export function startOutbox(): void {
  window.addEventListener("online", () => void flush());
  setInterval(() => {
    if (outbox.pending > 0) void flush();
  }, RETRY_EVERY_MS);
  void flush();
}
