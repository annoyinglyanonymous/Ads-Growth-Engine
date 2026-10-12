/* The pieces of the triage report both shapes share, ported macro for macro
 * from templates/_triage.html: the formatting helpers, one issue (in full and
 * folded), the test plan, the outcome tag, the three tables, the shape-2
 * report, the legacy problem / fix list, and the notes and history on a card.
 *
 * NO ARITHMETIC HERE. Every figure is the pack's and is only formatted
 * (format.ts, byte-identical to charts.py). "better" / "worse" beside a change
 * is a sign comparison against a fixed list of which way each metric is good;
 * it divides nothing. Counts are .length.
 *
 * Every sentence of copy is the partial's, verbatim.
 */
import { Fragment } from "react";
import { f, money, num, pct, signedPct, words } from "../../lib/format";
import { isMapping, notNone, truthy } from "./reason";
import type {
  TriageCampaign, TriageConfidence, TriageFamily, TriageFamilyMetric, TriageFunnel,
  TriageHistoryItem, TriageIssue, TriageOutcome, TriageProblem, TriageRecommendation,
  TriageRung, TriageSpread, TriageTestPlan, TriageWhere,
} from "./types";

/* ----------------------------------------------------------- constants */

// Which way is good, per funnel metric. Frequency has no merit of its own:
// rising can mean fatigue or a smaller audience, so it gets no word. 017's
// own key names are listed too.
const MERIT = new Map<string, "lower" | "higher" | "neutral">([
  ["cpm", "lower"], ["cpc", "lower"], ["cpa", "lower"],
  ["link_ctr", "higher"], ["cvr", "higher"], ["frequency", "neutral"],
  ["cost_per_link_click", "lower"], ["conversion_rate", "higher"],
]);
const RESULT_WORD = new Map<string, string>([
  ["improved", "improved"], ["neutral", "no clear change"],
  ["worse", "worse"], ["too_early", "too early to read"],
]);
const METRIC_LABEL = new Map<string, string>([
  ["cpm", "CPM"], ["cpc", "Cost per link click"], ["cpa", "CPA"],
  ["link_ctr", "Link CTR"], ["cvr", "Conversion rate"], ["frequency", "Daily frequency"],
]);
const DRIVERS: [string, string][] = [
  ["auction_cost", "Auction cost (CPM)"],
  ["click_through", "Click-through (CTR)"],
  ["post_click", "Post-click (CVR)"],
];
const DOTS = new Map<string, number>([["low", 1], ["medium", 2], ["high", 3]]);
// A campaign family as the page says it, the metrics its typical range is
// quoted on (metric, label, fv unit), and where this week sits in it.
const FAMILY_WORDS = new Map<string, string>([
  ["franchise", "franchise"], ["m_and_a", "M&A / book roll"], ["b_roll", "B-roll"],
  ["hiring", "hiring"], ["other", "other"],
]);
const FAMILY_METRICS: [string, string, string][] = [
  ["cpa", "CPA", "cpa"], ["link_ctr", "link CTR", "link_ctr"],
  ["conversion_rate", "CVR", "cvr"], ["cpm", "CPM", "cpm"],
];
const POSITION_WORDS = new Map<string, string>([
  ["below", "below the range"], ["within", "within the range"], ["above", "above the range"],
]);

/* ------------------------------------------------- formatting helpers */

/** Jinja's `capitalize`: the first character up, the rest down. */
export function capitalize(s: unknown): string {
  const t = String(s);
  return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase();
}

/** Jinja's `replace('\\n', ' ')`: the literal two-character backslash-n, which
 *  is what the model's JSON carries, becomes a space. A real newline is left
 *  alone, as the filter left it. */
export function unescapeNewlines(s: unknown): string {
  return String(s).split("\\n").join(" ");
}

/** A funnel figure in its own unit. */
export function fv(metric: unknown, v: unknown): string {
  const m = String(metric);
  if (m === "cpm" || m === "cpc" || m === "cpa") return money(v);
  if (m === "link_ctr" || m === "cvr") return pct(v, 2);
  return num(v, 2);
}

