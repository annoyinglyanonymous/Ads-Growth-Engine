/* FATIGUE. Five named symptoms and their count, each ad's most recent window
 * against the one before. The score is a count, not a weighting. Ported from
 * templates/creative.html.
 *
 * Two pictures sit above the table. The first is how many symptoms each
 * readable ad carries -- the shape of the account's fatigue in one bar. The
 * second is WHICH symptom fires, counted across the ads: an account that is
 * mostly losing click-through needs new openings, and one that is mostly
 * paying more per thousand needs new audiences, and a table of ads cannot say
 * which of those this is.
 *
 * The symptom pills are the same five-cell grid the brief uses. Position says
 * which symptom, fill says whether it fired, and the count of filled cells is
 * the score -- so there is no score column, and no paragraph about daily
 * frequency: the column header says "Daily freq", which is the whole of
 * CLAUDE.md's rule, and the column is where a reader needs it.
 *
 * The table shows the ads carrying a symptom, eight at a time, and says what
 * is behind the cut. Ads with no symptom are counted, not listed.
 */
import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { PageFrame } from "../components/Layout";
import { Meter, Sparkline, SplitBar } from "../components/charts";
import { AdLink, Empty, More, Seg, StageHead, usePage } from "../components/ui";
import { withParams, type PageBase } from "../lib/api";
import { money, num, words } from "../lib/format";

/** One side of ads.fatigue's comparison: the window's rates and its mean daily frequency. */
interface Side {
  rates?: Record<string, number | null> | null;
  daily_frequency_avg?: number | null;
}

interface FatigueRow {
  ad_key: string;
  entity_name: string | null;
  optimization_goal: string | null;
  spend_recent: number | null;
  confident: boolean | null;
  link_ctr_decline: boolean | null;
  cpm_rise: boolean | null;
  cpa_rise: boolean | null;
  frequency_rise: boolean | null;
  ranking_drop: boolean | null;
  score: number | null;
  recent: Side | null;
  prior: Side | null;
}

export interface CreativeData extends PageBase {
  d: {
    brand: string;
    rows: FatigueRow[];
    suppressed_unconfident: number | null;
    window_days?: number;
  };
  window: number;
  min_spend: number;
  unconfident: number;
  /** ad_key -> link_ctr per day of the recent window, from ads.ad_daily_rates. */
  spark: Record<string, (number | null)[]>;
}

/** The same five cells the brief draws. */
function SymGrid({ r }: { r: FatigueRow }) {
  const flags: [unknown, string][] = [
    [r.link_ctr_decline, "click-through down"], [r.cpm_rise, "impression cost up"],
    [r.cpa_rise, "acquisition cost up"], [r.frequency_rise, "daily frequency up"],
    [r.ranking_drop, "ranking down"],
  ];
  const names = flags.filter(([on]) => on).map(([, name]) => name);
  return (
    <div className="sym" role="img" aria-label={names.length ? names.join(", ") : "no symptoms"}>
      {flags.map(([on, name]) => <span key={name} className={on ? "on" : undefined} title={name} />)}
    </div>
  );
}

/** The filter form: the GET to /creative became a navigation with the same
 *  query. The select applies at once; the text field applies on Apply or
 *  Enter, because a window that refetches on every keystroke is unreadable. */
function Filters({ minSpend, unconfident }: { minSpend: number; unconfident: number }) {
  const { search } = useLocation();
  const navigate = useNavigate();
  const [spend, setSpend] = useState(String(minSpend));
  const go = (min: string, unc: string) =>
    navigate(`/creative${withParams(search, { min_spend: min, unconfident: unc })}`);
  const apply = (e: FormEvent) => { e.preventDefault(); go(spend, String(unconfident)); };
  return (
    <form className="filters" style={{ padding: 0, margin: 0, border: 0, background: "none" }} onSubmit={apply}>
      <label htmlFor="min_spend">Min spend
        <input id="min_spend" name="min_spend" value={spend} size={5} onChange={(e) => setSpend(e.target.value)} />
      </label>
      <label htmlFor="unconfident">Low volume
        <select id="unconfident" name="unconfident" value={unconfident ? "1" : "0"}
                onChange={(e) => go(spend, e.target.value)}>
          <option value="0">hidden</option>
          <option value="1">shown</option>
        </select>
      </label>
      <button type="submit" className="btn ghost">Apply</button>
    </form>
  );
}

