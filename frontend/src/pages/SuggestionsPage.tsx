/* SUGGESTIONS. What the account is saying, and what to write next. Ported
 * from templates/suggestions.html.
 *
 * NO BUTTON. scripts/suggest.py publishes one after every import, so the
 * answer is here before the page is opened -- a suggestion somebody has to
 * remember to ask for is a suggestion nobody asks for.
 *
 * THE PROSE SITS ABOVE THE EVIDENCE, and on this page that is right.
 * experiments.html argues the opposite for its Ideas list, because that log
 * is decisions somebody made and the ideas are unfiled guesses. Here the
 * tables are not decisions; they are the INPUT the suggestion was written
 * from, and a reader checking a sentence goes looking for the ad it quotes.
 *
 * THE EVIDENCE is the wording: the cheapest and dearest ads of one goal side
 * by side, headline and opening line quoted, cost per result beside each,
 * one goal at a time behind tabs because cost per result is not comparable
 * across goals. When the live pack has `dimensions_by_goal` for the open
 * tab, the hook / offer / audience labels are read under that goal alone,
 * with a CPA and a relative word; otherwise the old mixed-goal spend panels
 * render. It sits in one closed fold under the report -- it is what a reader
 * checks a sentence against, not what they read first -- and opens itself
 * once a goal tab has been chosen.
 *
 * LESS TO READ (2026-10). Above the campaign groups, one card: "What matters
 * today" is a tile per goal (its CPA move, the evidence, and the campaigns
 * that account for a rise), the summary's computed lines, and the review's
 * one note, labelled as the review's. A goal with no CPA move -- THRUPLAY
 * buys views, not conversions -- gets no tile rather than an empty one.
 * "Recommendations followed up" is one line of counts with the by-kind table
 * folded under it. Beside the card, a count per bucket that jumps to its
 * group. Each panel renders nothing when its field is absent, so an older
 * file renders as it did.
 *
 * Nothing here derives a figure: every number is the pack's or the published
 * file's. The only arithmetic is counting, and a column maximum for a meter.
 */
import { Link, useLocation } from "react-router-dom";
import { PageFrame } from "../components/Layout";
import { Meter } from "../components/charts";
import { Triage } from "../components/Triage";
import { Caveat, usePage } from "../components/ui";
import { withParams, type PageBase } from "../lib/api";
import { ago, f, money, num, pct, signedPct, truncate, words } from "../lib/format";
import "../styles/suggestions.css";

/* ------------------------------------------------------------- the pack */

interface CopyRow {
  ad: string | null;
  headline: string | null;
  body: string | null;
  cpa: number | null;
  spend: number | null;
  conversions: number | null;
}

interface Goal {
  optimization_goal: string;
  ads_run: number;
  rankable_on_cost: boolean;
  ads_with_no_conversions?: number | null;
  cheapest?: CopyRow[] | null;
  dearest?: CopyRow[] | null;
  by_spend_only?: CopyRow[] | null;
}

/** One ads.facet_by_goal row: a label read inside one goal. */
interface GoalDimRow {
  value: string | null;
  spend: number | null;
  conversions: number | null;
  rates?: Record<string, number | null> | null;
  relative?: string | null;
  relative_pct?: number | null;
  evidence_level?: string | null;
}

/** One ads.facet_performance row, trimmed by the pack: mixed goals. */
interface DimRow {
  value: string | null;
  spend?: number | null;
  conversions?: number | null;
}

interface Tiring {
  ad: string | null;
  score: number | null;
}

/* --------------------------------------------------- the published file */

interface Contributor {
  campaign: string;
  contribution_share_pct: number | null;
  cumulative_share_pct?: number | null;
}

interface GoalSummary {
  goal: string;
  cpa_pct: number | null;
  evidence?: string | null;
  contributors?: Contributor[] | null;
  lines?: string[] | null;
}

interface AccountSummary {
  goals?: GoalSummary[] | null;
  lines?: string[] | null;
  note?: string | null;
}

interface Measurement {
  result?: string | null;
  settled_days?: number | null;
  basis?: string | null;
}

interface RecentEntry {
  campaign: string;
  kind?: string | null;
  action?: string | null;
  implemented_on?: string | null;
  match?: string | null;
  measurement?: Measurement | null;
}