/** "better" or "worse" from the sign of a change and which way the metric is
 *  good. A comparison with zero, not a calculation. The same word serves as a
 *  chip's class. */
export function meritWord(metric: unknown, change: unknown): "better" | "worse" | "" {
  const m = MERIT.get(String(metric)) ?? "neutral";
  const x = f(change);
  if (x === null || m === "neutral") return "";
  if ((x < 0 && m === "lower") || (x > 0 && m === "higher")) return "better";
  if ((x > 0 && m === "lower") || (x < 0 && m === "higher")) return "worse";
  return "";
}

function Direction({ metric, change }: { metric: unknown; change: unknown }) {
  const w = meritWord(metric, change);
  return w ? <span className={`dir ${w}`}>{w}</span> : null;
}

/** One CPA chip on the folded row: the window, which way, how far. The arrow
 *  carries the sign, so the figure is printed without one. */
export function CpaChip({ label, v }: { label: string; v: unknown }) {
  if (!notNone(v)) return null;
  const x = f(v) ?? 0; // Jinja's `| float` reads a bad value as 0.0
  const w = meritWord("cpa", x);
  return (
    <span className={w ? `cc ${w}` : "cc"}
          title={`CPA, ${label} against the ${label} before: ${signedPct(x)}`}>
      <span className="cl">{label}</span>{x > 0 ? "↑" : x < 0 ? "↓" : "→"}{pct(Math.abs(x), 0)}
    </span>
  );
}

/** What became of an act or test issue, from the outcomes ledger (D3): read
 *  off the account, never written by the review. */
export function OutcomeTag({ o }: { o: TriageOutcome }) {
  const when: string[] = [];
  if (o.implemented_on) {
    when.push("implemented " + o.implemented_on
      + (o.match ? (o.match === "person" ? " (set by a person)" : ` (${o.match})`) : ""));
  }
  if (o.status === "measuring") {
    when.push("measuring" + (notNone(o.settled_days)
      ? `, ${o.settled_days} settled day${o.settled_days === 1 ? "" : "s"}` : ""));
  } else if (o.status === "measured") {
    when.push("measured: " + (RESULT_WORD.get(String(o.result)) ?? (o.result || "--")));
  } else if (o.status === "open") {
    when.push("not yet seen in the account");
  } else if (o.status === "not_detectable") {
    when.push("not visible in Meta’s structure data");
  } else if (o.status === "expired") {
    when.push("expired, not implemented");
  } else if (o.status === "implemented" && !o.implemented_on) {
    when.push("implemented");
  }
  return when.length ? <span className="otag">{when.join(" · ")}</span> : null;
}

/** The confidence dots, three of them, `n` lit. */
function Dots({ level }: { level: unknown }) {
  const n = DOTS.get(String(level)) ?? 0;
  return (
    <span className="dots" aria-hidden="true">
      {[0, 1, 2].map((i) => <i key={i} className={i < n ? "on" : ""} />)}
    </span>
  );
}

/** The confidence dots and word, on one line. */
export function ConfDots({ conf }: { conf: TriageConfidence }) {
  return <><Dots level={conf.level} />{capitalize(conf.level)} confidence</>;
}

/* ------------------------------------------------------------ test plan */

/** The control / challenger design. Round 2 says what the challenger IS: an
 *  ad already in the account, or a brief for one that has to be made. */
