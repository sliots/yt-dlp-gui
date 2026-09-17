const TOKEN_KEY = "ytdlp.token";

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : `请求失败 (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(headers) {
  const token = getToken();
  return token ? { ...headers, Authorization: `Bearer ${token}` } : headers;
}

export async function api(path, options = {}) {
  const headers = authHeaders({ ...(options.headers || {}) });
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`/api/v1${path}`, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    if (response.status === 401) {
      clearToken();
      window.dispatchEvent(new CustomEvent("auth-required"));
    }
    throw new ApiError(response.status, payload?.detail || response.statusText);
  }
  return payload;
}

export async function verifyToken() {
  await api("/auth/check");
  return true;
}

export function websocketUrl() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/ws/logs?token=${encodeURIComponent(getToken())}`;
}
