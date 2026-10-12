/* THE BRIEF. Every other page answers one question; this one is the whole
 * account, read in the order a person has to read it. Ported from
 * templates/brief.html.
 *
 * WHAT THIS PAGE LEARNED FROM BEING READ
 *
 * It used to render nine numbered sections at full fidelity whatever they
 * contained -- roughly three thousand of its five and a half thousand pixels
 * were empty boxes, and the six sentences the rules had already written were
 * scattered one per section where nobody read them in order.
 *
 * So: FINDINGS FIRST, EVIDENCE UNDER THEM, AND A SECTION EARNS ITS HEIGHT.
 *
 *   * The readings are promoted into one ranked band at the top, grouped by
 *     the section they belong to, each with a miniature carrying the shape
 *     of the claim and a link to the table that proves it.
 *   * A section with no rows collapses to one line that says WHY it is
 *     empty. "It loaded and the answer is none" and "it could not be read"
 *     are different claims, and an empty box says neither.
 *   * A long table shows five rows and then says what is in the tail.
 *
 * Nothing was deleted. Every section is still here and every number still
 * comes out of the same function.
 *
 * THE ONE RULE THIS PAGE EXISTS TO ENFORCE
 *
 * A fact and a reading are never allowed to look alike. Facts live in .kpi
 * and .stat tiles, tables and charts, and a table names the SQL that produced
 * it beside its heading. Readings live in the findings band, labelled as
 * readings of the figures below and never inside a table cell. Proposals get
 * a third label -- "Proposal -- not filed" -- and the command that would file
 * them. They are told apart by PLACE and SHAPE, not by colour: exactly one
 * finding row is ever tinted, and only when it is about the data rather than
 * about the advertising.
 *
 * WHAT IS NOT PRINTED HERE, AND WHERE IT WENT
 *
 * Rule names, /facts pointers and provisional flags are not on the page. They
 * are still on every reading and in briefs/<date>-<brand>.json; the audit
 * trail is for the person CHECKING the brief, not the person reading it.
 *
 * Nothing here derives a figure: counts are .length, the only subtraction is
 * `100 - share` for the coverage bar, exactly where the template did it.
 */
import { createContext, Fragment, useContext, type ReactNode } from "react";
import { PageFrame } from "../components/Layout";
import { AskButton, useAnalysis } from "../components/AskButton";
import { BarChart, EffectBars, SplitBar } from "../components/charts";
import { Icon } from "../components/Icon";
import { AdLink, Dash, Empty, More, stamp, usePage } from "../components/ui";
import type { PageBase } from "../lib/api";
import { f, money, num, pct, words } from "../lib/format";

/* ------------------------------------------------------------------ types
   The brief document as intel/brief.py builds it and jsonable_encoder sends
   it: dates are ISO strings, Decimals are numbers. Optional everywhere,
   because a degraded section arrives as None and a published brief may be
   older than the shape. */

type N = number | null | undefined;
type Rates = Record<string, N>;

interface Goal { goal?: string | null; spend?: N }

interface Reading {
  rule: string;
  says: string;
  section?: string | null;
  subject?: { kind?: string | null; key?: string | null; name?: string | null } | null;
  cites?: unknown;
  provisional?: boolean;
  author?: string;
}

interface Account {
  platform_account_id: string;
  label?: string | null;
  currency?: string | null;
  active?: boolean | null;
  insights_through?: string | null;
  last_structure_ok?: string | null;
}

interface Unmatched {
  asset_name?: string | null; name?: string | null; title?: string | null;
  headline?: string | null; asset_id?: string | null; key?: string | null;
  expected_utm_content?: string | null; utm_content?: string | null; expected?: string | null;
  observed_utm_content?: string | null; observed?: string | null; found?: string | null;
  reason?: string | null; why?: string | null; note?: string | null;
}

interface Trust {
  healthy?: boolean | null;
  problems?: string[] | null;
  settled_through?: string | null;
  credential?: { ok?: boolean | null; confirmed_as?: string | null; note?: string | null } | null;
  coverage?: { first_day?: string | null; last_day?: string | null; ad_days?: N; ads?: N; spend?: N } | null;
  conversion_definitions?: number | string | { name?: string | null }[] | null;
  accounts?: Account[] | null;
  match?: {
    approved_assets?: N; with_tracked_url?: N; matched?: N; match_rate?: N;
    unmatched?: Unmatched[] | null;
  } | null;
}

interface Spend {
  spend?: N; conversions?: N; impressions?: N; clicks?: N; link_clicks?: N; currencies?: N;
  rates?: Rates | null; mixed_currency?: boolean | null;
  delta?: Rates | null; pct?: Rates | null;
}

interface StructureChange {
  changed_at?: string | null; entity_name?: string | null; field: string;
  old_value?: string | null; new_value?: string | null;
}

interface Mover {
  ad_key: string; entity_name?: string | null; reason?: string | null;
  rate_effect?: N; mix_effect?: N; total_effect?: N;
}

interface Movement {
  cpa_change?: N; rate_effect?: N; mix_effect?: N; unattributable_effect?: N;
  attributable_share?: N; attributable_ads?: N; unattributable_ads?: N;
  rows?: Mover[] | null;
  structure_changes?: { available?: boolean | null; why?: string | null; rows?: StructureChange[] | null } | null;
}

interface FatigueRow {
  ad_key: string; entity_name?: string | null; optimization_goal?: string | null;
  days_observed?: N; spend_recent?: N; impressions_recent?: N; score?: N;
  link_ctr_decline?: boolean | null; cpm_rise?: boolean | null; cpa_rise?: boolean | null;
  frequency_rise?: boolean | null; ranking_drop?: boolean | null;
  recent?: { rates?: Rates | null } | null;
  prior?: { rates?: Rates | null } | null;
}

interface Creative { rows?: FatigueRow[] | null; suppressed_unconfident?: N; frequency_note?: string | null }

interface CopyRow {
  value?: string | null; ads_run?: N; spend?: N; conversions?: N; rates?: Rates | null;
  optimization_goals?: Goal[] | null; comparable_on_cost?: boolean | null;
  spend_sufficient?: boolean | null; rank_within_goal?: N;
}

type CopyDim = "format" | "family" | "hook" | "offer" | "audience";
type Copy = Partial<Record<CopyDim, CopyRow[] | null>> & { from?: string };

interface AngleRow {
  angle_slug?: string | null; angle_name?: string | null; name?: string | null;
  family?: string | null; definition?: string | null;
  spend?: N; conversions?: N; rates?: Rates | null;
  spend_share_pct?: N; conversion_share_pct?: N;
  comparable_on_cost?: boolean | null; spend_sufficient?: boolean | null;
  rank_within_goal?: N; tagged_ads?: N; inherited_ads?: N; state?: string | null;
}

interface PriorExperiment {
  name?: string | null; conclusion?: string | null;
  angle_slug?: string | null; angle_name?: string | null;
}

interface Angles {
  rows?: AngleRow[] | null; never_run?: AngleRow[] | null;
  under_spent?: AngleRow[] | null; prior_experiments?: PriorExperiment[] | null;
}

interface Untagged { total_spend?: N; tagged_spend?: N; untagged_spend?: N; untagged_ads?: N }
interface Labels { ads?: N; with_a_hook?: N; with_an_offer?: N }

interface ExperimentRow {
  name?: string | null; state?: string | null; hypothesis?: string | null;
  primary_metric?: string | null; conclusion?: string | null; readable?: boolean | null;
}

interface Experiments { rows?: ExperimentRow[] | null; awaiting_launch?: N }

interface Candidate { observed?: string | null; handle?: string | null; reviews?: N; last_seen?: string | null }

interface Waiting {
  angles_unsigned?: AngleRow[] | null;
  experiments_unlaunched?: ExperimentRow[] | null;
  experiments_unconcluded?: ExperimentRow[] | null;
  unmapped_handles?: Candidate[] | null;
  untagged_spend?: N;
  where?: string | null;
}

interface Proposal {
  angle_slug?: string | null; angle_name?: string | null; family?: string | null;
  definition?: string | null; gap?: string | null; already_tested?: boolean;
  prior_experiments?: PriorExperiment[] | null; how_to_file?: string | null; filed?: boolean;
}