export function TestPlan({ tp }: { tp: TriageTestPlan }) {
  const br = isMapping(tp.brief) ? tp.brief : {};
  const from = tp.challenger_kind_adjusted_from;
  return (
    <div className="tplan">
      {tp.variable && <div className="tv">Test: {tp.variable}</div>}
      <div className="arms">
        <div className="arm"><b>Control</b>{tp.control || "--"}</div>
        {tp.challenger_kind === "existing_asset" ? (
          <div className="arm kind"><b>EXISTING ASSET TO TEST</b>
            <div className="cad">{tp.challenger_ad || "--"}</div>
            {tp.challenger && tp.challenger !== tp.challenger_ad && <div>{tp.challenger}</div>}
          </div>
        ) : tp.challenger_kind === "new_creative" ? (
          <div className="arm kind"><b>NEW CREATIVE NEEDED</b>
            {tp.challenger && <div>{tp.challenger}</div>}
            <dl>
              {br.format && <><dt>Format</dt><dd>{br.format}</dd></>}
              {br.angle_or_hook && <><dt>Angle or hook</dt><dd>{br.angle_or_hook}</dd></>}
              {br.opening_line && <><dt>Opening line</dt><dd>“{br.opening_line}”</dd></>}
              {br.built_from && <><dt>Built from</dt><dd>{br.built_from}</dd></>}
            </dl>
          </div>
        ) : (
          <div className="arm"><b>Challenger</b>{tp.challenger || "--"}</div>
        )}
      </div>
      <dl>
        {tp.setup && <><dt>Set it up</dt><dd>{tp.setup}</dd></>}
        {tp.tag && <><dt>Tag the new ad</dt>
          <dd>Add <code>utm_content={tp.tag}</code> to its URL parameters, so its results can be read back by that tag.</dd></>}
        {truthy(tp.hold_constant) && <><dt>Hold constant</dt><dd>{(tp.hold_constant as string[]).join(" · ")}</dd></>}
        {truthy(tp.evaluate_on) && <><dt>Evaluate on</dt><dd>{(tp.evaluate_on as string[]).join(" · ")}</dd></>}
        {tp.decide_when && <><dt>Decide when</dt><dd>{tp.decide_when}</dd></>}
      </dl>
      {from && (
        <div className="adj">
          The review proposed {from === "existing_asset" ? "an existing asset" : words(from).toLowerCase()}
          {from === "existing_asset" ? ", but no ad of that name is in this goal's pool" : ""}; it is shown
          as {tp.challenger_kind === "new_creative" ? "new creative needed" : words(tp.challenger_kind).toLowerCase()}.
        </div>
      )}
    </div>
  );
}

/* --------------------------------------- old files: problem / fix cards */

