/* One report per campaign, from `published.triage`. Ported from
 * templates/_triage.html.
 *
 * Rendered by the suggestions page inside its "AI read" block, so this draws
 * only the groups: no heading, no pill of its own. Reads only `published`,
 * format.ts (the same formatters charts.py gives the prose), the WindowDots
 * and PairedMoves charts, and reasonLine / leadIssue (triage/reason.ts).
 *
 * No script and no button -- the page has neither. Everything that opens is a
 * <details>, and nothing starts open.
 *
 * THREE SHAPES OF ENTRY.
 * - Round 2 (contracts2 D4) carries `bucket`. Groups are Act / Test /
 *   Monitor / No change, in that order, campaigns inside each in the order
 *   given (creative.sort_triage: impact first). A group is NOT folded: each
 *   campaign is one line, so a group reads as a list -- the reason, the
 *   excess cost, the CPA move over 7 and 14 days, the spend -- and the tail
 *   of a long group folds behind "N more". Opening a campaign shows three
 *   panels: what to do (the issue the bucket came from, which need not be
 *   the primary one), CPA by window and what moved, and where the spend
 *   went. The full issues, the tables and what not to do yet are one more
 *   click away, each its own fold.
 * - Shape 2 without `bucket` carries `issues` (contracts C5) and groups by
 *   rating into folded groups, exactly as before.
 * - An older file carries `problems` (each with its fix) or plain `changes`,
 *   and renders through Legacy, unchanged.
 * Every section is guarded on its own data, so a missing piece drops out
 * alone. Neither shape invents the other.
 *
 * NO ARITHMETIC HERE. Every figure is the pack's (creative.triage_from
 * attaches them), never the model's, and this only formats them. A bar is
 * drawn against a fixed 100% or against the largest value shown; no share,
 * remainder or difference is worked out. "better" / "worse" beside a change
 * is a sign comparison against a fixed list of which way each metric is
 * good; it divides nothing. The only subtractions are the partial's own
 * counts of what is folded away ("N more", "N more issues").
 *
 * RED / AMBER / GREEN, and this is the only component allowed them. The rest
 * of the dashboard is blue and white; the user granted this panel an
 * exception by name on 2026-09-23. The rating groups of a shape-2 file use
 * it (styles/triage.css). Round 2 groups by bucket, and its groups and chips
 * are navy: the bucket is a word, and a column of amber dots on 23 of 25
 * rows said nothing the word did not.
 *
 * Without buckets: red first, then yellow, then green, the order a reader
 * works through it; within a group, the pack's order, which is spend.
 */
import "../styles/triage.css";
import { f, money, num, pct, words } from "../lib/format";
import { Meter, PairedMoves, WindowDots } from "./charts";
import { isMapping, leadIssue, notNone, reasonLine, truthy } from "./triage/reason";
import {
  CardHistory, CardNotes, ConfDots, CpaChip, IssueV3, Legacy, OutcomeTag, Report,
  SecChanged, SecFunnel, SecWhere, familyLine, unescapeNewlines,
} from "./triage/parts";
import type {
  TriageCampaign, TriageConfidence, TriageImpact, TriageIssue, TriagePublished,
  TriageRecommendation, TriageRung, TriageSpread, TriageTestPlan, TriageWhere,
} from "./triage/types";

export type { TriageCampaign, TriageIssue, TriagePublished } from "./triage/types";
export { leadIssue, reasonLine } from "./triage/reason";

const BUCKETS = ["act", "test", "monitor", "no_change"];
const RATING_LABEL = new Map<string, string>([
  ["red", "Critical"], ["yellow", "Needs changes"], ["green", "No change"],
]);
// The ladder's rungs, oldest on the left, for the dot chart.
const WINDOWS: [string, string][] = [
  ["before", "Before"], ["last_28", "28d"], ["prior_14", "Prior 14"],
  ["last_14", "Last 14"], ["prior_7", "Prior 7"], ["last_7", "Last 7"],
  ["unsettled", "Unsettled"],
];

/** `c.movement['7v7']` / `['14v14']` as a mapping, or {}. */
function pair(c: TriageCampaign, key: string): Record<string, unknown> {
  const mv = isMapping(c.movement) ? c.movement : {};
  const m = mv[key];
  return isMapping(m) ? m : {};
}

/* ---------------------------------------------- round 2: one campaign, opened
   Three panels -- what to do, how CPA moved, where the money went -- and the
   detail one click further. Each panel draws only from its own fields and
   drops out when they are missing. */
