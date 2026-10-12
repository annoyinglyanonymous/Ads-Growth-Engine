/* Small pieces every page speaks: the window switcher, a caveat, an empty
 * state, the folded tail of a table, a stage heading. Their class names are
 * base.css's, carried over from the templates unchanged -- a page asks for
 * `.stage` and does not care which renderer is answering.
 */
import type { CSSProperties, ReactElement, ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { usePageData, withParams, type PageBase, type PageState } from "../lib/api";
import { Icon } from "./Icon";
import { ErrorPage, Loading } from "../pages/ErrorPage";

/** Fetch a page's data and, until it is there, the frame to draw instead.
 *  A page writes `const page = usePage(...); if (page.gate) return page.gate;`
 *  and from then on `page.data` is its JSON with `error` already handled. */
export function usePage<T extends PageBase>(path: string, nav: string, title: string):
  { data: T; gate: null } | { data: null; gate: ReactElement } {
  const page: PageState<T> = usePageData<T>(path);
  if (page.status === "loading") return { data: null, gate: <Loading nav={nav} title={title} /> };
  if (page.status === "failed") return { data: null, gate: <ErrorPage nav={nav} error={page.error} /> };
  if (page.data.error) {
    return { data: null, gate: <ErrorPage nav={nav} error={page.data.error} detail={page.data.detail} /> };
  }
  return { data: page.data, gate: null };
}

/** The window switcher. Links, not a form: each window is a URL somebody can
 *  send, and changing it needs no Apply button. */
export function Seg({ param, options, current, label = "Window", suffix = "d" }: {
  param: string; options: number[]; current: number; label?: string; suffix?: string;
}) {
  const { search, pathname } = useLocation();
  return (
    <nav className="seg" aria-label={label}>
      {options.map((n) => (
        <Link key={n} to={`${pathname}${withParams(search, { [param]: n })}`}
              aria-current={n === current ? "page" : undefined}>{n}{suffix}</Link>
      ))}
    </nav>
  );
}

/** A pale blue note with a navy mark. The sentence inside is what says why. */
export function Caveat({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div className="caveat" style={style}><Icon name="warn" /><span>{children}</span></div>;
}

export function Empty({ children, small }: { children: ReactNode; small?: boolean }) {
  return <div className={small ? "empty small" : "empty"}>{children}</div>;
}

/** The tail of a cut table. A <details> rather than a button because the rows
 *  must be there for Ctrl-F and for print whether or not anyone opened it. */
export function More({ summary, children, style }: {
  summary: ReactNode; children: ReactNode; style?: CSSProperties;
}) {
  return (
    <details className="more" style={style}>
      <summary>{summary}</summary>
      {children}
    </details>
  );
}

export function StageHead({ title, source, state }: { title: ReactNode; source?: string; state?: ReactNode }) {
  return (
    <div className="stage-head">
      <h2>{title}</h2>
      {source && <span className="state">Source: <code>{source}</code></span>}
      {state}
    </div>
  );
}

/** An absent figure in a cell. NULL, never 0. */
export function Dash() {
  return <span className="subtle">{"—"}</span>;
}

/** A link to an ad's page, carrying the brand. */
export function AdLink({ adKey, brand, children }: { adKey: string; brand: string | null; children: ReactNode }) {
  return <Link to={`/ad/${adKey}?brand=${encodeURIComponent(brand ?? "")}`}>{children}</Link>;
}

/** The reader's own date string. Everything arrives ISO from jsonable_encoder;
 *  "YYYY-MM-DD HH:MM" is what strftime printed. */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return "--";
  return iso.length >= 16 ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : iso;
}
