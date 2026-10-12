/* The shape of a published suggestions file, as far as the triage reads it.
 *
 * Three shapes of entry (see Triage.tsx): round 2 carries `bucket`, shape 2
 * without it carries `issues`, and an older file carries `problems` (each
 * with its fix) or plain `changes`. Every field is optional because every
 * section is guarded on its own data -- a missing piece drops out alone --
 * and anything the component does not read is `unknown`.
 *
 * Figures (spend, cpa, pct_change, ...) are typed `unknown` on purpose: they
 * go straight to format.ts, which answers "--" for anything that is not a
 * number, and nothing here does arithmetic on them.
 */

export interface TriageOutcome {
  status?: string | null;
  implemented_on?: string | null;
  match?: string | null;
  settled_days?: number | null;
  result?: string | null;
  [key: string]: unknown;
}

export interface TriageBrief {
  format?: string | null;
  angle_or_hook?: string | null;
  opening_line?: string | null;
  built_from?: string | null;
  [key: string]: unknown;
}

export interface TriageTestPlan {
  variable?: string | null;
  control?: string | null;
  challenger?: string | null;
  challenger_kind?: string | null;
  challenger_ad?: string | null;
  challenger_kind_adjusted_from?: string | null;
  brief?: TriageBrief | null;
  setup?: string | null;
  tag?: string | null;
  hold_constant?: string[] | null;
  evaluate_on?: string[] | null;
  decide_when?: string | null;
  [key: string]: unknown;
}

export interface TriageRecommendation {
  type?: string | null;
  action?: string | null;
  expected_outcome?: string | null;
  [key: string]: unknown;
}

export interface TriageConfidence {
  level?: string | null;
  reason?: string | null;
  [key: string]: unknown;
}

export interface TriageIssue {
  id?: string | null;
  status?: string | null;
  open_since?: string | null;
  priority?: string | null;
  observation?: string | null;
  evidence?: string | null;
  diagnosis?: string | null;
  recommendation?: TriageRecommendation | null;
  confidence?: TriageConfidence | null;
  test_plan?: TriageTestPlan | null;
  outcome?: TriageOutcome | null;
  incomplete?: string[] | null;
  type_adjusted_from?: string | null;
  priority_adjusted_from?: string | null;
  confidence_adjusted_from?: string | null;
  test_plan_dropped?: string | boolean | null;
  [key: string]: unknown;
}

/** One rung of the window ladder (017's cur_m / pri_m, never summed here). */
export interface TriageRung {
  key?: string | null;
  label?: string | null;
  since?: string | null;
  until?: string | null;
  provisional?: boolean | null;
  spend?: unknown;
  conversions?: unknown;
  cpa?: unknown;
  cpm?: unknown;
  link_ctr?: unknown;
  cvr?: unknown;
  daily_frequency?: unknown;
  [key: string]: unknown;
}

export interface TriageFunnelMetric {
  metric?: string | null;
  cur?: unknown;
  prior?: unknown;
  pct_change?: unknown;
  [key: string]: unknown;
}

export interface TriageFunnel {
  cur?: { since?: string | null; until?: string | null; [key: string]: unknown } | null;
  prior?: { since?: string | null; until?: string | null; [key: string]: unknown } | null;
  metrics?: TriageFunnelMetric[] | null;
  shares?: Record<string, unknown> | null;
  primary_driver?: string | null;
  evidence?: string | null;
  spend_to_baseline_cpa?: unknown;
  fatigue_consistent?: boolean | null;
  [key: string]: unknown;
}

export interface TriageAdGroup {
  name?: string | null;
  goal?: string | null;
  spend?: unknown;
  share_pct?: unknown;
  cpa?: unknown;
  rank?: unknown;
  of?: unknown;
  [key: string]: unknown;
}

export interface TriageAd {
  name?: string | null;
  role?: string | null;
  spend?: unknown;
  share_pct?: unknown;
  cpa?: unknown;
  [key: string]: unknown;
}

export interface TriageWhere {
  ad_groups?: TriageAdGroup[] | null;
  ads?: TriageAd[] | null;
  [key: string]: unknown;
}

export interface TriageSpread {
  ads_with_spend?: unknown;
  top_two_ads_share_pct?: unknown;
  ads_with_no_conversions?: unknown;
  share_on_ads_with_no_conversions_pct?: unknown;
  [key: string]: unknown;
}

export interface TriageFamilyMetric {
  cur?: unknown;
  p25?: unknown;
  p50?: unknown;
  p75?: unknown;
  position?: string | null;
  percentile?: unknown;
  [key: string]: unknown;
}

/** contracts-baselines E2: what is typical for this kind of campaign. */
export interface TriageFamily {
  family?: string | null;
  audience?: string | null;
  goal?: string | null;
  band_level?: string | null;
  campaign_weeks?: unknown;
  metrics?: Record<string, TriageFamilyMetric | null | undefined> | null;
  [key: string]: unknown;
}

export interface TriageImpact {
  goal?: string | null;
  excess_cost?: unknown;
  impact_score?: unknown;
  contribution_share_pct?: unknown;
  evidence?: string | null;
  [key: string]: unknown;
}

/** 017's pct moves keyed by pair ("7v7", "14v14"), then by metric. */
export type TriageMovement = Record<string, Record<string, unknown> | null | undefined>;

/** A resolved or dropped item from the last review (issue or legacy problem). */
export interface TriageHistoryItem {
  id?: string | null;
  by?: string | null;
  problem?: string | null;
  observation?: string | null;
  open_since?: string | null;
  change?: string | null;
  evidence?: string | null;
  [key: string]: unknown;
}

/** A legacy problem, from files published after 2026-09-24 and before the report. */
export interface TriageProblem {
  id?: string | null;
  status?: string | null;
  open_since?: string | null;
  problem?: string | null;
  fix?: string | null;
  [key: string]: unknown;
}

export interface TriageCampaign {
  campaign?: string | null;
  bucket?: string | null;
  rating?: string | null;
  rating_adjusted_from?: string | null;
  rating_reasons?: string[] | null;
  summary?: string | null;
  headline?: string | null;
  why?: string | null;
  status?: string | null;
  optimization_goals?: string[] | null;
  spend?: unknown;
  conversions?: unknown;
  cpa?: unknown;
  impact?: TriageImpact | null;
  movement?: TriageMovement | null;
  causal_flags?: string[] | null;
  unverified_figures?: string[] | null;
  family?: TriageFamily | null;
  ladder?: TriageRung[] | null;
  funnel?: TriageFunnel | null;
  where?: TriageWhere | null;
  spend_spread?: TriageSpread | null;
  issues?: TriageIssue[] | null;
  not_yet?: string[] | null;
  resolved?: TriageHistoryItem[] | null;
  dropped?: TriageHistoryItem[] | null;
  problems?: TriageProblem[] | null;
  changes?: unknown[] | null;
  [key: string]: unknown;
}

/** The published file. Only these three keys are read here; the page reads
 *  the rest (account_summary, outcomes, family_bands, shape, until, ...). */
export interface TriagePublished {
  triage?: TriageCampaign[] | null;
  previous_file?: string | null;
  data_changed?: boolean | null;
  [key: string]: unknown;
}