function Panels({ c }: { c: TriageCampaign }) {
  const p: TriageIssue = leadIssue(c);
  const rec: TriageRecommendation = isMapping(p.recommendation) ? p.recommendation : {};
  const conf: TriageConfidence = isMapping(p.confidence) ? p.confidence : {};
  const iss = truthy(c.issues) && Array.isArray(c.issues) ? c.issues : [];
  const m7 = pair(c, "7v7");
  const m14 = pair(c, "14v14");

  const ladder: TriageRung[] = truthy(c.ladder) && Array.isArray(c.ladder) ? c.ladder : [];
  const pts: [string, unknown, boolean][] = [];
  for (const [key, lab] of WINDOWS) {
    for (const w of ladder) if (w.key === key) pts.push([lab, w.cpa, Boolean(w.provisional)]);
  }
  // window_dots draws nothing from fewer than two values, and paired_moves
  // nothing without a figure: the section is guarded on the same rules.
  const dots = pts.filter(([, v]) => f(v) !== null).length >= 2;
  const moveRows: [string, unknown, unknown][] = [
    ["Link CTR", m7.link_ctr, m14.link_ctr],
    ["Cost per click", m7.cost_per_link_click, m14.cost_per_link_click],
    ["Conversion rate", m7.conversion_rate, m14.conversion_rate],
  ];
  const moves = moveRows.some(([, a, b]) => f(a) !== null || f(b) !== null);

  const wh: TriageWhere = isMapping(c.where) ? c.where : {};
  const ads = (Array.isArray(wh.ads) ? wh.ads : []).slice(0, 2);
  const s: TriageSpread = isMapping(c.spend_spread) ? c.spend_spread : {};
  const noConv = truthy(s) && notNone(s.share_on_ads_with_no_conversions_pct);
  const tp: TriageTestPlan = isMapping(p.test_plan) ? p.test_plan : {};

  return (
    <div className="tpanels">
      <section className="tp do" aria-label="What to do">
        <h4 className="tp-h">What to do</h4>
        {rec.action ? (
          <>
            {rec.type && <span className={`rtype ${rec.type}`}>{String(rec.type).toUpperCase()}</span>}
            <p className="tp-act">{rec.action}</p>
            {rec.expected_outcome && <p className="tp-out">{rec.expected_outcome}</p>}
            {/* The one thing a test needs copied into Ads Manager, so the next
                import can tie the new ad to this recommendation. Shown only when
                the issue leading the card is a test; one click selects it whole. */}
            {rec.type === "test" && tp.tag && (
              <p className="tp-tag"><span>Tag the new ad</span>
                <code title="Add to the new ad's URL parameters">utm_content={tp.tag}</code></p>
            )}
            <p className="tp-conf">
              {conf.level && <span><ConfDots conf={conf} /></span>}
              {iss.length > 1 && <span>{iss.length - 1} more issue{iss.length === 2 ? "" : "s"}</span>}
              {isMapping(p.outcome) && <OutcomeTag o={p.outcome} />}
            </p>
          </>
        ) : (
          <p className="tp-act">No issue raised: nothing in this campaign's numbers calls for a change.</p>
        )}
      </section>
      {(dots || moves) && (
        <section className="tp" aria-label="How CPA moved">
          {dots && <><h4 className="tp-h">CPA by window</h4><WindowDots points={pts} current="Last 7" width={340} /></>}
          {moves && (
            <>
              <h4 className="tp-h">What moved{" "}
                <span className="key"><i style={{ background: "var(--accent)" }} />7d{" "}
                  <i style={{ background: "var(--focus)" }} />14d</span></h4>
              <PairedMoves rows={moveRows} width={104} />
            </>
          )}
        </section>
      )}
      {(ads.length > 0 || noConv) && (
        <section className="tp" aria-label="Where the spend went">
          <h4 className="tp-h">Where the spend went</h4>
          {ads.map((a, i) => (
            <div key={i} className="sw-row"><span className="n" title={a.name ?? undefined}>{a.name}</span>
              <Meter value={a.share_pct} top={100} width={70} /><b>{pct(a.share_pct, 0)}</b>
              <span className="c">{money(a.cpa)}</span></div>
          ))}
          {noConv && (
            <div className="sw-nc"><b>{pct(s.share_on_ads_with_no_conversions_pct, 0)}</b>of spend on{" "}
              {num(s.ads_with_no_conversions)} of {num(s.ads_with_spend)} ads with no conversions{" "}
              <Meter value={s.share_on_ads_with_no_conversions_pct} top={100} width={180} /></div>
          )}
        </section>
      )}
    </div>
  );
}