interface Degraded { section: string; guard: string; load_bearing?: boolean }

interface Facts {
  trust?: Trust | null; spend?: Spend | null; movement?: Movement | null;
  creative?: Creative | null; copy?: Copy | null; angles?: Angles | null;
  untagged?: Untagged | null; creative_labels?: Labels | null; experiments?: Experiments | null;
}

interface BriefDoc {
  brand: string; since: string; until: string; days: number;
  settled_through?: string | null; unsettled_days?: N; caveat?: string | null;
  product?: string | null; generated_at?: string | null; provisional?: boolean;
  facts?: Facts | null; waiting_on_you?: Waiting | null; proposals?: Proposal[] | null;
  gaps?: unknown; degraded?: Degraded[] | null; readings?: Reading[] | null;
  summary?: { text?: string | null; written_at?: string | null } | null;
  file?: string;
}

export interface BriefData extends PageBase {
  d: BriefDoc;
  days: number;
  product: string | null;
  live: boolean;
  prior_since: string;
}

/* ---------------------------------------------------------------- helpers */

/** Jinja's `is not none`. */
const some = (v: unknown): boolean => v !== null && v !== undefined;

/** Jinja's `capitalize`: first letter up, the rest down. */
const capitalize = (s: string): string => s.slice(0, 1).toUpperCase() + s.slice(1).toLowerCase();

/** `r.angle_name or r.name or r` -- the template's fallback chain for a row
 *  that may be an older shape. The last resort prints the row, as Jinja did. */
const nameOf = (r: { angle_name?: string | null; name?: string | null }): string =>
  r.angle_name || r.name || JSON.stringify(r);

/** '+' before a positive figure; money() already puts the minus on a negative. */
const signedMoney = (v: unknown): string => `${(f(v) ?? 0) > 0 ? "+" : ""}${money(v)}`;

/* The one place a multi-part bar is allowed a second hue, and it is not a
   hue: five steps down the SAME navy, so an optimization-goal split is read
   as one quantity cut up rather than as five competing series. */
const TONES = ["--accent", "--accent-hi", "--line-2", "--accent-line", "--surface-3"];

/* Two facts every macro needs: whether money may be printed at all, and
   whether the prior window is comparably covered. */
const Guards = createContext({ oneCurrency: true, priorPartial: false });

/** Money, or a refusal. When a brand's accounts do not all report in one
 *  currency a total adds different units, and printing it is worse than
 *  printing nothing because it looks like an answer. */
function Cash({ v }: { v: unknown }) {
  const { oneCurrency } = useContext(Guards);
  if (oneCurrency) return <>{money(v)}</>;
  return <span className="muted" title="accounts report in more than one currency">withheld</span>;
}

/** A window-over-window delta, or the reason there isn't one. Withheld the
 *  same way money is withheld under mixed currency: a figure that looks like
 *  an answer and is not is worse than no figure. Never silently dropped, or
 *  the reader cannot tell it from a delta of zero. */
function Delta({ value, merit, guardable = false, children }: {
  value: unknown; merit: number; guardable?: boolean; children: ReactNode;
}) {
  const { priorPartial } = useContext(Guards);
  if (guardable && priorPartial) {
    return <div className="delta flat"><span className="pill warn">prior window incomplete</span></div>;
  }
  const v = f(value);
  if (v !== null) {
    const n = v || 0;
    const dir = n > 0 ? "up" : n < 0 ? "down" : "flat";
    const tone = merit && (n > 0) === (merit > 0) && n !== 0 ? " good" : merit && n !== 0 ? " bad" : "";
    return <div className={`delta ${dir}${tone}`}>{children}</div>;
  }
  return <div className="delta flat">{children}</div>;
}

/** ONE FINDING. A rank, the sentences the rules wrote, a miniature carrying
 *  their shape, and a link to the table underneath. The miniature is an
 *  illustration of the claim, never an extra claim of its own. */
function Finding({ n, anchor, rows, vis, flag = false }: {
  n: number; anchor: string; rows: Reading[]; vis: ReactNode; flag?: boolean;
}) {
  return (
    <div className={flag ? "finding flag" : "finding"}>
      <div className="rank" aria-hidden="true">{flag ? <Icon name="warn" /> : n}</div>
      <div className="says">
        <p>
          {rows.map((r, i) => {
            const subj = r.subject?.name;
            return (
              <Fragment key={i}>
                {subj ? <><strong>{subj}</strong>{" "}</> : null}
                {r.says}{i < rows.length - 1 ? " " : ""}
              </Fragment>
            );
          })}
        </p>
      </div>
      <div className="vis">{vis}</div>
      <a className="go" href={`#${anchor}`}>Evidence<Icon name="arrow-right" /></a>
    </div>
  );
}

/** The five named fatigue symptoms as five cells in a fixed order. Position
 *  says WHICH symptom and the aria-label spells them out, so colour is the
 *  second channel rather than the only one. The count of filled cells is the
 *  score, which is why the score no longer prints beside it. */
function Symptoms({ r }: { r: FatigueRow }) {
  const flags: [unknown, string][] = [
    [r.link_ctr_decline, "click-through down"],
    [r.cpm_rise, "impression cost up"],
    [r.cpa_rise, "acquisition cost up"],
    [r.frequency_rise, "daily frequency up"],
    [r.ranking_drop, "ranking down"],
  ];
  const names = flags.filter(([on]) => on).map(([, name]) => name);
  return (
    <div className="sym" role="img" aria-label={names.length ? names.join(", ") : "no symptoms"}>
      {flags.map(([on, name], i) => <span key={i} className={on ? "on" : undefined} title={name} />)}
    </div>
  );
}

/** A section with nothing in it, as one line carrying the reason. */
function Quiet({ anchor, title, code, why }: { anchor: string; title: string; code: string; why: ReactNode }) {
  return (
    <div className="quiet-row" id={anchor}>
      <span className="t">{title}</span>
      <span className={`pill ${code === "could not be read" ? "warn" : "quiet"}`}>{code}</span>
      <span className="w">{why}</span>
    </div>
  );
}

/* ============================================================== THE AI READ
   The brief writes its own prose now. scripts/brief.py asks a model to read
   the pack after every import and publishes the answer beside the facts, so
   the reading is on the page before anybody opens it -- the page renders it,
   it does not fetch it.

   It goes FIRST and it is labelled. A summary is interpretation, the findings
   under it are the rules' cited claims, and the tiles are facts; the "AI
   read" pill and the stamp keep the three visibly apart. The stamp names the
   model's window and its unsettled days, because the model wrote against
   exactly that and a reader comparing the prose to a tile needs to know it
   is the same week.

   `summary` is null when the model was unavailable or the call failed, and
   on a live brief. Then the slot says so and offers the old button, which is
   the one case a button still earns its place: there is nothing published to
   read. Drawn here rather than through AnalysisPanel because the section is
   on the page before anyone presses anything. */
