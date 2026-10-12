/* OVERVIEW. The live view -- the brief is the dated read, this is "what is
 * happening in this window, right now". Ported from templates/overview.html.
 *
 * The table is one goal at a time, behind tabs that carry each goal's count:
 * a CPA column that sets a cost per lead beside a cost per thruplay is ranked
 * by the reader's eye whether or not the page means it to. Inside one goal
 * the column IS comparable, and the bar beside each figure is allowed to mean
 * something.
 *
 * Nothing here derives a figure. `agg` is one brand row from
 * ads.window_metrics and `deltas` is ads.compare at brand level; summing the
 * per-ad rows and dividing is the trap ads.rate exists to prevent.
 */
import { Fragment } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { PageFrame } from "../components/Layout";
import { AskBox } from "../components/AskBox";
import { LineChart, Meter } from "../components/charts";
import { AdLink, Caveat, Dash, Empty, More, Seg, StageHead, usePage } from "../components/ui";
import { withParams, type PageBase } from "../lib/api";
import { money, num, pct, words } from "../lib/format";

interface Row {
  entity_key: string;
  entity_name: string | null;
  platform_id: string | null;
  optimization_goal: string | null;
  format: string | null;
  days: number | null;
  spend: number | null;
  conversions: number | null;
  rates: Record<string, number | null> | null;
  angle_name?: string | null;
  review_overall?: number | null;
}

export interface OverviewData extends PageBase {
  d: { brand: string; days: number; since: string; until: string; rows: Row[]; row_count: number };
  agg: {
    spend: number | null; conversions: number | null; impressions: number | null;
    rates: Record<string, number | null>; mixed_currency: boolean;
  };
  deltas: {
    spend: number | null; conversions: number | null; cpa: number | null; link_ctr: number | null;
    spend_pct: number | null; conversions_pct: number | null; cpa_pct: number | null;
    appeared: number; disappeared: number;
  };
  level: string;
  days: number;
  first_day: string | null;
  prior_since: string;
  spend_series: [string, number | null][];
  conv_series: [string, number | null][];
}

const LEVELS: [string, string][] = [["ad", "Ad"], ["ad_group", "Ad group"], ["campaign", "Campaign"]];

function dir(v: number): string {
  return v > 0 ? "up" : v < 0 ? "down" : "flat";
}