/** Round 2: one campaign as one line of its group's list, opened to the panels. */
function CampV3({ c, topSpend, prev }: { c: TriageCampaign; topSpend: number | null; prev: boolean }) {
  const m7 = pair(c, "7v7");
  const m14 = pair(c, "14v14");
  const imp: TriageImpact = isMapping(c.impact) ? c.impact : {};
  const iss = truthy(c.issues) && Array.isArray(c.issues) ? c.issues : [];
  const fam = familyLine(c);
  const hasNumbers = truthy(c.ladder) || isMapping(c.funnel) || isMapping(c.where)
    || isMapping(c.spend_spread) || fam !== null;
  const goals = Array.isArray(c.optimization_goals) ? c.optimization_goals : [];
  const notYet = truthy(c.not_yet) && Array.isArray(c.not_yet) ? c.not_yet : [];
  const xc = imp.excess_cost;
  return (
    <details className="tcamp">
      <summary>
        <span className="tc-name" title={c.campaign ?? undefined}><span className="ttl">{c.campaign}</span>
          <span className="tc-sub">
            {goals.length ? goals.map((g) => words(g)).join(", ") : "No single goal"}
            {" · "}{money(c.spend)}
            {c.status && c.status !== "ACTIVE" ? ` · ${String(c.status).toLowerCase()}` : ""}</span></span>
        <span className="head">{reasonLine(c)}</span>
        <span className="tc-figs">
          {notNone(xc) && (f(xc) ?? 0) > 0 && (
            <span className="xs" title="Beyond what this week's conversions cost at last week's rate">+{money(xc)}</span>
          )}
          <CpaChip label="7d" v={m7.cpa} /><CpaChip label="14d" v={m14.cpa} />
        </span>
        <span className="tc-bar"><Meter value={c.spend} top={topSpend} width={80} /></span>
        <svg className="tc-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
      </summary>
      <div className="tbody">
        <CardNotes c={c} v3={true} />
        <Panels c={c} />
        {(iss.length > 0 || hasNumbers || notYet.length > 0 || truthy(c.resolved) || truthy(c.dropped)) && (
          <div className="tfolds">
            {iss.length > 0 && (
              <details className="tfold"><summary>The full issue{iss.length > 1 ? "s" : ""} <span className="c">{iss.length}</span></summary>
                <div className="tfold-b">{iss.map((p, i) => <IssueV3 key={i} p={p} prev={prev} />)}</div>
              </details>
            )}
            {hasNumbers && (
              <details className="tfold"><summary>The numbers</summary>
                <div className="tfold-b"><SecChanged c={c} /><SecFunnel c={c} v3={true} /><SecWhere c={c} /></div>
              </details>
            )}
            {notYet.length > 0 && (
              <details className="tfold"><summary>What not to do yet <span className="c">{notYet.length}</span></summary>
                <div className="tfold-b"><ul className="notyet">{notYet.map((x, i) => <li key={i}>{String(x)}</li>)}</ul></div>
              </details>
            )}
            {(truthy(c.resolved) || truthy(c.dropped)) && (
              <details className="tfold"><summary>Since the last review</summary>
                <div className="tfold-b"><CardHistory c={c} /></div>
              </details>
            )}
          </div>
        )}
      </div>
    </details>
  );
}

/** Shape 2 and older: one campaign, folded inside its rating group. */
function Camp({ c, prev }: { c: TriageCampaign; prev: boolean }) {
  const head = "headline" in c && truthy(c.headline) ? c.headline : c.why;
  return (
    <details className="tcamp">
      <summary>
        <span className={`tpill ${c.rating || "none"}`}>{RATING_LABEL.get(String(c.rating)) ?? "Not rated"}</span>
        <span className="ttl">{c.campaign}</span>
        {truthy(head) && <span className="head">{unescapeNewlines(head)}</span>}
        <span className="figs">
          {money(c.spend)} spent · {num(c.conversions)} conv
          {notNone(c.cpa) ? ` · ${money(c.cpa)} CPA` : ""}
          {truthy(c.optimization_goals) ? ` · ${(c.optimization_goals as string[]).join(", ")}` : ""}
          {c.status && c.status !== "ACTIVE" ? ` · ${String(c.status).toLowerCase()}` : ""}
        </span>
      </summary>
      <div className="tbody">
        <CardNotes c={c} v3={false} />
        {"issues" in c ? <Report c={c} prev={prev} /> : <Legacy c={c} prev={prev} />}
        <CardHistory c={c} />
      </div>
    </details>
  );
}

/* ------------------------------------------------------- since the last review
   Counts of what the checker in creative.triage_from decided, not of what the
   model claimed. Only files published after 2026-09-24 carry these fields, so
   an older file shows nothing here. A report counts its issues; an older file
   its problems. Every number is a .length or a tally of items. */
