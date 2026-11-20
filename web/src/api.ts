export type Identity = { id: string; username: string };
export type Session = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};
export type Status =
  "queued" | "running" | "completed" | "failed" | "cancelled";
export type Run = {
  id: string;
  session_id: string;
  prompt: string;
  status: Status;
  answer: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};
export type Trace = {
  seq: number;
  id: string;
  parent_id: string | null;
  kind: string;
  label: string;
  status: string;
  detail?: string | null;
  created_at: string;
};
export type Artifact = {
  path: string;
  name: string;
  size: number;
  modified_at: string;
  mime_type: string;
};
export type EventPage = {
  events: Trace[];
  cursor: number;
  status: Status;
  has_more?: boolean;
};
export const running = (status: string) =>
  status === "queued" || status === "running";
export const scope = (sid: string) =>
  "/api/sessions/" + encodeURIComponent(sid);
export const fileURL = (sid: string, path: string, download = false) =>
  scope(sid) +
  "/file?" +
  new URLSearchParams({ path, ...(download ? { download: "true" } : {}) });
export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  opts: RequestInit = {},
): Promise<T> {
  let r: Response;
  try {
    r = await fetch(path, {
      ...opts,
      credentials: "same-origin",
      headers: {
        ...(opts.body ? { "Content-Type": "application/json" } : {}),
        ...opts.headers,
      },
    });
  } catch (e) {
    if (opts.signal?.aborted) throw e;
    throw new Error("网络连接中断，请检查连接后重试。");
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    if (r.status === 401 && !path.startsWith("/api/auth/"))
      window.dispatchEvent(new Event("famsun:signed-out"));
    const fallback =
      r.status === 429
        ? "请求过于频繁，请稍后重试。"
        : "请求失败（" + r.status + "）";
    throw new HttpError(
      r.status,
      typeof body.detail === "string" ? body.detail : fallback,
    );
  }
  return r.status === 204 ? (undefined as T) : r.json();
}
export const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
export const messageOf = (err: unknown) =>
  err instanceof Error ? err.message : "请求失败，请重试。";
export function fileSize(n: number) {
  return n < 1024
    ? n + " B"
    : n < 1024 ** 2
      ? (n / 1024).toFixed(1) + " KB"
      : (n / 1024 ** 2).toFixed(1) + " MB";
}