export function CreativePage() {
  const page = usePage<CreativeData>("/creative", "creative", "Fatigue");
  if (page.gate) return page.gate;
  const data = page.data;
  const { d, unconfident, spark } = data;
  const win = data.window;

  // Counted, never summed: how many ads carry how many symptoms, and how many
  // ads each symptom fired on. Tallying the rows a function returned is not
  // computing a rate.
  const sc = { three: 0, two: 0, one: 0, none: 0 };
  const fired = { ctr: 0, cpm: 0, cpa: 0, frq: 0, rnk: 0 };
  for (const r of d.rows) {
    if ((r.score || 0) >= 3) sc.three += 1;
    else if (r.score === 2) sc.two += 1;
    else if (r.score === 1) sc.one += 1;
    else sc.none += 1;
    if (r.link_ctr_decline) fired.ctr += 1;
    if (r.cpm_rise) fired.cpm += 1;
    if (r.cpa_rise) fired.cpa += 1;
    if (r.frequency_rise) fired.frq += 1;
    if (r.ranking_drop) fired.rnk += 1;
  }
  const symptoms: [string, number][] = [
    ["Click-through down", fired.ctr], ["Acquisition cost up", fired.cpa],
    ["Impression cost up", fired.cpm], ["Daily frequency up", fired.frq],
    ["Ranking down", fired.rnk],
  ];
  // A stable sort, descending: on a tie the first listed wins, as Jinja's did.
  const most = symptoms.reduce((best, s) => (s[1] > best[1] ? s : best), symptoms[0]);
  const topFired = most[1];
  const shown = d.rows.filter((r) => r.score);
  const legend: [string, number, string][] = [
    ["3+ symptoms", sc.three, "--accent"], ["2", sc.two, "--accent-hi"],
    ["1", sc.one, "--focus"], ["none", sc.none, "--accent-line"],
  ];

  const toolbar = (
    <>
      <Seg param="window" options={[3, 7, 14]} current={win} />
      <Filters key={`${data.min_spend}:${unconfident}`} minSpend={data.min_spend} unconfident={unconfident} />
    </>
  );

  const row = (r: FatigueRow) => {
    const rec = r.recent?.rates ?? {};
    const pri = r.prior?.rates ?? {};
    return (
      <tr key={r.ad_key}>
        <td className="name">
          <AdLink adKey={r.ad_key} brand={d.brand}>{r.entity_name || r.ad_key}</AdLink>
          <div style={{ marginTop: 3 }}>
            <span className="pill quiet">{r.optimization_goal ? words(r.optimization_goal) : "No goal recorded"}</span>
            {!r.confident && <> <span className="pill warn">low volume</span></>}
          </div>
        </td>
        <td><SymGrid r={r} /></td>
        <td><Sparkline values={spark[r.ad_key] ?? []} /></td>
        <td className="num">{money(r.spend_recent)}</td>
        <td className="num">{pri.cpa ? money(pri.cpa) : "--"} →{" "}
          <strong>{rec.cpa ? money(rec.cpa) : "--"}</strong></td>
        <td className="num">{num(r.prior?.daily_frequency_avg, 2)} →{" "}
          <strong>{num(r.recent?.daily_frequency_avg, 2)}</strong></td>
      </tr>
    );
  };

  return (
    <PageFrame nav="creative" title="Fatigue" brand={d.brand} settled={data.settled} toolbar={toolbar}>
      {!d.rows.length ? (
        <Empty>
          No ad ran in both the recent {win} days and the {win} before
          them, so there is nothing to compare. That is the usual answer on a new
          account.
        </Empty>
      ) : (
        <>
          <section className="answer" aria-label="Fatigue in one line">
            <p>Of <strong>{num(d.rows.length)}</strong> ads with enough spend to read,{" "}
              <strong>{num(sc.three)}</strong> show three or more symptoms and{" "}
              <strong>{num(sc.two)}</strong> show two.
              {topFired ? <> {most[0]} is the most common symptom, on{" "}
                <strong>{num(topFired)}</strong>.</> : null}</p>
            <div className="jumps"><a href="#ads">The ads</a></div>
          </section>

          {/* The pictures. */}
          <div className="grid" style={{ marginBottom: 20 }}>
            <section className="card span2" aria-label="How many symptoms each ad carries">
              <div className="card-head"><h2 className="card-t">How many symptoms each ad carries</h2></div>
              <div style={{ marginTop: 12, borderRadius: 3, overflow: "hidden" }}>
                <SplitBar segments={[["3 or more", sc.three, "--accent"], ["2", sc.two, "--accent-hi"],
                                     ["1", sc.one, "--focus"], ["none", sc.none, "--accent-line"]]} height={18} />
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 18, marginTop: 10, fontSize: 12 }}>
                {legend.map(([label, n, tone]) => (
                  <span key={label} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <span style={{ width: 10, height: 10, borderRadius: 2, background: `var(${tone})` }} />
                    {label} <strong>{num(n)}</strong></span>
                ))}
              </div>
              {d.suppressed_unconfident && !unconfident ? (
                // Counted, not listed: below the spend or impression floor the
                // score is arithmetically correct and means nothing, and a list
                // of them reads like a list of problems.
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14 }}>
                  <div style={{ flex: 1, height: 12, borderRadius: 3, border: "1px dashed var(--focus)" }} />
                  <span className="small muted" style={{ whiteSpace: "nowrap" }}><strong>{num(d.suppressed_unconfident)}</strong>{" "}
                    more below the floor ·{" "}
                    <Link to={`/creative?brand=${encodeURIComponent(d.brand)}&window=${win}&unconfident=1`}>show them</Link></span>
                </div>
              ) : null}
            </section>

            <section className="card" aria-label="Which symptom fires">
              <div className="card-head"><h2 className="card-t">Which symptom fires</h2></div>
              {symptoms.map(([label, n]) => (
                <div key={label} style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, fontSize: 12.5 }}>
                  <span style={{ flex: "0 0 140px", color: "var(--ink-2)" }}>{label}</span>
                  <Meter value={n} top={topFired} width={130} />
                  <strong style={{ color: "var(--accent)" }}>{num(n)}</strong>
                </div>
              ))}
            </section>
          </div>

          {/* The ads. */}
          <section className="stage" id="ads" aria-label="The ads">
            <StageHead title="The ads" source="ads.fatigue" />
            {!shown.length ? (
              <Empty>No readable ad carries a symptom in this window.</Empty>
            ) : (
              <>
                <div className="table-wrap" style={{ borderBottomLeftRadius: 0, borderBottomRightRadius: 0 }}>
                  <table>
                    <thead><tr>
                      <th scope="col">Ad</th>
                      <th scope="col">Symptoms
                        <div className="sym-head" aria-hidden="true">
                          <span>CTR</span><span>CPM</span><span>CPA</span><span>FRQ</span><span>RNK</span>
                        </div></th>
                      <th scope="col">Link CTR, {win}d</th>
                      <th scope="col" className="num">Spend, {win}d</th>
                      <th scope="col" className="num">CPA before → now</th>
                      <th scope="col" className="num">Daily freq</th>
                    </tr></thead>
                    <tbody>{shown.slice(0, 8).map(row)}</tbody>
                  </table>
                </div>
                <More style={{ background: "var(--surface)", border: "1px solid var(--line)", borderTop: 0,
                               borderRadius: "0 0 var(--radius) var(--radius)" }}
                      summary={<>
                        {shown.length > 8 && <><strong>Show the other {shown.length - 8}</strong> — </>}
                        {num(sc.none)} readable ad(s) carry no symptom and are not listed.</>}>
                  {shown.length > 8 && (
                    <div className="table-wrap" style={{ border: 0 }}>
                      <table><tbody>{shown.slice(8).map(row)}</tbody></table>
                    </div>
                  )}
                </More>
              </>
            )}
          </section>
        </>
      )}
    </PageFrame>
  );
}
