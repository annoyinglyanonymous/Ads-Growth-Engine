/* How a page gets its data.
 *
 * EVERY PAGE CALLS THE SAME FUNCTION THE AGENT CALLS -- ui.py's docstring, and
 * it still holds: a page URL and its /api twin take the same query string, and
 * the API handler is the one that calls intel.metrics. Nothing here derives a
 * number; a page receives what the Jinja template used to be handed (`d`,
 * `settled`, `brand`, and the page's own extras) and formats it.
 *
 * A 200 with `error` set is ui._fail: a state of the install (usually
 * ADS_DATABASE_URL_RO unset), said in one sentence. The page draws ErrorPage.
 * A non-2xx or a network failure is a different claim and gets its own
 * sentence, including the one about a stale server process.
 */
import { useEffect, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

export const DEFAULT_BRAND = "renegade";

/** The frame every page's JSON carries. */
export interface PageBase {
  nav: string;
  brand: string | null;
  settled: string | null;
  caveat: string | null;
  until: string | null;
  today: string;
  error?: string;
  detail?: string;
}

export type PageState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "ok"; data: T; error: null }
  | { status: "failed"; data: null; error: string };

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** The one sentence for a 404 against an endpoint this build expects: a
 *  dashboard process older than the page, not a broken feature. The two look
 *  identical otherwise, and it has cost a round of "why is it not working". */
export function staleServer(path: string): string {
  return `The dashboard is running an older version of itself and has no ${path}. ` +
    "Restart it: Ctrl+C, then python -m main.";
}

export async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (r.status === 404) throw new HttpError(404, staleServer(url.split("?")[0]));
  if (!r.ok) {
    let msg = `The server answered ${r.status}.`;
    try {
      const body = await r.json();
      if (body && typeof body.error === "string") msg = body.error;
    } catch { /* the status is the message */ }
    throw new HttpError(r.status, msg);
  }
  return (await r.json()) as T;
}

/** The brand in the query string, or the default. */
export function brandOf(search: string): string {
  return new URLSearchParams(search).get("brand") || DEFAULT_BRAND;
}

/** Fetch a page's data from its /api twin, re-fetching when the URL changes.
 *  `path` is the page path ("/", "/why", "/ad/<key>"); the query string of
 *  the current location is passed through untouched, so /creative?window=14
 *  stays a link somebody can send. */
export function usePageData<T extends PageBase>(path: string): PageState<T> {
  const { search } = useLocation();
  const [state, setState] = useState<PageState<T>>({ status: "loading", data: null, error: null });

  useEffect(() => {
    let live = true;
    setState({ status: "loading", data: null, error: null });
    getJson<T>(`/api${path}${search}`)
      .then((d) => { if (live) setState({ status: "ok", data: d, error: null }); })
      .catch((e: unknown) => {
        if (live) setState({ status: "failed", data: null, error: e instanceof Error ? e.message : String(e) });
      });
    return () => { live = false; };
  }, [path, search]);

  return state;
}

/** The ad key from the route, for /ad/:adKey. */
export function useAdKey(): string {
  return useParams().adKey ?? "";
}

/** Append or replace query parameters on the current search string. A null
 *  value removes the key. The result is a link, so each page's window
 *  switcher stays a set of URLs rather than a form. */
export function withParams(search: string, patch: Record<string, string | number | null>): string {
  const p = new URLSearchParams(search);
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === "") p.delete(k);
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}