export function OverviewPage() {
  const page = usePage<OverviewData>("/", "overview", "Overview");
  const { search } = useLocation();
  const navigate = useNavigate();
  if (page.gate) return page.gate;
  const data = page.data;

  const { d, agg, deltas, level, days } = data;
  const r_ = agg.rates ?? {};
  const priorPartial = Boolean(data.first_day && data.prior_since && data.first_day > data.prior_since);

  // Grouped by goal, largest first, so the default tab is the one most of the
  // money went through. A row with no goal recorded groups under ''.
  const by = new Map<string, Row[]>();
  for (const r of d.rows) {
    const k = r.optimization_goal ?? "";
    if (!by.has(k)) by.set(k, []);
    by.get(k)!.push(r);
  }
  const groups = [...by.entries()].map(([k, rows]) => [k, rows, rows.length] as const)
    .sort((a, b) => b[2] - a[2]);
  const asked = new URLSearchParams(search).get("goal");
  const active = asked !== null && by.has(asked) ? asked : (groups[0]?.[0] ?? "");
  const rows = by.get(active) ?? [];
  const converted = rows.filter((r) => r.conversions);
  const tabbed = groups.length > 1 && level !== "campaign";
  const showAngle = d.rows.some((r) => r.angle_name);
  const showReview = d.rows.some((r) => r.review_overall);

  const shown = rows.slice(0, 8);
  const cpas = shown.map((r) => r.rates?.cpa).filter((v): v is number => Boolean(v));
  const top = cpas.length ? Math.max(...cpas) : null;

  const toolbar = (
    <>
      <Seg param="days" options={[7, 14, 28, 90]} current={days} />
      <div className="filters" style={{ padding: 0, margin: 0, border: 0, background: "none" }}>
        <label htmlFor="level">Level
          <select id="level" value={level}
                  onChange={(e) => navigate(`/${withParams(search, { level: e.target.value, goal: null })}`)}>
            {LEVELS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        </label>
      </div>
    </>
  );

  const row = (r: Row) => {
    const rr = r.rates ?? {};
    return (
      <tr key={r.entity_key}>
        <td className="name">
          {level === "ad"
            ? <AdLink adKey={r.entity_key} brand={d.brand}>{r.entity_name || r.platform_id}</AdLink>
            : (r.entity_name || r.platform_id)}
          <div className="why">{r.format ? `${words(r.format)} · ` : ""}{num(r.days)} day(s)</div>
        </td>
        {showAngle && <td>{r.angle_name ? <span className="pill quiet">{r.angle_name}</span> : <Dash />}</td>}
        <td className="num">{money(r.spend)}</td>
        <td className="num">{num(r.conversions)}</td>
        {/* NULL, never 0: an ad that did not convert has no CPA, and a zero
            would sort to the top of the column and read as the best ad. */}
        <td className="num"><div className="num-bar"><Meter value={rr.cpa} top={top} />
          <strong style={{ minWidth: 58 }}>{rr.cpa ? money(rr.cpa) : <Dash />}</strong></div></td>
        <td className="num">{rr.link_ctr ? pct(rr.link_ctr) : <Dash />}</td>
        <td className="num">{rr.cpm ? money(rr.cpm) : <Dash />}</td>
        {showReview && <td className="num">{r.review_overall ? num(r.review_overall, 1) : <Dash />}</td>}
      </tr>
    );
  };

  const head = (
    <thead><tr>
      <th scope="col">{level === "ad" ? "Ad" : words(level)}</th>
      {showAngle && <th scope="col">Angle</th>}
      <th scope="col" className="num">Spend</th>
      <th scope="col" className="num">Conv</th>
      <th scope="col" className="num">CPA</th>
      <th scope="col" className="num">Link CTR</th>
      <th scope="col" className="num">CPM</th>
      {showReview && <th scope="col" className="num">Review</th>}
    </tr></thead>
  );

  return (
    <PageFrame nav="overview" title="Overview" brand={d.brand} settled={data.settled} toolbar={toolbar}>
      {agg.mixed_currency && (
        <Caveat><strong>Mixed currency.</strong> This brand's accounts do not all report in one
          currency, so the totals below add different units together. Filter to one account
          before reading them.</Caveat>
      )}

      <section className="answer" aria-label="The window in one line">
        <p><strong>{money(agg.spend)}</strong> bought <strong>{num(agg.conversions)}</strong> conversions
          at <strong>{money(r_.cpa)}</strong> over the {d.days} days to {d.until}.
          {tabbed && rows.length > 0 && (
            <> <strong>{num(converted.length)}</strong> of the <strong>{num(rows.length)}</strong>{" "}
              {words(active).toLowerCase()} ads converted at all.</>
          )}
          {(deltas.appeared || deltas.disappeared) ? (
            <> {num(deltas.appeared)} started and {num(deltas.disappeared)} stopped.</>
          ) : null}
        </p>
        <div className="jumps">
          <a href="#goals">By optimization goal</a>
          <a href="#daily">Spend per day</a>
          <Link to={`/why?brand=${encodeURIComponent(d.brand)}`}>Why CPA moved</Link>
        </div>
      </section>

      {/* Spend and conversions are withheld when the prior window starts before
          the first day this brand has data for: a delta against a window the
          import had barely reached is a fact about the importer. */}
      <div className="stats">
        <div className="stat"><div className="k">Spend</div>
          <div className="v">{money(agg.spend)}</div>
          {priorPartial
            ? <div className="delta flat"><span className="pill warn">prior window incomplete</span></div>
            : deltas.spend !== null && (
              <div className={`delta ${dir(deltas.spend)}`}>{money(deltas.spend)} vs prior {d.days}d</div>
            )}
        </div>
        <div className="stat"><div className="k">Conversions</div>
          <div className="v">{num(agg.conversions)}</div>
          {priorPartial
            ? <div className="delta flat"><span className="pill warn">prior window incomplete</span></div>
            : deltas.conversions !== null && (
              <div className={`delta ${deltas.conversions > 0 ? "up good" : deltas.conversions < 0 ? "down bad" : "flat"}`}>
                {num(deltas.conversions)} vs prior {d.days}d</div>
            )}
        </div>
        <div className="stat"><div className="k">CPA</div>
          <div className="v">{money(r_.cpa)}</div>
          {deltas.cpa !== null
            ? <div className={`delta ${deltas.cpa < 0 ? "down good" : deltas.cpa > 0 ? "up bad" : "flat"}`}>
                {money(deltas.cpa)}{deltas.cpa_pct !== null ? ` (${pct(deltas.cpa_pct)})` : ""}</div>
            : <div className="sub">no prior CPA — nothing converted in one window</div>}
        </div>
        <div className="stat"><div className="k">Link CTR</div>
          <div className="v">{pct(r_.link_ctr)}</div>
          {deltas.link_ctr !== null && (
            // Percentage POINTS: pct() would print a 0.09-point move as "0.09%".
            <div className={`delta ${deltas.link_ctr > 0 ? "up good" : deltas.link_ctr < 0 ? "down bad" : "flat"}`}>
              {num(deltas.link_ctr, 2)} pp</div>
          )}
        </div>
        <div className="stat"><div className="k">CPM</div>
          <div className="v">{money(r_.cpm)}</div>
          <div className="sub">{num(agg.impressions)} impressions</div>
        </div>
      </div>

      {/* Two charts, never one with two y-axes. */}
      <div className="boards" id="daily" style={{ marginBottom: 20 }}>
        <section className="board" aria-label="Spend per day">
          <header><h2>Spend per day</h2></header>
          <div className="body"><LineChart points={data.spend_series} kind="spend" /></div>
        </section>
        <section className="board" aria-label="Conversions per day">
          <header><h2>Conversions per day</h2></header>
          <div className="body"><LineChart points={data.conv_series} kind="count" /></div>
        </section>
      </div>

      <section className="stage" id="goals" aria-label="By optimization goal">
        <StageHead title={`By ${tabbed ? "optimization goal" : level.replace("_", " ")}`} source="ads.window_metrics" />
        {!d.rows.length ? (
          <Empty>Nothing imported for this window. A pull is a command somebody runs, not
            something this page fires: <code>python -m meta_ads --pull --brand {d.brand}</code></Empty>
        ) : (
          <>
            {tabbed && (
              <nav className="tabs" aria-label="Optimization goal">
                {groups.map(([k, , n]) => (
                  <Link key={k} to={`/${withParams(search, { goal: k })}#goals`}
                        aria-current={k === active ? "page" : undefined}>
                    {k ? words(k) : "No goal recorded"}<span className="c">{n}</span>
                  </Link>
                ))}
              </nav>
            )}
            <div className="table-wrap" style={{ borderBottomLeftRadius: 0, borderBottomRightRadius: 0 }}>
              <table>{head}<tbody>{shown.map(row)}</tbody></table>
            </div>
            {rows.length > 8 && (
              <More style={{ background: "var(--surface)", border: "1px solid var(--line)", borderTop: 0,
                             borderRadius: "0 0 var(--radius) var(--radius)" }}
                    summary={<><strong>Show all {rows.length}</strong> — {num(rows.length - converted.length)} of
                      them spent without converting, so they carry no CPA.
                      {d.row_count >= 200 && <> These are the 200 highest-spend {level.replace("_", " ")}s in the window.</>}</>}>
                <div className="table-wrap" style={{ border: 0 }}>
                  <table><tbody>{rows.slice(8).map(row)}</tbody></table>
                </div>
              </More>
            )}
          </>
        )}
      </section>

      <Fragment>
        <div className="grid" style={{ marginTop: 20 }}>
          <div className="span2"><AskBox brand={d.brand} /></div>
        </div>
      </Fragment>
    </PageFrame>
  );
}