function BriefSummary({ d, days, live, product }: {
  d: BriefDoc; days: number; live: boolean; product: string | null;
}) {
  const sm = d.summary;
  const url = `/brief/summary.json?brand=${d.brand}&days=${days}` +
    (product ? `&product=${encodeURIComponent(product)}` : "");
  const a = useAnalysis(url);
  const published = Boolean(sm && sm.text);
  const state = !a.asked ? `no summary was published for this ${live ? "window" : "week"}`
    : a.phase === "busy" ? "this takes about a minute" : a.phase === "failed" ? "failed" : "";
  return (
    <section className="ai-read" id="brief-summary" aria-label="AI read">
      <div className="ai-read-head">
        <span className="pill stop">AI read</span>
        {published ? (
          <span className="state">written {String(sm!.written_at).slice(0, 10)} · from the{" "}
            {days} days to {d.until} · {num(d.unsettled_days)} unsettled day(s)</span>
        ) : (
          <>
            <span className="state" data-analysis-state>{state}</span>
            <span style={{ marginLeft: "auto", display: "inline-flex" }}>
              <AskButton label="Explain this brief" phase={a.phase} onClick={a.ask} className="btn ghost" />
            </span>
          </>
        )}
      </div>
      {published ? (
        String(sm!.text).split("\n\n").map((para, i) =>
          para.trim() ? <p key={i}>{para.trim()}</p> : null)
      ) : (
        <>
          <div data-analysis-body>
            {!a.asked ? <p className="muted">The findings below stand on their own.</p>
              : /* Plain paragraphs, as text. chat.SYSTEM tells the session to
                   write sentences with no markdown, so anything arriving with
                   asterisks in it is a prompt that drifted. */
              String(a.text).split(/\n{2,}/).map((para, i) => {
                const t = para.trim();
                return t ? (
                  <p key={i} className={a.phase === "failed" ? "n err" : undefined}
                     style={{ margin: "0 0 10px", maxWidth: "80ch" }}>{t}</p>
                ) : null;
              })}
          </div>
          <p className="small muted" data-analysis-foot hidden={a.phase !== "done"}>Written by a Claude Code
            session from the numbers below. It reports; it decides nothing.</p>
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------- findings
   The readings, grouped by the section they belong to and ordered by what
   the group costs. Trust is first because a number you cannot trust is worse
   than no number; the rest follow the order the evidence appears in.

   A reading says which section it belongs to -- readings.py's `_reading`
   carries it -- so this page obeys the rule author rather than guessing.
   Subject KIND is only the fallback, and a reading this page cannot place
   lands in the guard at the very bottom rather than vanishing. */
const GROUPS: [string, string, string, string[]][] = [
  ["trust", "Trust", "trust",
    ["trust", "credential", "match", "coverage", "account", "pull", "conversion_definition"]],
  ["movement", "What moved it", "moved",
    ["movement", "bridge", "structure", "structure_change"]],
  ["creative", "What's tiring", "tiring",
    ["creative", "fatigue", "ad"]],
  ["angles", "Angles and tagging", "angles",
    ["angles", "angle", "untagged", "tag", "facet", "never_run", "under_spent", "gaps", "gap", "degraded"]],
  ["spend", "The window", "window",
    ["spend", "window", "brand"]],
  ["copy", "What the copy is doing", "copy",
    ["copy", "format", "hook", "offer", "family"]],
  ["experiments", "Tests in flight", "experiments",
    ["experiments", "experiment"]],
  ["waiting", "Waiting on you", "waiting",
    ["waiting", "action", "owner"]],
];

/** The symptom-count buckets the creative miniature and the tiring tail quote. */
interface Score { hi: number; two: number; one: number; none: number }

/** The miniature for each group. Composition where the finding is a split,
 *  signed bars where it is a cancellation, two tracks where it is coverage.
 *  A group with no picture worth drawing gets none rather than a decorative
 *  one. */
function Miniature({ k, facts, sc }: { k: string; facts: Facts; sc: Score }) {
  const t = facts.trust ?? {};
  const cov = t.coverage ?? {};
  const mv = facts.movement ?? {};
  const cr = facts.creative ?? {};
  const s = facts.spend ?? {};
  const ut = facts.untagged ?? {};
  if (k === "trust") {
    const structureOk = (t.accounts ?? []).filter((a) => a.last_structure_ok);
    return (
      <>
        <div className="track-row"><span className="k">Insights</span>
          <span className="bar" />
          <span className="v">{(cov.last_day || t.settled_through) || "--"}</span></div>
        <div className="track-row"><span className="k">Structure</span>
          <span className={structureOk.length ? "bar" : "bar off"} />
          <span className="v">{structureOk.length ? "ok" : "never"}</span></div>
      </>
    );
  }
  if (k === "movement") {
    // merit is -1 on every row: these are all CPA, where up is worse. The
    // sign still decides which side of zero the bar sits on.
    return <EffectBars rows={[["Rate", mv.rate_effect, -1], ["Mix", mv.mix_effect, -1], ["Net", mv.cpa_change, -1]]} kind="cpa" />;
  }
  if (k === "creative") {
    return (
      <>
        <SplitBar segments={[["3 or more symptoms", sc.hi, "--accent"], ["2 symptoms", sc.two, "--accent-hi"],
          ["1 symptom", sc.one, "--line-2"], ["no symptom", sc.none, "--surface-3"]]} />
        <div className="n muted">{num((cr.rows ?? []).length)} readable ·{" "}
          {num(cr.suppressed_unconfident)} below the floor</div>
      </>
    );
  }
  if (k === "angles") {
    // The two coverages side by side, drawn: the copy is labelled, the angle
    // is not. One bar for both would say "untagged" and be wrong about half.
    const cl = facts.creative_labels ?? {};
    return (
      <>
        <div className="track-row"><span className="k">Labels</span>
          <span className={cl.with_an_offer ? "bar" : "bar off"} />
          <span className="v">{cl.ads ? num(cl.with_an_offer) : "--"} ads</span></div>
        <div className="track-row"><span className="k">Angle</span>
          <span className={ut.tagged_spend ? "bar" : "bar off"} />
          <span className="v">{ut.tagged_spend ? <Cash v={ut.tagged_spend} /> : "none"}</span></div>
      </>
    );
  }
  if (k === "spend") {
    return (
      <>
        <SplitBar segments={[["Conversions", s.conversions, "--accent"],
          ["Link clicks that did not convert", s.link_clicks, "--surface-3"]]} />
        <div className="n muted">{num(s.conversions)} of{" "}
          {num(s.link_clicks)} link clicks</div>
      </>
    );
  }
  return null;
}

/* ---------------------------------------------------------------- rows */

function MvRow({ r, brand }: { r: Mover; brand: string }) {
  return (
    <tr>
      <td className="name">
        <AdLink adKey={r.ad_key} brand={brand}>{r.entity_name || r.ad_key}</AdLink>
        {r.reason && r.reason !== "attributable" && <div className="why">{r.reason.replace(/_/g, " ")}</div>}
      </td>
      <td className="num">{some(r.rate_effect) ? <Cash v={r.rate_effect} /> : "--"}</td>
      <td className="num">{some(r.mix_effect) ? <Cash v={r.mix_effect} /> : "--"}</td>
      <td className="num"><strong><Cash v={r.total_effect} /></strong></td>
    </tr>
  );
}

function CrRow({ r, brand }: { r: FatigueRow; brand: string }) {
  const rec = r.recent?.rates ?? {};
  const pri = r.prior?.rates ?? {};
  return (
    <tr>
      <td className="name">
        <AdLink adKey={r.ad_key} brand={brand}>{r.entity_name || r.ad_key}</AdLink>
        <div className="why">{num(r.impressions_recent)} impressions over{" "}
          {num(r.days_observed)} day(s)</div>
      </td>
      <td><Symptoms r={r} /></td>
      <td className="num"><Cash v={r.spend_recent} /></td>
      <td className="num"><Cash v={pri.cpa} /></td>
      <td className="num"><Cash v={rec.cpa} /></td>
      <td className="num">{pct(pri.link_ctr)}</td>
      <td className="num">{pct(rec.link_ctr)}</td>
      {/* CPL is not comparable across optimization goals, so the goal travels
          with the row. */}
      <td>{r.optimization_goal ? <span className="pill quiet">{words(r.optimization_goal)}</span> : <Dash />}</td>
    </tr>
  );
}

/** What was tried before on this angle, if anything. */
function PriorFor({ r, prior }: { r: AngleRow; prior: PriorExperiment[] }) {
  let hits = prior.filter((e) => (e.angle_slug ?? null) === (r.angle_slug ?? null));
  if (!hits.length) hits = prior.filter((e) => (e.angle_name ?? null) === (r.angle_name ?? null));
  if (!hits.length) return <div className="why">no prior experiment on record</div>;
  return (
    <>
      {hits.map((e, i) => (
        <div key={i} className="why">tested before: {e.name}
          {e.conclusion ? <> — {e.conclusion}</> : <> — no conclusion recorded</>}</div>
      ))}
    </>
  );
}

/** A table's folded tail, styled as the template styled it. */
const TAIL = {
  background: "var(--surface)", border: "1px solid var(--line)", borderTop: 0,
  borderRadius: "0 0 var(--radius) var(--radius)",
} as const;
const CUT = { borderBottomLeftRadius: 0, borderBottomRightRadius: 0 } as const;

/* ================================================================= page */

export function BriefPage() {
  const page = usePage<BriefData>("/brief", "brief", "The brief");
  if (page.gate) return page.gate;
  const data = page.data;
  const { d, days, product, live } = data;

  // ---------------------------------------------------------------- setup
  const facts: Facts = d.facts ?? {};
  const t = facts.trust ?? {};
  const s = facts.spend ?? {};
  const mv = facts.movement ?? {};
  const cr = facts.creative ?? {};
  // `copy` is the one key in this brief whose name collides with a dict
  // method in the template; here it is just a key.
  const cp: Copy = facts.copy ?? {};
  const an = facts.angles ?? {};
  const ut = facts.untagged ?? {};
  const ex = facts.experiments ?? {};
  const w = d.waiting_on_you ?? {};

  const cred = t.credential ?? {};
  const cov = t.coverage ?? {};
  const match = t.match ?? {};
  const rates = s.rates ?? {};
  const dl = s.delta ?? {};
  const pc = s.pct ?? {};
  const arows = an.rows ?? [];
  const oneCurrency = !s.mixed_currency;

  // Which sections failed to load, by name. A section that is MISSING and a
  // section that is EMPTY are different findings and the quiet strip below
  // says which -- `degraded` is the only thing that can tell them apart.
  const failed: Record<string, string> = {};
  for (const g of d.degraded ?? []) failed[g.section] = g.guard;

  // THE PRIOR WINDOW MAY NOT BE COMPARABLY COVERED, and a delta measured
  // against a window the importer had barely reached is a fact about the
  // import rather than about the advertising. ui.py's note beside
  // `prior_since` explains why this is a proxy and names the missing verb.
  // Rates survive the mismatch and totals do not, so only spend and
  // conversions are withheld.
  const priorPartial = Boolean(data.prior_since && cov.first_day && cov.first_day > data.prior_since);

  // ------------------------------------------------------------- findings
  const readings = d.readings ?? [];
  const placed = new Set<Reading>();
  const findings: [string, string, string, Reading[]][] = [];
  for (const [key, label, anchor, sections] of GROUPS) {
    const rows = readings.filter((r) => sections.includes(r.section || r.subject?.kind || "unattached"));
    rows.forEach((r) => placed.add(r));
    if (rows.length) findings.push([key, label, anchor, rows]);
  }

  const crRows = cr.rows ?? [];
  const sc: Score = { hi: 0, two: 0, one: 0, none: 0 };
  for (const r of crRows) {
    const score = r.score || 0;
    if (score >= 3) sc.hi += 1;
    else if (score === 2) sc.two += 1;
    else if (score === 1) sc.one += 1;
    else sc.none += 1;
  }

  // ------------------------------------------------------------ what moved
  // Rate, mix, and the ads that converted in only one window -- the third
  // row is what makes the bars add up to the net. Without it a reader sums
  // rate and mix, gets a different number, and trusts neither.
  const eff: [string, unknown, number][] = [["Rate effect", mv.rate_effect, -1], ["Mix effect", mv.mix_effect, -1]];
  if (some(mv.unattributable_effect)) eff.push(["One window", mv.unattributable_effect, -1]);
  eff.push(["Net", mv.cpa_change, -1]);
  const share = f(mv.attributable_share);
  const ch = mv.structure_changes ?? {};
  const mvRows = mv.rows ?? [];
  const undefinedSplit = mvRows.filter((r) => r.reason !== "attributable");

  // ------------------------------------------------------------------ copy
  // Format first, and deliberately: it needs no tag and no signature, so this
  // section says something real even when the bank is inert.
  const copyDims: [CopyDim, string, CopyRow[]][] = [];
  for (const [dim, label] of [["format", "Format"], ["hook", "Hook"], ["offer", "Offer"],
    ["family", "Family"], ["audience", "Audience"]] as [CopyDim, string][]) {
    const real = (cp[dim] ?? []).filter((r) => some(r.value));
    if (real.length) copyDims.push([dim, label, real]);
  }
  // What the money was buying, once, at the top. `family` carries every ad
  // in the window, so its goal split is the window's.
  const allGoals = ((cp.family ?? [{}])[0] ?? {}).optimization_goals ?? [];
  const goalSegs = (goals: Goal[]): [string, unknown, string][] =>
    goals.map((g, i) => [g.goal ? words(g.goal) : "no goal recorded", g.spend, TONES[i % TONES.length]]);
  const absent: string[] = [];
  for (const [dim, label] of [["hook", "hook"], ["offer", "offer"], ["audience", "audience"],
    ["family", "family"]] as [CopyDim, string][]) {
    if (!(cp[dim] ?? []).filter((r) => some(r.value)).length) absent.push(label);
  }

  // ---------------------------------------------------------------- angles
  const bars: [string, unknown][] = arows.filter((r) => r.comparable_on_cost)
    .map((r) => [r.angle_name || "--", (r.rates ?? {}).cpa]);
  const pri = an.prior_experiments ?? [];
  const neverRun = an.never_run ?? [];
  const underSpent = an.under_spent ?? [];
  const proposals = d.proposals ?? [];
  const exRows = ex.rows ?? [];
  const unsigned = w.angles_unsigned ?? [];
  const unlaunched = w.experiments_unlaunched ?? [];
  const unconcluded = w.experiments_unconcluded ?? [];
  const unmapped = w.unmapped_handles ?? [];
  const waitingN = unsigned.length + unlaunched.length + unconcluded.length + unmapped.length;

  // NOTHING TO REPORT, AND WHY. A collapsed section is never shown as a zero.
  const silent: [string, string, string, ReactNode][] = [];
  if (!arows.length) {
    silent.push(["angles", "Which angles earned their place",
      failed["angles"] ? "could not be read" : "nothing to show",
      failed["angles"] || ("The angle bank has no members, so no ad can carry an angle and none " +
        "carried spend. The copy itself is labelled: hook, offer and audience " +
        "are read below under What the copy is doing.")]);
  }
  if (!(neverRun.length || underSpent.length || proposals.length)) {
    silent.push(["gaps", "What we've never said",
      failed["angles.coverage"] ? "could not be read" : "nothing to show",
      failed["angles.coverage"] || ("A set difference against a vocabulary with no members in it. This " +
        "becomes a finding the day the first angle is signed.")]);
  }
  if (!exRows.length) {
    silent.push(["experiments", "Tests in flight",
      failed["experiments"] ? "could not be read" : "nothing to show",
      failed["experiments"] || (
        <>No experiment has been proposed. This one was readable and the answer
          is genuinely none; experiments are filed with{" "}
          <code>python -m intel record --kind experiment --json {"<path>"}</code>{" "}
          and launched by a person in Ads Manager.</>)]);
  }
  if (!waitingN) {
    silent.push(["waiting", "Waiting on you", "clear",
      "No angle awaits a signature, no experiment awaits a launch or a " +
      "conclusion, and every handle a reviewer wrote maps to an angle."]);
  }

  // A reading none of the groups above claimed. Empty in the normal case;
  // exists so a rule shipped with a new section name shows up somewhere
  // visible instead of vanishing.
  const orphans = readings.filter((r) => !placed.has(r));

  const cd = t.conversion_definitions;
  const problems = t.problems ?? [];
  const unhealthy = t.healthy === false;

  // THE CHIPS SAY WHICH DOCUMENT THIS IS. A published brief is a dated week;
  // a live one is whatever window was asked for.
  const toolbar = live
    ? <span className="chip">live · {days} days to {d.until}</span>
    : (
      <>
        <span className="chip">{days === 7 ? "week" : `${days} days`} to {d.until}</span>
        <span className="chip">published {String(d.generated_at).slice(0, 10)}</span>
      </>
    );

  const expTone: Record<string, string> = {
    concluded: "ok", running: "warn", proposed: "quiet", awaiting_conclusion: "warn",
  };

  return (
    <Guards.Provider value={{ oneCurrency, priorPartial }}>
    <PageFrame nav="brief" title="The brief" brand={d.brand} settled={data.settled} toolbar={toolbar}>

      <BriefSummary d={d} days={days} live={live} product={product} />

      {/* ===================================================== THE SCOREBOARD
          Six facts, one row. */}
      <div className="stats" id="window">
        <div className="stat">
          <div className="k">Spend</div>
          <div className="v"><Cash v={s.spend} /></div>
          {/* Spend has no direction of merit, so this one gets an arrow and
              the muted colour -- a weather report does not colour rainfall red. */}
          <Delta value={dl.spend} merit={0} guardable>
            <Cash v={dl.spend} />{some(pc.spend) ? ` (${pct(pc.spend)})` : ""} vs prior window
          </Delta>
        </div>

        <div className="stat">
          <div className="k">CPA</div>
          {/* s.rates.cpa, read out of the jsonb ads.rate() returned. Never a
              flat key, and never a division done on this page. */}
          <div className="v"><Cash v={rates.cpa} /></div>
          <Delta value={dl.cpa} merit={-1}>
            {some(dl.cpa)
              ? <><Cash v={dl.cpa} />{some(pc.cpa) ? ` (${pct(pc.cpa)})` : ""} vs prior window</>
              : <>no prior CPA — nothing converted in one of the two windows</>}
          </Delta>
        </div>

        <div className="stat">
          <div className="k">Conversions</div>
          <div className="v">{num(s.conversions)}</div>
          <Delta value={dl.conversions} merit={1} guardable>
            {num(dl.conversions)}{some(pc.conversions) ? ` (${pct(pc.conversions)})` : ""} vs prior window
          </Delta>
        </div>

        <div className="stat">
          <div className="k">Link CTR</div>
          <div className="v">{pct(rates.link_ctr)}</div>
          {/* Percentage POINTS. pct() would render 0.0999 as "0.10%", which
              reads as a tenth of a percent relative rather than a tenth of a point. */}
          <Delta value={dl.link_ctr} merit={1}>
            {num(dl.link_ctr, 2)} pp vs prior · link clicks, not all clicks
          </Delta>
        </div>

        <div className="stat">
          <div className="k">Settled through</div>
          <div className="v" style={{ fontSize: 19, marginTop: 4 }}>{t.settled_through || "--"}</div>
          <div className="sub">{num(d.unsettled_days)} unsettled day(s) in this window
            {" "}— Meta restates for about three</div>
        </div>

        <div className="stat">
          {/* "No ANGLE", not "no tag". ads.untagged_spend counts an ad as
              tagged only when a facet resolves an angle, and the angle bank is
              empty on this brand -- so this reads 100% forever while
              scripts/tag.py has labelled every ad's hook and offer.
              creative_labels is the other coverage, and the line under the
              figure names it so the two are never confused. The share would
              read better and this page may not compute one. */}
          <div className="k">Spend with no angle</div>
          <div className="v"><Cash v={ut.untagged_spend} /></div>
          <div className="sub">of <Cash v={ut.total_spend} />
            {(facts.creative_labels ?? {}).ads ? (
              <> · the copy is labelled: offer on{" "}
                {num(facts.creative_labels?.with_an_offer)} of {num(facts.creative_labels?.ads)} ads, hook on{" "}
                {num(facts.creative_labels?.with_a_hook)}</>
            ) : null}</div>
        </div>
      </div>

      {/* ================================================ WHAT THIS WINDOW SAYS */}
      {findings.length > 0 && (
        <section className="stage" id="findings" aria-label="What this window says">
          <div className="stage-head">
            <h2>What this window says</h2>
            <span className="state">readings of the figures below, not decisions about them</span>
          </div>
          <div className="card" style={{ padding: 0 }}>
            {findings.map(([key, , anchor, rows], i) => (
              <Finding key={key} n={i + 1} anchor={anchor} rows={rows}
                       vis={<Miniature k={key} facts={facts} sc={sc} />}
                       flag={key === "trust" && unhealthy} />
            ))}
          </div>
        </section>
      )}

      {/* ==================================================== WHAT MOVED IT
          Structure changes come FIRST inside this section, because a budget
          edit is a cause and a rate/mix split is arithmetic. Read the other
          way round, every account edit gets re-explained as creative decay. */}
      <section className="stage" id="moved" aria-label="What moved it">
        <div className="stage-head">
          <h2>What moved it</h2>
          <span className="state">Source: <code>ads.cpa_bridge_totals</code> + <code>intel why</code></span>
        </div>

        <div className="grid" style={{ marginBottom: 16 }}>
          <div className="card span2">
            <div className="card-head"><h2 className="card-t">The two things that can move a CPA</h2></div>
            <EffectBars rows={eff} kind="cpa" width={560} rowH={30} labelW={76} valueW={90} />
          </div>

          <div className="card">
            {/* The part the split cannot assign, in dollars; the share only
                when it can honestly be called coverage (when the one-window
                ads pull the other way the function's share exceeds 100%). */}
            <div className="k">Outside the split</div>
            <div className="big">{signedMoney(mv.unattributable_effect)}</div>
            {share !== null && share >= 0 && share <= 100 && (
              <>
                <div style={{ margin: "9px 0 0", borderRadius: 3, overflow: "hidden" }}>
                  <SplitBar segments={[["Covered", share, "--accent"], ["Not covered", 100 - share, "--line"]]} height={8} />
                </div>
                <div className="n" style={{ marginTop: 6 }}>the split covers {pct(share)} of the move</div>
              </>
            )}
            <div className="n" style={{ marginTop: 8 }}>
              {num(mv.attributable_ads)} ad(s) converted in <em>both</em> windows,
              which is all the split can be computed for.{" "}
              {num(mv.unattributable_ads)} did not.
            </div>
          </div>
        </div>

        {/* `why` is printed verbatim and an empty table is never drawn in its
            place. "No changes recorded" and "we cannot see changes" look
            identical as an empty list, and only one of them is a fact. */}
        <div className="stage-head">
          <h2>What changed in the account</h2>
        </div>
        {!ch.available ? (
          <Empty>
            {ch.why || "No reason given for why account changes are unavailable, which is itself a gap."}
            <div className="small muted" style={{ marginTop: 8 }}>This is not the same as
              “nothing changed”. Nothing changed is a claim; this is an
              absence of evidence.</div>
          </Empty>
        ) : !(ch.rows ?? []).length ? (
          <Empty>No budget, status or goal edit was recorded in this
            window, and the change log was readable, so this one is a finding rather
            than a blind spot.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr>
                <th scope="col">When</th><th scope="col">What</th>
                <th scope="col">Field</th><th scope="col">From</th><th scope="col">To</th>
              </tr></thead>
              <tbody>
                {(ch.rows ?? []).map((c, i) => (
                  <tr key={i}>
                    <td className="small">{stamp(c.changed_at)}</td>
                    <td className="name">{c.entity_name || "--"}</td>
                    <td><span className="pill quiet">{c.field}</span></td>
                    <td className="mono">{some(c.old_value) ? c.old_value : "--"}</td>
                    <td className="mono">{some(c.new_value) ? c.new_value : "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="stage-head" style={{ marginTop: 16 }}>
          <h2>The ads that moved it most</h2>
        </div>
        {!mvRows.length ? (
          <Empty>Nothing spent in either window, so there is no movement
            to decompose.</Empty>
        ) : (
          <>
            <div className="table-wrap" style={CUT}>
              <table>
                <thead><tr>
                  <th scope="col">Ad</th>
                  <th scope="col" className="num">Rate effect</th>
                  <th scope="col" className="num">Mix effect</th>
                  <th scope="col" className="num">Total effect</th>
                </tr></thead>
                <tbody>{mvRows.slice(0, 5).map((r) => <MvRow key={r.ad_key} r={r} brand={d.brand} />)}</tbody>
              </table>
            </div>
            {mvRows.length > 5 && (
              /* The shape of the tail is itself a fact. A cut that says only
                 "show all" asks the reader to scroll to find out whether it matters. */
              <More style={TAIL}
                    summary={<><strong>Show the other {mvRows.length - 5}</strong> —{" "}
                      {undefinedSplit.length} of them carry no split at all, having
                      converted in only one of the two windows.</>}>
                <div className="table-wrap" style={{ border: 0 }}>
                  <table><tbody>{mvRows.slice(5).map((r) => <MvRow key={r.ad_key} r={r} brand={d.brand} />)}</tbody></table>
                </div>
              </More>
            )}
          </>
        )}
      </section>

      {/* ==================================================== WHAT'S TIRING */}
      {crRows.length > 0 && (
        <section className="stage" id="tiring" aria-label="What is tiring">
          <div className="stage-head">
            <h2>What's tiring</h2>
            <span className="state">Source: <code>ads.fatigue</code>, the last seven days against the seven before</span>
          </div>

          <div className="table-wrap" style={CUT}>
            <table>
              <thead><tr>
                <th scope="col">Ad</th>
                <th scope="col">Symptoms
                  <div className="sym-head" aria-hidden="true">
                    <span>CTR</span><span>CPM</span><span>CPA</span><span>FRQ</span><span>RNK</span>
                  </div></th>
                <th scope="col" className="num">Spend, 7d</th>
                <th scope="col" className="num">CPA before</th>
                <th scope="col" className="num">CPA now</th>
                <th scope="col" className="num">Link CTR before</th>
                <th scope="col" className="num">Link CTR now</th>
                <th scope="col">Goal it was buying</th>
              </tr></thead>
              <tbody>{crRows.slice(0, 5).map((r) => <CrRow key={r.ad_key} r={r} brand={d.brand} />)}</tbody>
            </table>
          </div>
          {(crRows.length > 5 || (f(cr.suppressed_unconfident) ?? 0) > 0) && (
            <More style={TAIL}
                  summary={<>
                    {crRows.length > 5 && (
                      <><strong>Show the other {crRows.length - 5}</strong> —{" "}
                        {sc.one} carry a single symptom and {sc.none} carry none.</>
                    )}
                    {(f(cr.suppressed_unconfident) ?? 0) > 0 && (
                      <>{crRows.length > 5 ? " " : ""}A further {num(cr.suppressed_unconfident)} fall below the spend
                        or impression floor, where a symptom count would be arithmetic rather
                        than a finding, so they are counted here and not listed.</>
                    )}
                  </>}>
              {crRows.length > 5 && (
                <div className="table-wrap" style={{ border: 0 }}>
                  <table><tbody>{crRows.slice(5).map((r) => <CrRow key={r.ad_key} r={r} brand={d.brand} />)}</tbody></table>
                </div>
              )}
            </More>
          )}
        </section>
      )}

      {/* ========================================= WHAT THE COPY IS DOING */}
      {copyDims.length > 0 && (
        <section className="stage" id="copy" aria-label="What the copy is doing">
          <div className="stage-head">
            <h2>What the copy is doing</h2>
            <span className="state">Source: <code>ads.facet_performance</code></span>
          </div>

          {/* Every cost figure in this section is confounded by what the money
              was buying, and one bar says so faster than a paragraph repeated
              under each dimension. */}
          {allGoals.length > 1 && (() => {
            const segs = goalSegs(allGoals);
            return (
              <div className="caveat" style={{ display: "block" }}>
                <div><strong>Cost is not comparable across these rows.</strong> The
                  window's spend bought {allGoals.length} different things, and an ad
                  group buying link clicks is not comparable with one buying conversions.</div>
                <div style={{ marginTop: 9, borderRadius: 3, overflow: "hidden" }}>
                  <SplitBar segments={segs} height={14} />
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginTop: 7, fontSize: 11.5 }}>
                  {segs.map(([name, spend, tone], i) => (
                    <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                      <span style={{ width: 9, height: 9, borderRadius: 2, background: `var(${tone})` }} />
                      {name} <strong><Cash v={spend} /></strong></span>
                  ))}
                </div>
              </div>
            );
          })()}

          {copyDims.map(([dim, label, rows], di) => (
            <Fragment key={dim}>
              <div className="stage-head" style={di ? { marginTop: 18 } : undefined}>
                <h2>By {label.toLowerCase()}</h2>
              </div>
              <div className="table-wrap">
                <table>
                  <thead><tr>
                    <th scope="col">{label}</th>
                    <th scope="col" className="num">Ads</th>
                    <th scope="col" className="num">Spend</th>
                    <th scope="col" className="num">Conv</th>
                    <th scope="col" className="num">CPA</th>
                    <th scope="col" className="num">Rank in goal</th>
                    <th scope="col">What that spend was buying</th>
                  </tr></thead>
                  <tbody>
                    {rows.map((r, i) => {
                      const rr = r.rates ?? {};
                      const goals = r.optimization_goals ?? [];
                      const gsegs = goalSegs(goals);
                      return (
                        <tr key={i}>
                          <td className="name"><strong>{r.value}</strong>
                            {!r.spend_sufficient && <div className="why">below the spend floor — shown, but thin</div>}
                          </td>
                          <td className="num">{num(r.ads_run)}</td>
                          <td className="num"><Cash v={r.spend} /></td>
                          <td className="num">{num(r.conversions)}</td>
                          <td className="num"><Cash v={rr.cpa} /></td>
                          <td className="num">
                            {r.comparable_on_cost ? (r.rank_within_goal || "--")
                              : <span className="muted" title="ran under more than one optimization goal">not rankable</span>}
                          </td>
                          <td style={{ minWidth: 220 }}>
                            {goals.length ? (
                              <>
                                <div style={{ borderRadius: 3, overflow: "hidden" }}>
                                  <SplitBar segments={gsegs} height={9} />
                                </div>
                                <div className="why">{gsegs.map(([name]) => name).join(" · ")}</div>
                              </>
                            ) : <span className="muted small">no goal recorded</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Fragment>
          ))}

          {absent.length > 0 && (
            <p className="sub" style={{ marginTop: 10 }}>
              <strong>{capitalize(absent.join(", "))}{absent.length > 1 ? " are" : " is"} absent rather than empty.</strong>
              {" "}Each returns a single unlabelled row holding every ad in the window, and
              there is nothing to group by until the creative has been classified.
            </p>
          )}
        </section>
      )}

      {/* ========================================== WHICH ANGLES EARNED THEIR PLACE
          Spend share beside conversion share, in adjacent columns, because
          THAT CONTRAST IS THE FINDING. */}
      {arows.length > 0 && (
        <section className="stage" id="angles" aria-label="Which angles earned their place">
          <div className="stage-head">
            <h2>Which angles earned their place</h2>
            <span className="state">Source: <code>ads.angle_performance</code></span>
          </div>

          {bars.length > 0 && (
            <div className="card pad0" style={{ marginBottom: 16 }}>
              <div className="card-head"><h2 className="card-t">CPA by angle</h2>
                <span className="more">sorted; identity is the label, not the colour</span></div>
              <BarChart rows={bars} kind="cpa" />
              <p className="n" style={{ margin: "0 0 12px" }}>
                Only the {bars.length} angle(s) comparable on cost are plotted. An
                angle with no bar had no conversions in this window, which is a finding
                and not an omission.
              </p>
            </div>
          )}

          <div className="table-wrap">
            <table>
              <thead><tr>
                <th scope="col">Angle</th><th scope="col">Family</th>
                <th scope="col" className="num">Spend</th>
                <th scope="col" className="num">Spend share</th>
                <th scope="col" className="num">Conv share</th>
                <th scope="col" className="num">Conv</th>
                <th scope="col" className="num">CPA</th>
                <th scope="col" className="num">Rank in goal</th>
                <th scope="col" className="num">Tagged</th>
                <th scope="col" className="num">Inherited</th>
                <th scope="col">State</th>
              </tr></thead>
              <tbody>
                {arows.map((r, i) => {
                  const rr = r.rates ?? {};
                  return (
                    <tr key={r.angle_slug ?? i}>
                      <td className="name"><strong>{r.angle_name || "--"}</strong>
                        {r.definition && <div className="why">{r.definition}</div>}
                        {!r.spend_sufficient && <div className="why">below the spend floor — ran, but not enough to read</div>}
                      </td>
                      <td>{r.family ? <span className="pill quiet">{r.family}</span> : <Dash />}</td>
                      <td className="num"><Cash v={r.spend} /></td>
                      <td className="num"><strong>{pct(r.spend_share_pct, 1)}</strong></td>
                      <td className="num"><strong>{pct(r.conversion_share_pct, 1)}</strong></td>
                      <td className="num">{num(r.conversions)}</td>
                      <td className="num">
                        {r.comparable_on_cost ? <Cash v={rr.cpa} />
                          : <span className="muted" title="ran under more than one optimization goal">not comparable</span>}
                      </td>
                      <td className="num">{r.rank_within_goal || "--"}</td>
                      <td className="num">{num(r.tagged_ads)}</td>
                      <td className="num">{num(r.inherited_ads)}
                        {(f(r.inherited_ads) ?? 0) > 0 && <div className="why">never judged</div>}
                      </td>
                      <td>{r.state ? <span className="pill quiet">{r.state.replace(/_/g, " ")}</span> : <Dash />}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="sub" style={{ marginTop: 10 }}>Spend share against conversion share is
            the comparison that matters. Inherited means the angle was read off the
            campaign_asset chain rather than judged by anyone.</p>
        </section>
      )}

      {/* ================================================ WHAT WE'VE NEVER SAID
          The set difference the angle bank exists for. A free-text handle
          cannot produce this list, because you cannot count what nobody wrote down. */}
      {(neverRun.length > 0 || underSpent.length > 0 || proposals.length > 0) && (
        <section className="stage" id="gaps" aria-label="What we have never said">
          <div className="stage-head">
            <h2>What we've never said</h2>
          </div>

          <div className="boards">
            <section className="board" aria-label="Never run">
              <header><h2>Never run</h2></header>
              <div className="body">
                {!neverRun.length ? (
                  <p className="muted small" style={{ margin: 0 }}>Every active angle has run in
                    this window.</p>
                ) : neverRun.map((r, i) => (
                  <div key={i} style={{ marginBottom: 12 }}>
                    {r.family && <span className="pill quiet">{r.family}</span>}
                    <strong>{nameOf(r)}</strong>
                    {r.definition && <div className="why">{r.definition}</div>}
                    <PriorFor r={r} prior={pri} />
                  </div>
                ))}
              </div>
            </section>

            <section className="board" aria-label="Under spent">
              <header><h2>Ran, but not enough to read</h2></header>
              <div className="body">
                {!underSpent.length ? (
                  <p className="muted small" style={{ margin: 0 }}>Every angle that ran cleared
                    the spend floor.</p>
                ) : (
                  <>
                    <p className="small muted" style={{ margin: "0 0 10px" }}>These are not losing
                      angles. They are angles with no verdict, and treating the two the
                      same is how an idea gets retired before it was tried.</p>
                    {underSpent.map((r, i) => {
                      const rr = r.rates ?? {};
                      return (
                        <div key={i} style={{ marginBottom: 12 }}>
                          {r.family && <span className="pill quiet">{r.family}</span>}
                          <strong>{nameOf(r)}</strong>
                          <div className="why"><Cash v={r.spend} /> ·{" "}
                            {num(r.conversions)} conversion(s) ·{" "}
                            CPA <Cash v={rr.cpa} /></div>
                          <PriorFor r={r} prior={pri} />
                        </div>
                      );
                    })}
                  </>
                )}
              </div>
            </section>
          </div>

          {/* Proposals sit here because this is where the gap is visible. They
              carry a third label and the command, because this repo cannot
              file them: `intel record` takes a path and a person runs it. */}
          {proposals.length > 0 && (
            <>
              <div className="stage-head" style={{ marginTop: 16 }}>
                <h2>Proposals</h2>
                <span className="state">{proposals.length} · none of them filed</span>
              </div>
              {proposals.map((p, i) => (
                <div key={i} role="note" style={{ margin: "12px 0 12px 2px", padding: "2px 0 2px 16px",
                                                  borderLeft: "3px dashed var(--line-2)" }}>
                  <div className="eyebrow">Proposal — not filed</div>
                  <p style={{ margin: "3px 0 0", maxWidth: "80ch" }}>
                    <strong>{p.angle_name}</strong>
                    {p.family && <> <span className="pill quiet">{p.family}</span></>}
                  </p>
                  {p.gap && <div className="small" style={{ marginTop: 3 }}>{p.gap}</div>}
                  <div className="small muted" style={{ marginTop: 4 }}>
                    {p.already_tested ? <>Tested before — this is a re-run, not a
                      new idea.</> : <>No prior test on record.</>}
                    {(p.prior_experiments ?? []).map((e, j) => (
                      <div key={j} style={{ marginTop: 2 }}>{e.name || JSON.stringify(e)}
                        {e.conclusion ? <> — {e.conclusion}</> : e.name ? <> — no conclusion recorded</> : null}</div>
                    ))}
                  </div>
                  {p.how_to_file && (
                    <div style={{ marginTop: 6 }}><code>{p.how_to_file}</code></div>
                  )}
                </div>
              ))}
            </>
          )}
        </section>
      )}

      {/* ==================================================== TESTS IN FLIGHT */}
      {exRows.length > 0 && (
        <section className="stage" id="experiments" aria-label="Tests in flight">
          <div className="stage-head">
            <h2>Tests in flight</h2>
          </div>
          {(f(ex.awaiting_launch) ?? 0) > 0 && (
            <div className="card quiet" style={{ marginBottom: 16 }}>
              {num(ex.awaiting_launch)} proposed experiment(s) have never been
              launched. A proposal nobody ran is a different problem from a test that
              came back inconclusive, and only one of them is about the advertising.
            </div>
          )}
          <div className="table-wrap">
            <table>
              <thead><tr>
                <th scope="col">Name</th><th scope="col">State</th>
                <th scope="col">Hypothesis</th><th scope="col">Primary metric</th>
                <th scope="col">Conclusion</th>
              </tr></thead>
              <tbody>
                {exRows.map((r, i) => (
                  <tr key={i}>
                    <td className="name"><strong>{r.name}</strong></td>
                    <td>
                      <span className={`pill ${expTone[r.state ?? ""] ?? "quiet"}`}>{(r.state || "unknown").replace(/_/g, " ")}</span>
                    </td>
                    <td className="small">{r.hypothesis || "--"}</td>
                    <td>{r.primary_metric ? <span className="pill quiet">{r.primary_metric}</span> : <Dash />}</td>
                    <td className="small">{r.conclusion ? r.conclusion : <span className="muted">not concluded</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ===================================================== WAITING ON YOU
          The only section where the verb belongs to the reader rather than to
          this repo. Nothing below can be done by an agent: an angle goes
          active and an experiment gets a conclusion only when a person signs
          it, under their own name, and the schema enforces it. */}
      {waitingN > 0 && (
        <section className="stage" id="waiting" aria-label="Waiting on you">
          <div className="stage-head">
            <h2>Waiting on you</h2>
          </div>

          <div className="boards">
            {unsigned.length > 0 && (
              <section className="board" aria-label="Angles awaiting a signature">
                <header><h2>Angles awaiting a signature</h2>
                  <div className="note">an angle is active only once a person signs it
                    (<code>angle_active_is_signed</code>)</div></header>
                <div className="body">
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {unsigned.map((a, i) => (
                      <li key={i} style={{ marginBottom: 4 }}><strong>{nameOf(a)}</strong>
                        {a.family && <> <span className="pill quiet">{a.family}</span></>}</li>
                    ))}
                  </ul>
                  <p className="small" style={{ margin: "10px 0 0" }}>
                    <a href={w.where || "http://127.0.0.1:8000/angles"}>Sign them in
                      growth-engine</a> — not this dashboard, and never{" "}
                    <code>localhost</code>.</p>
                </div>
              </section>
            )}

            {unlaunched.length > 0 && (
              <section className="board" aria-label="Experiments never launched">
                <header><h2>Experiments never launched</h2>
                  <div className="note">proposed here, launched by a person in Ads Manager</div></header>
                <div className="body">
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {unlaunched.map((e, i) => (
                      <li key={i} style={{ marginBottom: 4 }}>{e.name || JSON.stringify(e)}
                        {e.hypothesis && <div className="why">{e.hypothesis}</div>}</li>
                    ))}
                  </ul>
                </div>
              </section>
            )}

            {unconcluded.length > 0 && (
              <section className="board" aria-label="Experiments awaiting a conclusion">
                <header><h2>Experiments awaiting a conclusion</h2>
                  <div className="note">the result is computed; the verdict is written by a
                    person and attributed to them</div></header>
                <div className="body">
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {unconcluded.map((e, i) => (
                      <li key={i} style={{ marginBottom: 4 }}>{e.name || JSON.stringify(e)}
                        {e.primary_metric && <div className="why">primary metric:{" "}
                          {e.primary_metric}</div>}</li>
                    ))}
                  </ul>
                </div>
              </section>
            )}

            {unmapped.length > 0 && (
              <section className="board" aria-label="Unmapped handles">
                <header><h2>Unmapped handles</h2>
                  <div className="note">mapping one is a decision about vocabulary, so a
                    person files it rather than a tagging pass</div></header>
                <div className="body">
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th scope="col">Observed</th>
                        <th scope="col" className="num">Reviews</th>
                        <th scope="col">Last seen</th></tr></thead>
                      <tbody>
                        {unmapped.map((c, i) => (
                          <tr key={i}><td>{c.observed || c.handle || JSON.stringify(c)}</td>
                            <td className="num">{c.reviews !== undefined ? c.reviews : "--"}</td>
                            <td className="small muted">{c.last_seen || "--"}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </section>
            )}
          </div>
        </section>
      )}

      {/* ============================================== NOTHING TO REPORT, AND WHY
          "It loaded and the answer is none" and "it could not be read" are
          different claims -- the first is a finding, the second an absence of
          evidence -- and an empty box says neither. */}
      {silent.length > 0 && (
        <section className="stage" aria-label="Nothing to report">
          <div className="stage-head">
            <h2>Nothing to report, and why</h2>
          </div>
          <div className="card" style={{ padding: 0 }}>
            {silent.map(([anchor, title, code, why]) => (
              <Quiet key={anchor} anchor={anchor} title={title} code={code} why={why} />
            ))}
          </div>
        </section>
      )}

      {/* ====================================================== TRUST, IN FULL
          The headline facts are the scoreboard's two right-hand tiles and,
          when something is wrong, the first finding on the page. Everything
          behind them lives here. It is never collapsed when the checks failed. */}
      <section className="stage" id="trust" aria-label="Trust">
        <details className="card" style={{ padding: 0 }} open={unhealthy}>
          <summary style={{ cursor: "pointer", padding: "14px 20px", display: "flex",
                            alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h2 style={{ margin: 0 }}>Trust, in full</h2>
            {unhealthy
              ? <span className="pill stop">{problems.length} problem(s)</span>
              : <span className="pill ok">no problem reported</span>}
            <span className="state" style={{ marginLeft: "auto" }}>Source: <code>intel status</code>
              {" "}+ <code>ads.match_health</code></span>
          </summary>

          <div style={{ padding: "0 20px 18px" }}>
            {problems.map((prob, i) => (
              <div key={i} className="caveat"><span>{prob}</span></div>
            ))}

            <div className="stats" style={{ marginBottom: 0 }}>
              <div className="stat">
                <div className="k">Credential</div>
                <div className="v" style={{ fontSize: 16, marginTop: 6 }}>
                  {cred.ok ? <span className="pill ok">confirmed</span>
                    : <span className="pill stop">not confirmed</span>}
                </div>
                <div className="sub">{cred.confirmed_as || "no identity returned"}</div>
              </div>
              <div className="stat">
                <div className="k">Coverage</div>
                <div className="v">{num(cov.ad_days)}</div>
                <div className="sub">ad-days · {num(cov.ads)} ads ·{" "}
                  <Cash v={cov.spend} /> · first day{" "}
                  {cov.first_day || "--"}</div>
              </div>
              <div className="stat">
                <div className="k">Approved copy on a live ad</div>
                <div className="v">{pct(match.match_rate)}</div>
                <div className="sub">{num(match.matched)} matched of{" "}
                  {num(match.with_tracked_url)} tracked ·{" "}
                  {num(match.approved_assets)} approved</div>
              </div>
              <div className="stat">
                <div className="k">What counts as a conversion</div>
                {typeof cd === "number" ? (
                  <>
                    <div className="v">{num(cd)}</div>
                    <div className="sub">definition(s) registered for this brand</div>
                  </>
                ) : typeof cd === "string" ? (
                  <div className="v" style={{ fontSize: 15, marginTop: 6 }}>{cd}</div>
                ) : cd && cd.length ? (
                  <>
                    <div className="v">{cd.length}</div>
                    <div className="sub">{cd.map((x) => x.name).join(", ")}</div>
                  </>
                ) : (
                  <>
                    <div className="v" style={{ fontSize: 15, marginTop: 6 }}>none</div>
                    <div className="sub">every CPA on this page is a cost per <em>nothing in
                      particular</em></div>
                  </>
                )}
              </div>
            </div>

            {(t.accounts ?? []).length > 0 && (
              <>
                <div className="stage-head" style={{ marginTop: 16 }}>
                  <h2>Accounts</h2>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead><tr>
                      <th scope="col">Account</th><th scope="col">Active</th>
                      <th scope="col">Currency</th><th scope="col">Insights through</th>
                      <th scope="col">Last structure pull</th>
                    </tr></thead>
                    <tbody>
                      {(t.accounts ?? []).map((a) => (
                        <tr key={a.platform_account_id}>
                          <td className="name">{a.label || a.platform_account_id}
                            <div className="why mono">{a.platform_account_id}</div></td>
                          <td>{a.active ? <span className="pill ok">active</span>
                            : <span className="pill quiet">inactive</span>}</td>
                          <td>{a.currency || "--"}</td>
                          <td className="small">{a.insights_through || "--"}</td>
                          <td className="small">
                            {a.last_structure_ok ? stamp(a.last_structure_ok)
                              : <span className="muted">never succeeded</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {/* The unmatched table prints the EXPECTED utm_content, not only
                the name. "Asset 41 did not match" is a complaint; "asset 41
                should have carried ren-ws-q3-a2 and no ad does" is something a
                person can go and fix. */}
            {(match.unmatched ?? []).length > 0 && (
              <>
                <div className="stage-head" style={{ marginTop: 16 }}>
                  <h2>Approved copy with no ad behind it</h2>
                  <span className="state">{(match.unmatched ?? []).length} asset(s)</span>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead><tr>
                      <th scope="col">Approved asset</th>
                      <th scope="col">Expected utm_content</th>
                      <th scope="col">What is on the ad</th>
                      <th scope="col">Why it did not match</th>
                    </tr></thead>
                    <tbody>
                      {(match.unmatched ?? []).map((u, i) => (
                        <tr key={i}>
                          <td className="name">
                            {u.asset_name || u.name || u.title || u.headline || u.asset_id || u.key || "--"}
                            {u.asset_id && <div className="why mono">{u.asset_id}</div>}</td>
                          <td><code>{u.expected_utm_content || u.utm_content || u.expected || "--"}</code></td>
                          <td className="mono small">{u.observed_utm_content || u.observed || u.found || "--"}</td>
                          <td className="small muted">{u.reason || u.why || u.note || "no ad in this window carries this value"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </details>
      </section>

      {/* ------------------------------------------------------- overflow guard
          A reading none of the groups above claimed. Silently losing an
          opinion is how a brief begins lying by omission. */}
      {orphans.length > 0 && (
        <section className="stage" aria-label="Readings not attached to a section">
          <div className="stage-head">
            <h2>Readings this page could not place</h2>
          </div>
          <p className="sub">Each of these cites a fact from a section above, but its
            section is not one this page maps to a band. They are printed here
            because a reading that disappears is worse than one in the wrong place.</p>
          <div className="card" style={{ padding: 0 }}>
            {orphans.map((r, i) => {
              const subj = r.subject?.name;
              return (
                <div key={i} className="finding">
                  <div className="rank" aria-hidden="true">·</div>
                  <div className="says"><p>
                    {subj ? <><strong>{subj}</strong>{" "}</> : null}{r.says}</p></div>
                </div>
              );
            })}
          </div>
        </section>
      )}

    </PageFrame>
    </Guards.Provider>
  );
}