function Since({ published, t }: { published: TriagePublished; t: TriageCampaign[] }) {
  if (!truthy(published.previous_file)) return null;
  let resolvedN = 0, fixedYou = 0, droppedN = 0, still = 0, fresh = 0;
  for (const c of t) {
    if (Array.isArray(c.resolved)) {
      resolvedN += c.resolved.length;
      for (const x of c.resolved) if (isMapping(x) && x.by === "you") fixedYou += 1;
    }
    if (Array.isArray(c.dropped)) droppedN += c.dropped.length;
    const items = "issues" in c ? c.issues : c.problems;
    for (const p of Array.isArray(items) ? items : []) {
      if (!isMapping(p)) continue;
      if (p.status === "still_open") still += 1;
      else if (p.status === "new") fresh += 1;
    }
  }
  return (
    <div className="since">
      <span>Since the last review ({String(published.previous_file).slice(0, 10)}):</span>
      <span className="tpill green">{resolvedN} resolved{resolvedN ? ` (${fixedYou} by a change you made)` : ""}</span>
      <span className="tpill yellow">{still} still open</span>
      <span className="tpill none">{fresh} new</span>
      {droppedN ? <span className="tpill none">{droppedN} no longer mentioned</span> : null}
      {published.data_changed === false && (
        <span>· <b>no new data since then</b>, so nothing could be marked resolved</span>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- the groups */

export function Triage({ published }: { published: TriagePublished }) {
  const t: TriageCampaign[] = Array.isArray(published.triage) ? published.triage : [];
  const bucketed = t.filter((c) => truthy(c.bucket));
  const prev = truthy(published.previous_file);

  if (bucketed.length) {
    // (label, rows, bucket, how many rows show before the rest fold)
    const groups: [string, TriageCampaign[], string, number][] = [
      ["Act", t.filter((c) => c.bucket === "act"), "act", 99],
      ["Test", t.filter((c) => c.bucket === "test"), "test", 5],
      ["Monitor", t.filter((c) => c.bucket === "monitor"), "monitor", 4],
      ["No change", t.filter((c) => c.bucket === "no_change"), "no_change", 3],
      ["Not sorted", t.filter((c) => !BUCKETS.includes(String(c.bucket))), "none", 99],
    ];
    // The spend bar on each row is scaled against the largest spend in the file.
    const spends = t.map((c) => c.spend).filter(truthy).map(f).filter((v): v is number => v !== null);
    const topSpend = spends.length ? Math.max(...spends) : null;
    return (
      <>
        <Since published={published} t={t} />
        {groups.filter(([, rows]) => rows.length).map(([label, rows, bk, limit]) => (
          <section key={bk} className="tlist" id={bk} data-bucket={bk} aria-label={label}>
            <header><span className={`bk ${bk}`}>{label}</span>
              <span className="tl-n">{rows.length} campaign{rows.length === 1 ? "" : "s"}</span></header>
            {rows.slice(0, limit).map((c, i) => <CampV3 key={i} c={c} topSpend={topSpend} prev={prev} />)}
            {rows.length > limit && (
              <details className="tl-more">
                <summary><b>{rows.length - limit} more</b>{rows.slice(limit).map((c) => c.campaign ?? "").join(" · ")}</summary>
                {rows.slice(limit).map((c, i) => <CampV3 key={i} c={c} topSpend={topSpend} prev={prev} />)}
              </details>
            )}
          </section>
        ))}
      </>
    );
  }

  const groups: [string, string, string, TriageCampaign[]][] = [
    ["red", "Critical", "Look at these first", t.filter((c) => c.rating === "red")],
    ["yellow", "Needs changes", "Some changes worth making", t.filter((c) => c.rating === "yellow")],
    ["green", "No change", "Leave these alone", t.filter((c) => c.rating === "green")],
    ["none", "Not rated", "The reply did not rate these", t.filter((c) => !truthy(c.rating))],
  ];
  return (
    <>
      <Since published={published} t={t} />
      {groups.filter(([, , , rows]) => rows.length).map(([key, label, sub, rows]) => {
        let gres = 0;
        for (const c of rows) if (Array.isArray(c.resolved)) gres += c.resolved.length;
        return (
          <details key={key} className={`tgroup ${key}`}>
            <summary>
              <span className="count">{rows.length}</span>
              <span className={`tpill ${key}`}>{label}</span>
              <span className="sub">{sub}</span>
              {gres ? <span className="tpill green">{gres} resolved</span> : null}
              <span className="names">{rows.map((c) => c.campaign ?? "").join(" · ")}</span>
            </summary>
            <div className="body">
              {rows.map((c, i) => <Camp key={i} c={c} prev={prev} />)}
            </div>
          </details>
        );
      })}

      <p className="small muted" style={{ marginTop: 4, maxWidth: "80ch" }}>
        Click a group to open its campaigns, and a campaign to open its report. Each
        campaign is judged only against ad groups with the same optimization goal.
        Red means look here first; it decides nothing. A campaign whose ad groups
        span two goals shows no CPA, because that number would mix two different
        events.
      </p>
    </>
  );
}
