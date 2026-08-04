// SPDX-License-Identifier: AGPL-3.0-or-later
import { assetUrl } from '@/basePath';

interface RequestOptions {
  timeout?: number;
  signal?: AbortSignal;
  headers?: Record<string, string>;
  params?: Record<string, string | number | boolean | undefined>;
}

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public body?: unknown
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// Prefer the backend's friendly message over ApiError.message, which is the
// debug-style "Request failed: POST <path> (<status>)" text — useless to a user.
export function getApiErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const body = err.body as { message?: string; error?: string } | null | undefined;
    if (typeof body?.message === 'string' && body.message) return body.message;
    if (typeof body?.error === 'string' && body.error) return body.error;
    return fallback;
  }
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

class ApiClient {
  private baseUrl: string;
  private defaultTimeout: number;
  private isRefreshing: boolean;

  constructor() {
    const meta = document.querySelector<HTMLMetaElement>('meta[name="base-path"]');
    const basePath = meta?.content?.replace(/\/$/, '') || '';
    this.baseUrl = window.location.origin + basePath + '/api';
    this.defaultTimeout = 30_000;
    this.isRefreshing = false;
  }

  private getAccessToken(): string | null {
    return localStorage.getItem('access_token');
  }

  private getRefreshToken(): string | null {
    return localStorage.getItem('refresh_token');
  }

  private setTokens(access: string, refresh?: string): void {
    localStorage.setItem('access_token', access);
    if (refresh) {
      localStorage.setItem('refresh_token', refresh);
    }
  }

  private clearTokensAndRedirect(): void {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    window.location.href = assetUrl('/login');
  }