type ResultCounts = Record<string, number | null | undefined>;

interface KindRow extends ResultCounts {
  made?: number | null;
  implemented?: number | null;
}

interface Scorecard {
  days?: number | null;
  made?: number | null;
  implemented?: number | null;
  by_result?: ResultCounts | null;
  /** Per kind: made, implemented, and the result counts either flat on the
   *  row or nested under `by_result` -- the template read both shapes. */
  by_kind?: Record<string, KindRow> | null;
  recent?: RecentEntry[] | null;
}

interface Outcomes {
  available?: boolean;
  scorecard?: Scorecard | null;
}

interface FamilyBand {
  family: string;
  audience: string | null;
  optimization_goal: string;
  metric: string;
  p25: number | null;
  p75: number | null;
  campaign_weeks?: number | null;
  since?: string | null;
  until?: string | null;
}

interface TriageEntry {
  bucket?: string | null;
  [k: string]: unknown;
}

export interface Published {
  published_at: string;
  until?: string | null;
  age_hours?: number | null;
  triage?: TriageEntry[] | null;
  suggestion?: string | null;
  account_summary?: AccountSummary | null;
  outcomes?: Outcomes | null;
  family_bands?: FamilyBand[] | null;
  [k: string]: unknown;
}

export interface SuggestionsData extends PageBase {
  d: {
    brand: string;
    days: number;
    until: string;
    goals?: Goal[] | null;
    dimensions?: Record<string, DimRow[] | null> | null;
    dimensions_by_goal?: Record<string, Record<string, GoalDimRow[] | null> | null> | null;
    tiring?: Tiring[] | null;
    settled_through?: string | null;
  };
  days: number;
  published: Published | null;
}

/* --------------------------------------------------------------- helpers */

/** Jinja's `is mapping`: a plain object, not a list, not null. */
function isMap(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}


const KIND: Record<string, string> = {
  creative_test: "Creative test", creative_refresh: "Creative refresh",
  delivery_cut: "Delivery cut", budget: "Budget",
  post_click: "Post-click", other: "Other",
};
const RESULT: Record<string, string> = {
  improved: "improved", neutral: "no clear change",
  worse: "worse", too_early: "too early to read",
};
const RESULT_KEYS = ["improved", "neutral", "worse", "too_early"] as const;
const BUCKETS: [string, string][] = [["act", "Act"], ["test", "Test"], ["monitor", "Monitor"], ["no_change", "No change"]];
const FAM_WORDS: Record<string, string> = {
  franchise: "Franchise", m_and_a: "M&A / book roll", b_roll: "B-roll", hiring: "Hiring", other: "Other",
};
/** 8 is campaign_vs_family's p_min_band_weeks (019), so the table shows
 *  exactly the ranges a campaign card may be read against. */
const BAND_MIN_WEEKS = 8;

/* ---------------------------------------------------- what matters today */

/* `published.account_summary` (contracts2 D4), built by
   creative.account_summary from SQL fields and counts; the one model-written
   sentence is labelled as the review's. Nothing at all when it is absent, so
   an older file renders as it did. A tile per goal that HAS a CPA move: the
   arrow carries the sign, the evidence word its weight, and the campaigns
   that account for a rise are listed with their share -- the last one's
   cumulative share is the "together" figure, read, never added up. The
   goal's own sentence restates the tile, so it is not printed. */