/** `problems` from files published after 2026-09-24; `changes` before. */
export function Legacy({ c, prev }: { c: TriageCampaign; prev: boolean }) {
  const items: unknown[] | null | undefined = "problems" in c
    ? (c.problems as unknown[] | null | undefined)
    : (Array.isArray(c.changes) ? c.changes : []).map(String);
  if (!truthy(items) || !Array.isArray(items)) return null;
  return (
    <div className="legacy">
      <ol>
        {items.map((p, i) => {
          const pm: TriageProblem | null = isMapping(p) ? p : null;
          const fix = pm ? pm.fix : p;
          return (
            <li key={i}>
              {pm && pm.problem && (
                <div className="problem"><span className="lbl">
                  {pm.status === "still_open" ? <span className="ptag still">still open since {pm.open_since}</span>
                    : pm.status === "new" && prev ? <span className="ptag new">new</span> : null}
                </span>{pm.problem}</div>
              )}
              {truthy(fix) && (
                <div className="fix"><span className="lbl">How to fix it</span>{unescapeNewlines(fix)}</div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/* -------------------------------------------------------- one issue */

/** How the checker moved the review's call, so the card says whose it was. */
function Adjustments({ p, rec, conf }: { p: TriageIssue; rec: TriageRecommendation; conf: TriageConfidence }) {
  return (
    <>
      {p.type_adjusted_from && (
        <div className="adj">The review proposed {String(p.type_adjusted_from).toUpperCase()}; the
          evidence supports {String(rec.type || "--").toUpperCase()}.</div>
      )}
      {p.priority_adjusted_from && (
        <div className="adj">The review ranked this {p.priority_adjusted_from}; the
          evidence puts it at {p.priority || "--"}.</div>
      )}
      {p.confidence_adjusted_from && (
        <div className="adj">The review gave this {p.confidence_adjusted_from}{" "}
          confidence; the evidence supports {conf.level || "--"}.</div>
      )}
      {truthy(p.test_plan_dropped) && (
        <div className="adj">The review's test plan was dropped
          {typeof p.test_plan_dropped === "string"
            ? `: ${p.test_plan_dropped}` : ": the evidence does not support a test here"}.</div>
      )}
    </>
  );
}

function StatusTag({ p, prev }: { p: { status?: string | null; open_since?: string | null }; prev: boolean }) {
  if (p.status === "still_open") return <span className="ptag still">still open since {p.open_since}</span>;
  if (p.status === "new" && prev) return <span className="ptag new">new</span>;
  return null;
}

/** One issue, in full (shape 2). */
export function Issue({ p, prev }: { p: TriageIssue; prev: boolean }) {
  const rec: TriageRecommendation = isMapping(p.recommendation) ? p.recommendation : {};
  const conf: TriageConfidence = isMapping(p.confidence) ? p.confidence : {};
  return (
    <div className="issue">
      <div className="ihead">
        <span className="plabel">{capitalize(p.priority || "unranked")}</span>
        {rec.type && <span className={`rtype ${rec.type}`}>{String(rec.type).toUpperCase()}</span>}
        <StatusTag p={p} prev={prev} />
      </div>
      <dl className="tdl">
        {p.observation && <><dt>Observation</dt><dd>{p.observation}</dd></>}
        {p.evidence && <><dt>Evidence</dt><dd>{p.evidence}</dd></>}
        {p.diagnosis && <><dt>Likely cause (hypothesis)</dt><dd>{p.diagnosis}</dd></>}
        {rec.action && <><dt className="rec">Recommendation</dt><dd>{rec.action}</dd></>}
        {rec.expected_outcome && <><dt>Expected outcome</dt><dd>{rec.expected_outcome}</dd></>}
        {conf.level && (
          <>
            <dt>Confidence</dt>
            <dd><Dots level={conf.level} />{capitalize(conf.level)}{conf.reason ? <> · {conf.reason}</> : null}</dd>
          </>
        )}
      </dl>
      {isMapping(p.test_plan) && <TestPlan tp={p.test_plan} />}
      <Adjustments p={p} rec={rec} conf={conf} />
    </div>
  );
}

/** One issue, folded (round 2). The folded line is what a reader scans:
 *  priority, what the evidence supports, how sure, the action, and what
 *  became of it. Opening it shows the reasoning and the test design. */
export function IssueV3({ p, prev }: { p: TriageIssue; prev: boolean }) {
  const rec: TriageRecommendation = isMapping(p.recommendation) ? p.recommendation : {};
  const conf: TriageConfidence = isMapping(p.confidence) ? p.confidence : {};
  const incomplete = Array.isArray(p.incomplete) ? p.incomplete : [];
  return (
    <details className="issue">
      <summary>
        <span className="plabel">{capitalize(p.priority || "unranked")}</span>
        {rec.type && <span className={`rtype ${rec.type}`}>{String(rec.type).toUpperCase()}</span>}
        {conf.level && <span className="iconf"><ConfDots conf={conf} /></span>}
        <span className="iact">{rec.action || p.observation || "--"}</span>
        {isMapping(p.outcome) && <OutcomeTag o={p.outcome} />}
        {truthy(p.incomplete) && <span className="itag">incomplete</span>}
        <StatusTag p={p} prev={prev} />
      </summary>
      <div className="ibody">
        {truthy(p.incomplete) && (
          <div className="adj" style={{ margin: "0 0 8px" }}>
            Missing: {incomplete.map((x) => String(x).replace(/_/g, " ")).join(", ")}; shown as monitor.
          </div>
        )}
        <dl className="tdl">
          {p.observation && <><dt>Observation</dt><dd>{p.observation}</dd></>}
          {p.evidence && <><dt>Evidence</dt><dd>{p.evidence}</dd></>}
          {p.diagnosis && <><dt>Likely cause (hypothesis)</dt><dd>{p.diagnosis}</dd></>}
          {rec.action && <><dt className="rec">Recommendation</dt><dd>{rec.action}</dd></>}
          {rec.expected_outcome && <><dt>Expected outcome</dt><dd>{rec.expected_outcome}</dd></>}
          {conf.reason && <><dt>Confidence reason</dt><dd>{conf.reason}</dd></>}
        </dl>
        {isMapping(p.test_plan) && <TestPlan tp={p.test_plan} />}
        <Adjustments p={p} rec={rec} conf={conf} />
      </div>
    </details>
  );
}

/* ----------------------------------------- the report's three tables
   Shape 2 without buckets prints them in its report; round 2 keeps them
   behind the "The numbers" fold. */

/** TYPICAL FOR THIS KIND OF CAMPAIGN. `c.family` (contracts-baselines E2):
 *  the typical range of campaigns of the same family and the same goal in
 *  this account, over the last 26 weeks, the current window left out. The
 *  quartiles and the position are the SQL's (ads.campaign_vs_family); this
 *  only formats them. No band, or no figure in it, prints nothing (null). */
export interface FamilyLine {
  f: TriageFamily;
  parts: string[];
  at: { label: string; unit: string; m: TriageFamilyMetric } | null;
}

export function familyLine(c: TriageCampaign): FamilyLine | null {
  const fam: TriageFamily = isMapping(c.family) ? c.family : {};
  const fm = isMapping(fam.metrics) ? fam.metrics : {};
  const parts: string[] = [];
  let at: FamilyLine["at"] = null;
  for (const [key, label, unit] of FAMILY_METRICS) {
    const raw = Object.hasOwn(fm, key) ? fm[key] : undefined;
    const m: TriageFamilyMetric = isMapping(raw) ? raw : {};
    if (notNone(m.p25) && notNone(m.p75)) {
      if (unit === "link_ctr" || unit === "cvr") parts.push(`${label} ${num(m.p25, 2)}–${pct(m.p75, 2)}`);
      else parts.push(`${label} ${money(m.p25)}–${money(m.p75)}`);
      // Where this week sits: CPA's position when it has one, else the
      // first metric that does.
      if (at === null && truthy(m.position)) at = { label, unit, m };
    }
  }
  return truthy(fam.band_level) && parts.length ? { f: fam, parts, at } : null;
}

export function FamilyNote({ line }: { line: FamilyLine }) {
  const { f: fam, parts, at } = line;
  const name = FAMILY_WORDS.get(String(fam.family)) ?? words(fam.family).toLowerCase();
  return (
    <p className="tnote tfam">Typical for {name}{fam.goal ? ` · ${words(fam.goal).toLowerCase()}` : ""}{" "}
      ({fam.band_level === "family_audience" && fam.audience ? `${fam.audience} audiences only` : "all audiences"}
      {notNone(fam.campaign_weeks) ? `, ${num(fam.campaign_weeks)} campaign-weeks` : ""}):{" "}
      {parts.join(" · ")}
      {at ? <>; this week’s {at.label}{notNone(at.m.cur) ? ` of ${fv(at.unit, at.m.cur)}` : ""}{" "}
        sits {POSITION_WORDS.get(String(at.m.position)) ?? at.m.position}
        {notNone(at.m.percentile) ? ` (percentile ${num(at.m.percentile, 0)})` : ""}</> : null}.</p>
  );
}

/** WHAT CHANGED. Every window the pack read, newest first. The unsettled
 *  days are shown, and marked, because Meta restates them. */
export function SecChanged({ c }: { c: TriageCampaign }) {
  const ladder: TriageRung[] = truthy(c.ladder) && Array.isArray(c.ladder) ? c.ladder : [];
  // Daily frequency is measured per ad only (017), so a campaign's ladder
  // never has it: show the column only when some rung does.
  const hasFreq = ladder.some((w) => truthy(w.daily_frequency));
  const fam = familyLine(c);
  return (
    <>
      {ladder.length > 0 && (
        <>
          <h4 className="tsec">What changed</h4>
          <div className="table-wrap"><table>
            <thead><tr><th>Window</th><th className="num">Spend</th><th className="num">Conv</th>
              <th className="num">CPA</th><th className="num">CPM</th><th className="num">Link CTR</th>
              <th className="num">CVR</th>{hasFreq && <th className="num">Daily freq</th>}</tr></thead>
            <tbody>
              {ladder.map((w, i) => (
                <tr key={i} className={truthy(w.provisional) ? "prov" : undefined}>
                  <td>{w.label || w.key}{truthy(w.provisional) && <span className="tag">provisional</span>}
                    {w.since && <span className="when">{w.since} – {w.until}</span>}</td>
                  <td className="num">{money(w.spend)}</td>
                  <td className="num">{num(w.conversions)}</td>
                  <td className="num">{money(w.cpa)}</td>
                  <td className="num">{money(w.cpm)}</td>
                  <td className="num">{pct(w.link_ctr, 2)}</td>
                  <td className="num">{pct(w.cvr, 2)}</td>
                  {hasFreq && <td className="num">{num(w.daily_frequency, 2)}</td>}
                </tr>
              ))}
            </tbody>
          </table></div>
          {ladder.some((w) => truthy(w.provisional)) && (
            <p className="tnote">Provisional: Meta can still restate the last three days.</p>
          )}
        </>
      )}
      {/* Typical for this kind of campaign: context under the campaign's own
          windows, never instead of them. */}
      {fam && (
        <>
          {ladder.length === 0 && <h4 className="tsec">What changed</h4>}
          <FamilyNote line={fam} />
        </>
      )}
    </>
  );
}

/** WHAT'S CAUSING IT -- in round 2, WHERE THE CHANGE CAME FROM, because a
 *  metric contributes to a CPA change and causes nothing. Last 7 settled days
 *  against the 7 before. */
export function SecFunnel({ c, v3 }: { c: TriageCampaign; v3: boolean }) {
  if (!isMapping(c.funnel)) return null;
  const fn: TriageFunnel = c.funnel;
  const cur = isMapping(fn.cur) ? fn.cur : null;
  const prior = isMapping(fn.prior) ? fn.prior : null;
  const metrics = truthy(fn.metrics) && Array.isArray(fn.metrics) ? fn.metrics : [];
  const shares = isMapping(fn.shares) ? fn.shares : null;
  const stb = fn.spend_to_baseline_cpa;
  const fat = fn.fatigue_consistent;
  const hasNote = truthy(fn.evidence) || notNone(stb) || notNone(fat);
  return (
    <>
      <h4 className="tsec">{v3 ? "Where the change came from" : "What's causing it"}
        {cur && prior && <span className="dates">{cur.since} – {cur.until} vs {prior.since} – {prior.until}</span>}</h4>
      {metrics.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Metric</th><th className="num">Prior</th><th className="num">Current</th>
            <th className="num">Change</th></tr></thead>
          <tbody>
            {metrics.map((m, i) => (
              <tr key={i}><td>{METRIC_LABEL.get(String(m.metric)) ?? m.metric}</td>
                <td className="num">{fv(m.metric, m.prior)}</td>
                <td className="num">{fv(m.metric, m.cur)}</td>
                <td className="num">{signedPct(m.pct_change)}<Direction metric={m.metric} change={m.pct_change} /></td></tr>
            ))}
          </tbody>
        </table></div>
      )}
      {shares && (
        <div className="chips"><span className="k">Share of the CPA change:</span>
          {DRIVERS.map(([key, label]) => (
            v3 && fn.primary_driver === key ? (
              <span key={key} className="chip-d primary">{label} — appears to be the largest contributor · {pct(shares[key], 0)}</span>
            ) : (
              <span key={key} className={fn.primary_driver === key ? "chip-d primary" : "chip-d"}>{label} {pct(shares[key], 0)}</span>
            )
          ))}
          {fn.primary_driver === "mixed" && <span className="k">no single driver</span>}
        </div>
      )}
      {hasNote && (
        <p className="tnote">
          {truthy(fn.evidence) && <>Evidence: <b>{fn.evidence}</b></>}
          {notNone(stb) && <>{truthy(fn.evidence) ? " · " : ""}this
            week's spend would have bought {num(stb, 1)} conversions
            at the prior 7 days' cost per result</>}
          {notNone(fat) && <>{truthy(fn.evidence) || notNone(stb) ? " · " : ""}
            {truthy(fat) ? "the fatigue pattern is consistent" : "no consistent fatigue pattern"}</>}
        </p>
      )}
    </>
  );
}

/** WHERE IT'S CONCENTRATED. */
export function SecWhere({ c }: { c: TriageCampaign }) {
  const wh: TriageWhere | null = isMapping(c.where) ? c.where : null;
  if (wh && (truthy(wh.ad_groups) || truthy(wh.ads))) {
    const groups = Array.isArray(wh.ad_groups) ? wh.ad_groups : [];
    const ads = Array.isArray(wh.ads) ? wh.ads : [];
    return (
      <>
        <h4 className="tsec">Where it's concentrated</h4>
        {groups.length > 0 && (
          <div className="table-wrap"><table>
            <thead><tr><th>Ad group</th><th>Goal</th><th className="num">Spend</th>
              <th className="num">Share</th><th className="num">CPA</th><th className="num">Rank</th></tr></thead>
            <tbody>
              {groups.map((g, i) => (
                <tr key={i}><td className="name">{g.name}</td><td>{words(g.goal)}</td>
                  <td className="num">{money(g.spend)}</td><td className="num">{pct(g.share_pct, 1)}</td>
                  <td className="num">{money(g.cpa)}</td>
                  <td className="num">{notNone(g.rank) ? <>{String(g.rank)} of {String(g.of)}</> : "--"}</td></tr>
              ))}
            </tbody>
          </table></div>
        )}
        {ads.length > 0 && (
          <div className="table-wrap" style={{ marginTop: 8 }}><table>
            <thead><tr><th>Ad</th><th>Role</th><th className="num">Spend</th>
              <th className="num">Share</th><th className="num">CPA</th></tr></thead>
            <tbody>
              {ads.map((a, i) => (
                <tr key={i}><td className="name">{a.name}</td><td>{a.role || "--"}</td>
                  <td className="num">{money(a.spend)}</td><td className="num">{pct(a.share_pct, 1)}</td>
                  <td className="num">{money(a.cpa)}</td></tr>
              ))}
            </tbody>
          </table></div>
        )}
      </>
    );
  }
  if (isMapping(c.spend_spread)) {
    const s: TriageSpread = c.spend_spread;
    return (
      <>
        <h4 className="tsec">Where it's concentrated</h4>
        <p className="tnote">{num(s.ads_with_spend)} ads spent; the top two took{" "}
          {pct(s.top_two_ads_share_pct, 1)} of the spend
          {truthy(s.ads_with_no_conversions) ? <>, and {pct(s.share_on_ads_with_no_conversions_pct, 1)}{" "}
            went to {num(s.ads_with_no_conversions)} ads with no conversions</> : null}.</p>
      </>
    );
  }
  return null;
}

/* ---------------------------------------- shape 2: the analyst report */

const RANKED: [string, string][] = [["primary", "Primary"], ["secondary", "Secondary"], ["monitor", "Monitor"]];

export function Report({ c, prev }: { c: TriageCampaign; prev: boolean }) {
  const v3 = truthy(c.bucket);
  const iss: TriageIssue[] = truthy(c.issues) && Array.isArray(c.issues) ? c.issues : [];
  const other = iss.filter((p) => !RANKED.some(([key]) => p.priority === key));
  const notYet = truthy(c.not_yet) && Array.isArray(c.not_yet) ? c.not_yet : [];
  return (
    <>
      <SecChanged c={c} />
      <SecFunnel c={c} v3={v3} />
      <SecWhere c={c} />

      {/* 4. ISSUES, most pressing first. */}
      {iss.length > 0 && (
        <>
          <h4 className="tsec">Issues</h4>
          {RANKED.map(([key, label]) => {
            const these = iss.filter((p) => p.priority === key);
            return these.length ? (
              <Fragment key={key}>
                <div className="prio">{label}</div>
                {these.map((p, i) => <Issue key={i} p={p} prev={prev} />)}
              </Fragment>
            ) : null;
          })}
          {other.length > 0 && (
            <>
              <div className="prio">Unranked</div>
              {other.map((p, i) => <Issue key={i} p={p} prev={prev} />)}
            </>
          )}
        </>
      )}

      {/* 5. WHAT NOT TO DO YET. */}
      {notYet.length > 0 && (
        <>
          <h4 className="tsec">What not to do yet</h4>
          <ul className="notyet">{notYet.map((x, i) => <li key={i}>{String(x)}</li>)}</ul>
        </>
      )}

      {iss.length > 0 && (
        <p className="tfoot">ACT / TEST / MONITOR is what the evidence supports, not a decision.</p>
      )}
    </>
  );
}

/* --------------------------------------------- notes and history on a card */

/** What the checker flagged on a card. Shared by both shapes of report. */
export function CardNotes({ c, v3 }: { c: TriageCampaign; v3: boolean }) {
  return (
    <>
      {/* Round 2 groups by bucket and keeps the rating internal, so the note
          about a moved rating would explain a label the card no longer shows. */}
      {truthy(c.rating_adjusted_from) && !v3 && (
        <div className="note">The review rated this {c.rating_adjusted_from};
          the numbers put it here{truthy(c.rating_reasons) ? <>:{" "}
          {(c.rating_reasons as string[]).join("; ")}</> : null}.</div>
      )}
      {truthy(c.causal_flags) && (
        <div className="note warn">Causal wording flagged: {(c.causal_flags as string[]).join(", ")}.
          A metric can appear to be the largest contributor to a change; the
          review should not say it caused it.</div>
      )}
      {truthy(c.unverified_figures) && (
        <div className="note warn">Not found in the data:{" "}
          {(c.unverified_figures as string[]).join(", ")}. Probably worked out by the
          review rather than read from the numbers; check before relying on it.</div>
      )}
    </>
  );
}

/** What became of the last review's issues. */
export function CardHistory({ c }: { c: TriageCampaign }) {
  const resolved: TriageHistoryItem[] = truthy(c.resolved) && Array.isArray(c.resolved) ? c.resolved : [];
  const dropped: TriageHistoryItem[] = truthy(c.dropped) && Array.isArray(c.dropped) ? c.dropped : [];
  return (
    <>
      {resolved.length > 0 && (
        <div className="done"><span className="lbl">Resolved since the last review</span>
          {resolved.map((x, i) => (
            <div key={i}>
              {x.by === "you" ? <span className="ptag fixed">fixed — you changed it</span>
                : x.by === "numbers" ? <span className="ptag moved">no longer showing in the numbers</span> : null}
              <b>{x.problem || x.observation}</b><br />
              <span className="small">Open since {x.open_since}.{x.change ? <> What changed: {x.change}.</> : null}{" "}
                {x.evidence}</span>
            </div>
          ))}
        </div>
      )}
      {dropped.length > 0 && (
        <div className="quiet"><span className="lbl">No longer mentioned — not confirmed fixed</span>
          {dropped.map((x, i) => (
            <div key={i}>{x.problem || x.observation} <span className="small">(raised {x.open_since})</span></div>
          ))}
        </div>
      )}
    </>
  );
}
