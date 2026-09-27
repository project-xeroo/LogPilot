import { useSession } from "@/store/session";

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

const BASE = "/api/v1";

function messageFrom(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d?.msg ? `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}` : String(d))).join("; ");
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; errors?: string[] };
    return [d.message, ...(d.errors ?? [])].filter(Boolean).join(" ") || `Request failed (${status})`;
  }
  return `Request failed (${status})`;
}

export async function request<T = any>(method: string, path: string, opts: { body?: unknown; params?: Record<string, unknown>; form?: FormData; raw?: boolean } = {}): Promise<T> {
  const { token } = useSession.getState();
  const url = new URL(BASE + path, window.location.origin);
  for (const [k, v] of Object.entries(opts.params ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, String(x)));
    else url.searchParams.set(k, String(v));
  }
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body });
  } catch {
    throw new ApiError(0, "Can't reach the LogPilot API. Check your connection and try again.");
  }
  if (res.status === 401 && token && !path.startsWith("/auth/login")) {
    useSession.getState().logout();
    throw new ApiError(401, "Your session has expired. Sign in again to continue.");
  }
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-json error body */
    }
    throw new ApiError(res.status, messageFrom(detail, res.status), detail);
  }
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T = any>(path: string, params?: Record<string, unknown>) => request<T>("GET", path, { params });
export const post = <T = any>(path: string, body?: unknown, params?: Record<string, unknown>) => request<T>("POST", path, { body, params });
export const put = <T = any>(path: string, body?: unknown) => request<T>("PUT", path, { body });
export const patch = <T = any>(path: string, body?: unknown) => request<T>("PATCH", path, { body });
export const del = <T = any>(path: string, params?: Record<string, unknown>) => request<T>("DELETE", path, { params });

/** Download a file endpoint (report export, audit export) with the auth header attached. */
export async function download(path: string, params: Record<string, unknown>, fallbackName: string) {
  const res = await request<Response>("GET", path, { params, raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(cd)?.[1] ?? fallbackName;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/** Upload a log file with progress (XHR gives real upload progress; fetch does not). */
export function uploadLogs(projectId: string, file: File, opts: { environment?: string; onProgress?: (pct: number) => void }): Promise<any> {
  const { token } = useSession.getState();
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const q = new URLSearchParams();
    if (opts.environment) q.set("environment", opts.environment);
    xhr.open("POST", `${BASE}/projects/${projectId}/logs/upload?${q}`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && opts.onProgress?.((e.loaded / e.total) * 100);
    xhr.onload = () => {
      let json: any = null;
      try {
        json = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(json);
      else reject(new ApiError(xhr.status, messageFrom(json?.detail, xhr.status), json?.detail));
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload failed: can't reach the LogPilot API."));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}