function TodayPanel({ acc }: { acc: AccountSummary }) {
  if (!(acc.goals?.length || acc.lines?.length || acc.note)) return null;
  const tiles = (acc.goals ?? []).filter((gb) => gb.cpa_pct !== null && gb.cpa_pct !== undefined);
  return (
    <section className="today" aria-label="What matters today">
      <h3>What matters today</h3>
      {tiles.length > 0 && (
        <div className="today-goals">
          {tiles.map((gb) => {
            const x = f(gb.cpa_pct) ?? 0;
            const contributors = gb.contributors ?? [];
            const last = contributors[contributors.length - 1];
            return (
              <div className="today-goal" key={gb.goal}>
                <div className="tg-name">{words(gb.goal)}</div>
                <div className="tg-move">{x > 0 ? "↑" : x < 0 ? "↓" : "→"} {pct(Math.abs(x), 2)}<span>CPA, 7 days</span></div>
                {gb.evidence && <span className="ev-p">{words(gb.evidence)} evidence</span>}
                {contributors.length > 0 && (
                  <>
                    <ol className="contrib">
                      {contributors.map((k, i) => (
                        <li key={i}><span className="cn" title={k.campaign}>{k.campaign}</span>
                          <Meter value={k.contribution_share_pct} top={100} width={50} /><b>{pct(k.contribution_share_pct, 1)}</b></li>
                      ))}
                    </ol>
                    {last.cumulative_share_pct !== undefined && last.cumulative_share_pct !== null && (
                      <p className="tg-tot">Together, {pct(last.cumulative_share_pct, 1)} of the rise</p>
                    )}
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
      {/* The lines name diagnose's patterns by their identifiers
          (possible_fatigue); a reader sees words. */}
      {acc.lines && acc.lines.length > 0 && (
        <ul className="today-facts">{acc.lines.map((l, i) => <li key={i}>{l.replace(/_/g, " ")}</li>)}</ul>
      )}
      {acc.note && <p className="today-note"><span className="pill quiet">The review’s read</span><span>{acc.note}</span></p>}
    </section>
  );
}

/* --------------------------------------------- recommendations followed up */

/* `published.outcomes.scorecard` (D3): counts of what the account shows was
   done and what the numbers read afterwards. A reading, never a conclusion
   -- nothing here writes ads.experiment.conclusion. One line of counts; the
   results appear once something has been carried out, and the by-kind table
   and the last measured entries fold under it. */
function FollowedPanel({ oc }: { oc: Outcomes | null }) {
  if (!isMap(oc)) return null;
  const sc: Scorecard = isMap(oc.scorecard) ? oc.scorecard : {};
  const br: ResultCounts = isMap(sc.by_result) ? sc.by_result : {};
  const byKind = isMap(sc.by_kind) ? sc.by_kind : {};
  const kinds = Object.entries(byKind).filter((kv): kv is [string, KindRow] => isMap(kv[1]));
  const recent = sc.recent ?? [];
  return (
    <section className="followed" aria-label="Recommendations followed up">
      <div className="fu-row">
        <strong>Followed up, last {num(sc.days || 90)} days</strong>
        <span className="fu"><b>{num(sc.made || 0)}</b>recommended</span>
        <span className="fu"><b>{num(sc.implemented || 0)}</b>carried out</span>
        {sc.implemented ? RESULT_KEYS.map((key) => (
          <span className="fu" key={key}><b>{num(br[key] || 0)}</b>{RESULT[key]}</span>
        )) : null}
        <span className="pill quiet">a reading, not a conclusion</span>
      </div>
      {sc.implemented && (kinds.length > 0 || recent.length > 0) ? (
        <details className="fu-more"><summary>By kind, and the last measured</summary>
          {kinds.length > 0 && (
            <div className="table-wrap"><table>
              <thead><tr><th>Kind</th><th className="num">Made</th><th className="num">Implemented</th>
                <th className="num">Improved</th><th className="num">No clear change</th>
                <th className="num">Worse</th><th className="num">Too early</th></tr></thead>
              <tbody>
                {kinds.map(([kind, v]) => {
                  const nested: unknown = (v as Record<string, unknown>).by_result;
                  const r: ResultCounts = isMap(nested) ? (nested as ResultCounts) : v;
                  return (
                    <tr key={kind}><td>{KIND[kind] ?? words(kind)}</td>
                      <td className="num">{num(v.made || 0)}</td><td className="num">{num(v.implemented || 0)}</td>
                      <td className="num">{num(r.improved || 0)}</td><td className="num">{num(r.neutral || 0)}</td>
                      <td className="num">{num(r.worse || 0)}</td><td className="num">{num(r.too_early || 0)}</td></tr>
                  );
                })}
              </tbody>
            </table></div>
          )}
          {recent.length > 0 && (
            <ul className="recent">
              {recent.map((e, i) => {
                const m: Measurement = isMap(e.measurement) ? e.measurement : {};
                return (
                  <li key={i}><b>{e.campaign}</b> · {e.action || (KIND[e.kind ?? ""] ?? "--")}
                    {" "}<span className="muted">· implemented {e.implemented_on || "--"}
                      {e.match ? ` (${e.match === "person" ? "set by a person" : e.match})` : ""}
                      {m.result ? <> · <b>{RESULT[m.result] ?? m.result}</b></> : null}
                      {m.settled_days !== undefined && m.settled_days !== null ? ` after ${m.settled_days} settled days` : ""}</span>
                    {m.basis && <div className="small muted">{m.basis}</div>}</li>
                );
              })}
            </ul>
          )}
        </details>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------ the wording */

/* One label table for one goal. Every figure is facet_by_goal's; the page
   only formats and picks the word's emphasis. */
function GoalDimTable({ title, rows }: { title: string; rows: GoalDimRow[] | null | undefined }) {
  const list = rows ?? [];
  return (
    <section className="card" aria-label={title}>
      <div className="card-head"><h2 className="card-t">{title}</h2></div>
      {!list.length ? (
        <p className="small muted" style={{ margin: "8px 0 0" }}>No ad under this goal carries this label yet.</p>
      ) : (
        <div className="table-wrap"><table>
          <thead><tr><th>Label</th><th className="num">Spend</th><th className="num">Conv</th>
            <th className="num">CPA</th><th>Relative</th></tr></thead>
          <tbody>
            {list.slice(0, 8).map((r, i) => {
              const rates = isMap(r.rates) ? r.rates : {};
              return (
                <tr key={i}><td className="name">{r.value === null || r.value === undefined ? <span className="muted">Not tagged</span> : r.value}</td>
                  <td className="num">{money(r.spend)}</td>
                  <td className="num">{num(r.conversions)}</td>
                  <td className="num">{money(rates.cpa)}</td>
                  <td>{r.relative ? <span className={`rel ${r.relative}`}>{r.relative}</span> : <span className="muted">--</span>}
                    {r.relative_pct !== undefined && r.relative_pct !== null ? <> <span className="small">{signedPct(r.relative_pct)}</span></> : null}
                    {r.evidence_level === "insufficient" || r.evidence_level === "directional"
                      ? <span className="ev">{r.evidence_level}</span> : null}</td></tr>
              );
            })}
          </tbody>
        </table></div>
      )}
    </section>
  );
}

function CopyCard({ r, top }: { r: CopyRow; top: number | null }) {
  const body = (r.body ?? "").replace(/\n/g, " ").trim();
  return (
    <div className="copy-card">
      <div className="copy-text">
        <div className="copy-ad">{r.ad || "--"}</div>
        <div className="copy-head">{r.headline || "--"}</div>
        {body && <div className="copy-body">“{truncate(body, 130)}”</div>}
      </div>
      <div className="copy-cost">
        {/* money(None) is "--" where money(0) would be "$0.00": a null CPA is
            undefined, not free. */}
        <div className="v">{money(r.cpa)}</div>
        <div className="n">{money(r.spend)} · {num(r.conversions)} conv</div>
        {r.cpa !== null && r.cpa !== undefined && <div style={{ marginTop: 5 }}><Meter value={r.cpa} top={top} width={90} /></div>}
      </div>
    </div>
  );
}

/* WHAT THE WRITING IS DOING, mixed goals. Spend by label, from
   ads.facet_performance. The labels ran under several goals, so the function
   declines to rank them on cost -- that is a refusal to compare two things
   measured differently, not a missing number. Shown only when the live pack
   has no per-goal tables for the open tab. */
function DimPanel({ title, rows }: { title: string; rows: DimRow[] | null | undefined }) {
  const list = (rows ?? []).filter((r) => r.value !== null && r.value !== undefined && r.value !== "none");
  const tops = list.slice(0, 5).map((r) => f(r.spend)).filter((v): v is number => v !== null);
  const top = tops.length ? Math.max(...tops) : null;
  return (
    <section className="card" aria-label={title}>
      <div className="card-head"><h2 className="card-t">{title}</h2></div>
      {!list.length ? (
        <p className="small muted" style={{ margin: "8px 0 0" }}>No ad carries this label yet.</p>
      ) : list.slice(0, 5).map((r, i) => (
        <div className="dim-row" key={i}>
          <span className="dim-v">{r.value}</span>
          <Meter value={r.spend} top={top} width={100} />
          <strong>{money(r.spend)}</strong>
          <span className="muted">{num(r.conversions)} conv</span>
        </div>
      ))}
    </section>
  );
}

/* --------------------------------------------------------- typical ranges */

/* `published.family_bands` (contracts-baselines E2): the middle half
   (p25–p75) of campaign-weeks per campaign family and goal over the last 26
   weeks, from ads.family_baseline, all audiences. Context for a campaign's
   own windows, never a target, and never read across goals: each row is one
   family under one goal. The page groups and formats; the figures are the
   SQL's. Closed until opened, inside a fold that is too. A range from fewer
   than BAND_MIN_WEEKS campaign-weeks prints "too few weeks" instead: one
   week's figure is its own p25 and p75 ($70.97–$70.97), which reads as a
   range and is not one. */
function bandCell(rows: FamilyBand[], metric: string) {
  const b = rows.find((r) => r.metric === metric);
  if (b && b.p25 !== null && b.p25 !== undefined && (b.campaign_weeks || 0) < BAND_MIN_WEEKS) {
    return <span className="muted" title={`${num(b.campaign_weeks || 0)} campaign-week(s); a range needs ${BAND_MIN_WEEKS}`}>too few weeks</span>;
  }
  if (b && b.p25 !== null && b.p25 !== undefined && b.p75 !== null && b.p75 !== undefined) {
    return metric === "link_ctr" || metric === "conversion_rate"
      ? `${num(b.p25, 2)}–${pct(b.p75, 2)}`
      : `${money(b.p25)}–${money(b.p75)}`;
  }
  return "--";
}

function FamilyBands({ fbands }: { fbands: FamilyBand[] }) {
  const fkeys: [string, string][] = [];
  for (const b of fbands) {
    if (!fkeys.some(([fam, goal]) => fam === b.family && goal === b.optimization_goal)) {
      fkeys.push([b.family, b.optimization_goal]);
    }
  }
  return (
    <details className="fu-more family-bands" aria-label="Typical ranges">
      <summary>Typical ranges by kind of campaign</summary>
      <p className="small muted" style={{ margin: "8px 0 0" }}>The middle half of campaign-weeks for each kind
        of campaign under one goal{fbands[0].since ? `, ${fbands[0].since} – ${fbands[0].until}` : ""}.
        Context for a campaign’s own weeks, not a target; never compare across goals.
        Source: <code>ads.family_baseline</code></p>
      <div className="table-wrap"><table>
        <thead><tr><th>Kind</th><th>Goal</th><th className="num">CPA</th><th className="num">Link CTR</th>
          <th className="num">CVR</th><th className="num">CPM</th><th className="num" title="The largest sample among the ranges on the row">Campaign-weeks</th></tr></thead>
        <tbody>
          {fkeys.map(([fam, goal]) => {
            const rows = fbands.filter((b) => b.family === fam && b.optimization_goal === goal);
            const ns = rows.map((r) => f(r.campaign_weeks)).filter((v): v is number => Boolean(v));
            return (
              <tr key={`${fam}/${goal}`}><td className="name">{FAM_WORDS[fam] ?? words(fam)}</td><td>{words(goal)}</td>
                <td className="num">{bandCell(rows, "cpa")}</td>
                <td className="num">{bandCell(rows, "link_ctr")}</td>
                <td className="num">{bandCell(rows, "conversion_rate")}</td>
                <td className="num">{bandCell(rows, "cpm")}</td>
                <td className="num">{ns.length ? num(Math.max(...ns)) : "--"}</td></tr>
            );
          })}
        </tbody>
      </table></div>
    </details>
  );
}

/* ---------------------------------------------------------------- the page */

export function SuggestionsPage() {
  const page = usePage<SuggestionsData>("/suggestions", "suggestions", "Suggestions");
  const { search } = useLocation();
  if (page.gate) return page.gate;
  const data = page.data;
  const { d, published } = data;

  // ------------------------------------------------------------ the AI read
  const tri = published?.triage?.length ? published.triage : [];
  const bucketed = tri.filter((t) => t.bucket);
  const acc: AccountSummary = published && isMap(published.account_summary) ? published.account_summary : {};
  const oc: Outcomes | null = published && isMap(published.outcomes) ? published.outcomes : null;
  const hasToday = Boolean(acc.goals?.length || acc.lines?.length || acc.note);

  // ------------------------------------------------------- the wording it quotes
  const asked = new URLSearchParams(search).get("goal");
  const goals = d.goals ?? [];
  const g = (asked !== null && goals.find((x) => x.optimization_goal === asked)) || goals[0];
  const dbg = isMap(d.dimensions_by_goal) ? d.dimensions_by_goal : {};
  const gslot = g ? dbg[g.optimization_goal] : null;
  const gdims = g && isMap(gslot) ? gslot : null;
  // Mixed-goal spend panels only when the open tab has no per-goal tables.
  const dims = gdims ? {} : (d.dimensions ?? {});

  const fbands = (published?.family_bands?.length ? published.family_bands : []).filter((b) => b.audience === null);
  const wearing = (d.tiring ?? []).filter((r) => { const s = f(r.score); return s !== null && s >= 2; });

  const toolbar = <span className="chip">{d.days} days to {d.until}</span>;

  return (
    <PageFrame nav="suggestions" title="Suggestions" brand={d.brand} settled={data.settled} toolbar={toolbar}>
      {/* ============================================================ THE AI READ */}
      <section className="ai-read" data-shape={tri.length ? "triage" : undefined} aria-label="Worth writing next">
        <div className="ai-read-head">
          <span className="pill stop">AI read</span>
          <span className="state">
            {published
              ? <>written {String(published.published_at ?? "").slice(0, 10)}
                  {published.age_hours !== null && published.age_hours !== undefined ? `, ${ago(published.age_hours)}` : ""}
                  {published.until ? `, on the window to ${published.until}` : ""}</>
              : "nothing published yet"}
          </span>
        </div>
        {/* Two shapes of published answer. Newer files carry `triage` -- one
            card per campaign, rendered by <Triage> -- and older ones carry
            `suggestion`, prose. Either way it lands inside this block, under
            the same pill and stamp. */}
        {tri.length ? (
          <>
            {(hasToday || oc || bucketed.length > 0) && (
              <div className={`today-grid${bucketed.length ? "" : " solo"}`}>
                {(hasToday || oc) && (
                  <div className="today-card">
                    <TodayPanel acc={acc} />
                    <FollowedPanel oc={oc} />
                  </div>
                )}
                {bucketed.length > 0 && (
                  <nav className="bk-count" aria-label="Campaigns by action">
                    {BUCKETS.map(([key, label]) => (
                      <a href={`#${key}`} key={key}><span className={`bk ${key}`}>{label}</span>
                        <b>{bucketed.filter((t) => t.bucket === key).length}</b></a>
                    ))}
                  </nav>
                )}
              </div>
            )}
            <Triage published={published!} />
          </>
        ) : published && published.suggestion ? (
          published.suggestion.split("\n\n").filter((p) => p.trim()).map((p, i) => <p key={i}>{p.trim()}</p>)
        ) : (
          <p className="muted">No suggestion has been published for this brand yet. One is
            written after every import — <span className="mono">python scripts/sync.py</span>
            {" "}runs it, or <span className="mono">python scripts/suggest.py --brand {d.brand}</span>
            {" "}publishes one on its own. The evidence below is live either way.</p>
        )}
        {/* The answer is dated -- the prose or the triage alike -- and the
            evidence under it is live. When an import has landed since, the two
            disagree, and the reader is told which moved. */}
        {published && (tri.length || published.suggestion) && published.until && published.until !== String(d.until) && (
          <Caveat style={{ margin: "12px 0 0" }}>
            Written against the window ending <strong>{published.until}</strong>;
            the evidence below now runs to <strong>{d.until}</strong>. The next
            import republishes it.
          </Caveat>
        )}
      </section>

      {/* ===================================================== THE WORDING IT QUOTES */}
      <details className="evidence-fold" open={Boolean(asked)}>
        <summary><strong>The evidence</strong>
          {goals.length > 0 && <span className="c">{goals.length} goal{goals.length === 1 ? "" : "s"}</span>}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4"
               strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg></summary>
        <div className="ef-body">
          <section className="stage" id="evidence" aria-label="The evidence it quotes">
            <div className="stage-head">
              <h2>The wording it quotes</h2>
            </div>
            {!goals.length || !g ? (
              <div className="empty">No ads spent in this window, so there is nothing to read the copy against.</div>
            ) : (
              <>
                <nav className="tabs" aria-label="Optimization goal">
                  {goals.map((x) => (
                    <Link key={x.optimization_goal}
                          to={`/suggestions${withParams(search, { goal: x.optimization_goal })}#evidence`}
                          aria-current={x.optimization_goal === g.optimization_goal ? "page" : undefined}>
                      {words(x.optimization_goal)}<span className="c">{x.ads_run}</span>
                    </Link>
                  ))}
                </nav>

                {g.rankable_on_cost ? (() => {
                  const cpas = [...(g.cheapest ?? []), ...(g.dearest ?? [])].map((r) => f(r.cpa))
                    .filter((v): v is number => Boolean(v));
                  const top = cpas.length ? Math.max(...cpas) : null;
                  return (
                    <>
                      <div className="boards">
                        <section className="board" aria-label="Cheapest per result">
                          <header><h2>Cheapest per result</h2></header>
                          {(g.cheapest ?? []).map((r, i) => <CopyCard key={i} r={r} top={top} />)}
                        </section>
                        <section className="board" aria-label="Dearest per result">
                          <header><h2>Dearest per result</h2></header>
                          {(g.dearest ?? []).map((r, i) => <CopyCard key={i} r={r} top={top} />)}
                        </section>
                      </div>
                      {g.ads_with_no_conversions ? (
                        <p className="sub" style={{ marginTop: 8 }}>{num(g.ads_with_no_conversions)} ad(s)
                          under {words(g.optimization_goal).toLowerCase()} spent without converting and are not ranked.</p>
                      ) : null}
                    </>
                  );
                })() : (
                  <div className="board" aria-label="By spend">
                    <header><h2>By spend — nothing converted under {words(g.optimization_goal).toLowerCase()}, so there is no cost ranking</h2></header>
                    {(g.by_spend_only ?? []).map((r, i) => <CopyCard key={i} r={r} top={null} />)}
                  </div>
                )}

                {/* WHAT THE WRITING IS DOING, under this goal only.
                    ads.facet_by_goal (018) reads each label inside one goal, so
                    a cost per result and a relative word are fair here:
                    "baseline" is the goal's largest-spend label, and better /
                    similar / worse is the SQL's own comparison with the goal's
                    CPA. Few conversions are marked rather than hidden. */}
                {gdims && (
                  <div className="goal-dims" aria-label="What the writing is doing under this goal">
                    <div className="stage-head">
                      <h2>What the writing is doing</h2>
                      <span className="state">under {words(g.optimization_goal).toLowerCase()} only ·
                        relative to this goal’s CPA · Source: <code>ads.facet_by_goal</code></span>
                    </div>
                    <div className="grid">
                      <GoalDimTable title="Hook it opens with" rows={gdims.hook} />
                      <GoalDimTable title="Offer it asks for" rows={gdims.offer} />
                      <GoalDimTable title="Who it addresses" rows={gdims.audience} />
                    </div>
                  </div>
                )}
              </>
            )}
          </section>

          {Object.keys(dims).length > 0 && (
            <section className="stage" aria-label="What the writing is doing">
              <div className="stage-head">
                <h2>What the writing is doing</h2>
                <span className="state">spend by label · mixed goals, so no cost ranking ·
                  Source: <code>ads.facet_performance</code></span>
              </div>
              <div className="grid">
                <DimPanel title="Hook it opens with" rows={dims.hook} />
                <DimPanel title="Offer it asks for" rows={dims.offer} />
                <DimPanel title="Who it addresses" rows={dims.audience} />
              </div>
            </section>
          )}

          {fbands.length > 0 && <FamilyBands fbands={fbands} />}
        </div>
      </details>

      {/* One line where the Fatigue table used to be copied. */}
      {wearing.length > 0 && (
        <div className="card" style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px" }}>
          <span><strong>{num(wearing.length)}</strong> ad(s) show two or more fatigue
            symptoms and need new copy first.</span>
          <Link to={`/creative?brand=${encodeURIComponent(d.brand)}`} style={{ marginLeft: "auto", fontWeight: 600 }}>Open Fatigue →</Link>
        </div>
      )}
    </PageFrame>
  );
}
