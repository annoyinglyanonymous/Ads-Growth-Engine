/* The shell: a 220px rail, the pale canvas, the content column.
 *
 * Ported from templates/base.html. The rail is text-labelled and not an icon
 * strip: this app has fifteen verbs and eight screens, and an icon-only rail
 * makes the reader guess which glyph means "angle coverage".
 *
 * The rail needs two facts from whichever page is open -- which link is
 * current, and the settled edge -- so a page publishes them through
 * FrameContext from <PageFrame>, which also draws the topbar and <main>.
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { brandOf } from "../lib/api";
import { Freshness } from "./Freshness";
import { Icon } from "./Icon";

export interface Frame {
  nav: string;
  brand: string | null;
  settled: string | null;
}

const FrameContext = createContext<(f: Frame) => void>(() => {});

/* Two groups, by the reader's question. READ is "what happened": the brief,
   the live numbers, and why cost moved. CREATIVE is "what do we write next". */
const READ = [
  ["brief", "/brief", "The brief", "overview"],
  ["overview", "/", "Overview", "overview"],
  ["why", "/why", "Why CPA moved", "why"],
] as const;
const CREATIVE = [
  ["creative", "/creative", "Fatigue", "creative"],
  ["suggestions", "/suggestions", "Suggestions", "suggest"],
  ["angles", "/angles", "Angles", "angles"],
  ["experiments", "/experiments", "Experiments", "experiments"],
] as const;

export function Layout() {
  const { search } = useLocation();
  const [frame, setFrame] = useState<Frame>({ nav: "", brand: null, settled: null });
  const brand = frame.brand ?? brandOf(search);
  const q = brand ? `?brand=${encodeURIComponent(brand)}` : "";

  const links = (rows: typeof READ | typeof CREATIVE) => (
    <nav>
      {rows.map(([key, href, label, glyph]) => (
        <Link key={key} className="rail-link" to={`${href}${q}`}
              aria-current={frame.nav === key ? "page" : undefined}>
          <Icon name={glyph} />{label}
        </Link>
      ))}
    </nav>
  );

  return (
    <FrameContext.Provider value={setFrame}>
      <div className="shell">
        <aside className="rail">
          <Link className="rail-brand" to={`/${q}`}>
            <span className="mark" aria-hidden="true">AI</span>
            <span className="name">Ads Intelligence</span>
          </Link>
          <div className="rail-group">Read</div>
          {links(READ)}
          <div className="rail-group">Creative</div>
          {links(CREATIVE)}
          <div className="rail-foot">
            <div className="freshness">
              <div className="t">Settled through</div>
              <div className="v">{frame.settled ?? "—"}</div>
              <div className="n">
                {frame.settled
                  ? "Meta restates for ~3 days. Anything after this date can still move."
                  : "No active ad account for this brand."}
              </div>
            </div>
            <Freshness brand={brand} />
          </div>
        </aside>
        <div className="col">
          <Outlet />
        </div>
      </div>
    </FrameContext.Provider>
  );
}

/** The topbar and <main> of one page, and the page's report to the rail. */
export function PageFrame({ nav, title, brand, settled, toolbar, sub, hideAsk, error, children }: {
  nav: string;
  title: ReactNode;
  brand?: string | null;
  settled?: string | null;
  toolbar?: ReactNode;
  sub?: ReactNode;
  /** /ask blanks the topbar's box: a page whose body is an ask form does not
   *  want a second one above it. */
  hideAsk?: boolean;
  /** One sentence in a navy flash above the body. */
  error?: string | null;
  children: ReactNode;
}) {
  const report = useContext(FrameContext);
  const { search } = useLocation();
  const b = brand === undefined ? brandOf(search) : brand;
  const s = settled ?? null;
  useEffect(() => { report({ nav, brand: b, settled: s }); }, [report, nav, b, s]);

  return (
    <>
      <header className="topbar">
        <h1>{title}</h1>
        {b && <span className="chip">{b}</span>}
        <span className="spacer" />
        {toolbar}
        {!hideAsk && <TopbarAsk brand={b} />}
        {sub}
      </header>
      <main>
        {error && <div className="flash error">{error}</div>}
        {children}
      </main>
    </>
  );
}

/** Ask, on every page, in the same place. A plain GET form to /ask, so the
 *  field works the same way whatever page it is on. */
function TopbarAsk({ brand }: { brand: string | null }) {
  const [q, setQ] = useState("");
  const action = useMemo(() => `/ask?brand=${encodeURIComponent(brand ?? "")}`, [brand]);
  return (
    <form className="topbar-ask" method="get" action="/ask" role="search">
      <input type="hidden" name="brand" value={brand ?? ""} />
      <Icon name="search" />
      <label htmlFor="topbar-q" className="sr-only">Ask a question about this account</label>
      <input id="topbar-q" name="q" type="text" autoComplete="off" value={q}
             onChange={(e) => setQ(e.target.value)} data-action={action}
             placeholder="Ask about spend, CPA, fatigue…" />
    </form>
  );
}
