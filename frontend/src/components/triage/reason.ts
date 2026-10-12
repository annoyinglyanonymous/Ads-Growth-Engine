/* reason_line and lead_issue, ported from ui.py, where they were registered as
 * Jinja globals for _triage.html (macro bodies do not see page scope). Pure
 * functions: no DOM, no arithmetic, nothing invented.
 *
 * Also the two Python-truthiness helpers every guard in the triage leans on:
 * Jinja's `{% if x %}` is Python's bool(), where an empty list or dict is
 * false, and its `is mapping` is isinstance(x, dict).
 */
import type { TriageCampaign, TriageIssue, TriageRecommendation } from "./types";

/** Jinja's `is mapping`: a plain object, not an array, not null. */
export function isMapping(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Python truthiness, which Jinja's `if` uses: null, false, 0, "", an empty
 *  list and an empty dict are all false. */
export function truthy(v: unknown): boolean {
  if (v === null || v === undefined || v === false || v === 0 || v === "") return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.keys(v as object).length > 0;
  return Boolean(v);
}

/** Jinja's `is not none`. An absent key is read the same way as null here:
 *  the template's lenient Undefined would have printed "--" for it. */
export function notNone(v: unknown): boolean {
  return v !== null && v !== undefined;
}

// A summary sentence that restates the CPA move -- "CPA +22%.", "CPA is up
// 4.80% on the prior 7 days..." -- which the row's chips already show.
const FIGURE_LEAD = /^CPA (is )?([+\-−\d]|up\b|down\b|fell\b|rose\b)/;
// A summary sentence that gives advice. The advice belongs to the bucket's own
// issue, and the model's one-liner sometimes names a secondary issue's.
const ADVICE = /^(Worth|Test|Monitor|Narrow|Cut|Pause|Stop|Shift|Move|Keep|Refresh|Nothing to change)\b/;
const SENTENCE = /.+?[.!?](?=\s|$)/g;
const PRIORITY = new Map<string, number>([["primary", 0], ["secondary", 1]]);

/**
 * The part of a campaign's one-line summary its row does not already show.
 *
 * A round-2 summary is four short sentences: the CPA move, the stage that
 * appears to contribute most, the advice, the confidence. On the folded row
 * the move is in the 7d / 14d chips beside it and the confidence is in the
 * opened panel, so they are dropped here. So is the advice -- not because it
 * is wrong to show, but because the summary sometimes carries a secondary
 * issue's advice ("Worth testing the form") under an Act heading, and the
 * row must not contradict its group. What is left is the reading.
 *
 * A cut, never a rewrite: every word printed is the summary's own, so
 * check_figures has already seen it. When nothing would be left, the whole
 * summary is returned.
 */
export function reasonLine(c: TriageCampaign): string {
  const s = String(c.summary || c.headline || c.why || "").replace(/\s+/g, " ").trim();
  const found = s.match(SENTENCE);
  const parts = (found && found.length ? found : [s]).map((p) => p.trim());
  const chips = isMapping(c.movement) && Object.keys(c.movement).length > 0;
  const keep = parts.filter((p) =>
    !(chips && FIGURE_LEAD.test(p)) && !/confidence\.$/i.test(p) && !ADVICE.test(p));
  return keep.length ? keep.join(" ") : s;
}

/**
 * The issue a campaign's bucket came from: the strongest type, primary first.
 *
 * creative.bucket_of sets the bucket from the strongest recommendation among
 * the issues, which need not be the primary one -- a campaign can be Act on a
 * secondary delivery cut while its primary issue is a test. The opened card
 * leads with the issue that put it in its group.
 */
export function leadIssue(c: TriageCampaign): TriageIssue {
  const iss = (Array.isArray(c.issues) ? c.issues : []).filter(isMapping) as TriageIssue[];
  const want = String(c.bucket || "").toLowerCase();
  const same = iss.filter((i) => {
    const rec: TriageRecommendation = isMapping(i.recommendation) ? i.recommendation : {};
    return String(rec.type || "").toLowerCase() === want;
  });
  // A stable sort, like Python's: ties keep the order given.
  same.sort((a, b) => (PRIORITY.get(String(a.priority)) ?? 2) - (PRIORITY.get(String(b.priority)) ?? 2));
  return (same.length ? same : iss.length ? iss : [{}])[0];
}