  private buildUrl(url: string, params?: Record<string, string | number | boolean | undefined>): string {
    const fullUrl = `${this.baseUrl}${url}`;
    if (!params) return fullUrl;

    const searchParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) {
        searchParams.append(key, String(value));
      }
    }

    const qs = searchParams.toString();
    return qs ? `${fullUrl}?${qs}` : fullUrl;
  }

  private buildHeaders(extra?: Record<string, string>, isUpload?: boolean): Record<string, string> {
    const headers: Record<string, string> = {};

    if (!isUpload) {
      headers['Content-Type'] = 'application/json';
    }

    const token = this.getAccessToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    if (extra) {
      Object.assign(headers, extra);
    }

    return headers;
  }

  private async refreshAccessToken(): Promise<boolean> {
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) return false;

    try {
      const response = await fetch(`${this.baseUrl}/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${refreshToken}`,
        },
      });

      if (!response.ok) return false;

      const data = await response.json();
      this.setTokens(data.access_token, data.refresh_token);
      return true;
    } catch {
      return false;
    }
  }

  private async request<T>(
    method: string,
    url: string,
    body?: unknown,
    options?: RequestOptions,
    isUpload?: boolean
  ): Promise<T> {
    const finalUrl = this.buildUrl(url, options?.params);
    const headers = this.buildHeaders(options?.headers, isUpload);
    const timeout = options?.timeout ?? this.defaultTimeout;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    if (options?.signal) {
      options.signal.addEventListener('abort', () => controller.abort());
    }

    const fetchOptions: RequestInit = {
      method,
      headers,
      signal: controller.signal,
    };

    if (body !== undefined) {
      fetchOptions.body = isUpload ? (body as FormData) : JSON.stringify(body);
    }

    try {
      let response = await fetch(finalUrl, fetchOptions);

      if (response.status === 401 && !this.isRefreshing) {
        this.isRefreshing = true;
        const refreshed = await this.refreshAccessToken();
        this.isRefreshing = false;

        if (refreshed) {
          const retryHeaders = this.buildHeaders(options?.headers, isUpload);
          const retryResponse = await fetch(finalUrl, {
            ...fetchOptions,
            headers: retryHeaders,
          });
          response = retryResponse;
        } else {
          this.clearTokensAndRedirect();
          throw new ApiError('Authentication failed', 401);
        }
      }

      if (!response.ok) {
        // Read the body exactly once — the Response stream is one-shot, so a
        // failed `.json()` (e.g. a non-JSON Werkzeug 405/500 page) leaves the
        // stream consumed and a follow-up `.text()` throws "body stream already
        // read", masking the real status. Read raw text, then try to parse it.
        const rawBody = await response.text();
        let errorBody: unknown;
        try {
          errorBody = JSON.parse(rawBody);
        } catch {
          errorBody = rawBody; // Non-JSON error body (e.g. a Werkzeug error page).
        }
        throw new ApiError(
          `Request failed: ${method} ${url} (${response.status})`,
          response.status,
          errorBody
        );
      }

      if (response.status === 204) {
        return undefined as T;
      }

      const json = await response.json();

      // Flask API wraps responses in { data: ..., success: true }.
      // Unwrap so consumers get the inner payload directly.
      if (json && typeof json === 'object' && 'data' in json && 'success' in json) {
        return json.data as T;
      }
      return json as T;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async get<T>(url: string, options?: RequestOptions): Promise<T> {
    return this.request<T>('GET', url, undefined, options);
  }

  async post<T>(url: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>('POST', url, body, options);
  }

  async put<T>(url: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>('PUT', url, body, options);
  }

  async del<T>(url: string, options?: RequestOptions): Promise<T> {
    return this.request<T>('DELETE', url, undefined, options);
  }

  async patch<T>(url: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>('PATCH', url, body, options);
  }

  async upload<T>(url: string, formData: FormData, options?: RequestOptions): Promise<T> {
    return this.request<T>('POST', url, formData, options, true);
  }

  /** Build the absolute URL for an API path. Used by callers that bypass
   *  `request()` (e.g. streaming endpoints that need a raw `fetch`). */
  resolveUrl(path: string): string {
    return this.buildUrl(path);
  }

  /**
   * Download a binary response with auth headers attached. Use this for
   * file downloads instead of `<a href={resolveUrl(...)} download>` —
   * anchor navigations don't include the JWT (token lives in localStorage,
   * not cookies) and the route returns 401.
   */
  async download(path: string): Promise<{ blob: Blob; filename: string }> {
    const url = this.buildUrl(path);
    const headers = this.buildHeaders();
    delete headers['Content-Type']; // GET has no body

    let response = await fetch(url, { method: 'GET', headers });

    if (response.status === 401 && !this.isRefreshing) {
      this.isRefreshing = true;
      const refreshed = await this.refreshAccessToken();
      this.isRefreshing = false;
      if (refreshed) {
        const retryHeaders = this.buildHeaders();
        delete retryHeaders['Content-Type'];
        response = await fetch(url, { method: 'GET', headers: retryHeaders });
      } else {
        this.clearTokensAndRedirect();
        throw new ApiError('Authentication failed', 401);
      }
    }

    if (!response.ok) {
      let body: unknown;
      try { body = await response.text(); } catch { body = undefined; }
      throw new ApiError(
        `Download failed: ${path} (${response.status})`,
        response.status,
        body,
      );
    }

    const cd = response.headers.get('Content-Disposition') ?? '';
    const m = cd.match(/filename="([^"]+)"/);
    const filename = m
      ? m[1]
      : path.split('/').pop() || 'download';

    return { blob: await response.blob(), filename };
  }
}

export { ApiClient, ApiError };
export type { RequestOptions };
export const apiClient = new ApiClient();

// The authenticated apiClient was previously exposed on window so the
// vanilla-JS DotDNADataTable could fetch assets. That made the full
// authenticated client (including post/put/delete) reachable from any
// browser extension or XSS payload. The vanilla DataTable is no longer
// instantiated by the React app (ReportTable.tsx renders the React-side
// ExplorerTable instead), so the global is removed entirely. Any future
// vanilla-JS chart that needs API access must receive a constrained
// adapter via its constructor.
