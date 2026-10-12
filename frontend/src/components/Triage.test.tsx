/* Triage.tsx, rendered against published files of every shape. Ported from
 * tests/test_triage_render.py, test for test and under the same names; the
 * string-on-HTML assertions became DOM assertions with the same intent.
 *
 * The fixtures in tests/fixtures/suggestions/ are synthetic -- no real campaign
 * or ad copy -- and cover the three shapes a published file can have: plain
 * `changes` (before 2026-09-24), `problems` with their fixes, and the analyst
 * report (`issues`, shape 2; `bucket`, round 2). An old file must keep
 * rendering after the report lands: nothing rewrites the files already on disk.
 *
 * The tests that render suggestions.html whole (the "page puts what matters"
 * family and the family-bands table) belong to the page, not to this component.
 */
import { render } from "@testing-library/react";
import { Triage, leadIssue, reasonLine, type TriagePublished } from "./Triage";
import oldChanges from "../../../tests/fixtures/suggestions/old_changes.json";
import oldProblems from "../../../tests/fixtures/suggestions/old_problems.json";
import report from "../../../tests/fixtures/suggestions/report.json";
import reportV3 from "../../../tests/fixtures/suggestions/report_v3.json";

const FIX = { old_changes: oldChanges, old_problems: oldProblems, report, report_v3: reportV3 };
type Name = keyof typeof FIX;
// Loosely typed on purpose: the Python tests mutate the fixtures freely
// (pop a key, append an entry), and so do these.
type Loose = { triage: Record<string, any>[]; [key: string]: any };

/** A fresh copy, like json.loads of the file: every test mutates its own. */
function load(name: Name): Loose {
  return JSON.parse(JSON.stringify(FIX[name])) as Loose;
}

function draw(pub: Loose): HTMLElement {
  return render(<Triage published={pub as TriagePublished} />).container;
}

const text = (el: Element | null | undefined): string =>
  (el?.textContent ?? "").replace(/\s+/g, " ").trim();
const all = (root: ParentNode, sel: string): Element[] => Array.from(root.querySelectorAll(sel));

/** The card of the campaign with this name: the `details.tcamp` whose summary's .ttl is it. */
function campaign(root: ParentNode, name: string): HTMLDetailsElement {
  const ttl = all(root, "details.tcamp > summary .ttl").find((e) => text(e) === name);
  if (!ttl) throw new Error(`no campaign card named ${name}`);
  return ttl.closest("details.tcamp") as HTMLDetailsElement;
}
const summaryOf = (card: Element): Element => card.querySelector(":scope > summary")!;
const panel = (card: Element, label: string): Element | null =>
  card.querySelector(`section[aria-label="${label}"]`);
const fold = (card: Element, label: string): Element | undefined =>
  all(card, "details.tfold").find((d) => text(d.querySelector(":scope > summary")) === label);

/** "a group or campaign starts open"; no script, no button. */
function folded(root: ParentNode) {
  expect(all(root, "details[open]")).toHaveLength(0);
  expect(all(root, "script, button")).toHaveLength(0);
}

/** The indices of `marks` among the texts in `seq`, each found by prefix;
 *  every mark must be there and they must come in the order given. */
function inOrder(seq: string[], marks: string[]) {
  const at = marks.map((m) => seq.findIndex((s) => s.startsWith(m)));
  for (const [i, m] of marks.entries()) expect(at[i], m).toBeGreaterThanOrEqual(0);
  expect(at).toEqual([...at].sort((a, b) => a - b));
}

const isObj = (v: unknown): v is Record<string, any> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

// ---------------------------------------------------------------------------

describe("every shape", () => {
  it.each(["old_changes", "old_problems", "report"] as Name[])(
    "test_every_shape_renders_folded (%s)", (name) => {
      const pub = load(name);
      const root = draw(pub);
      expect(all(root, "details.tgroup").length).toBeGreaterThan(0);
      folded(root);
      for (const c of pub.triage) expect(root.textContent).toContain(c.campaign);
      // Every campaign is its own fold inside its group.
      expect(all(root, "details.tcamp")).toHaveLength(pub.triage.length);
    });

  it("test_old_changes_render_as_fixes", () => {
    const t = text(draw(load("old_changes")));
    expect(t).toContain("Pause the weakest ad in the main ad group.");
    expect(t).toContain("How to fix it");
    expect(t, "an unrated campaign went missing").toContain("Not rated");
  });

  it("test_old_problems_render_with_their_fixes", () => {
    const t = text(draw(load("old_problems")));
    expect(t).toContain("How to fix it");
    expect(t).toContain("The lead ad's click-through fell for two weeks.");
    expect(t).toContain("still open since 2026-09-23");
    expect(t).toContain("Since the last review");
    expect(t).toContain("1 still open");
    expect(t).toContain("2 new");
    expect(t).toContain("Resolved since the last review");
    expect(t).toContain("not confirmed fixed");
    expect(t).toContain("Not found in the data");
  });
});

const SECTIONS = ["What changed", "What's causing it", "Where it's concentrated", "Issues", "What not to do yet"];

describe("the shape-2 report", () => {
  it("test_the_report_renders_every_section_in_order", () => {
    const root = draw(load("report"));
    const alpha = campaign(root, "Campaign Alpha");
    inOrder(all(alpha, "h4.tsec").map(text), SECTIONS);
    expect(root.textContent, "a report fell through to the legacy card").not.toContain("How to fix it");
  });

  it("test_the_provisional_row_is_marked_once_per_campaign", () => {
    const pub = load("report");
    const root = draw(pub);
    const withLadder = pub.triage.filter((c) => Array.isArray(c.ladder) && c.ladder.length);
    expect(all(root, "span.tag").filter((e) => text(e) === "provisional")).toHaveLength(withLadder.length);
    expect(all(root, "tr.prov")).toHaveLength(withLadder.length);
    expect(root.textContent).toContain("Provisional: Meta can still restate the last three days.");
  });

  it("test_the_funnel_says_which_way_each_change_went", () => {
    const root = draw(load("report"));
    const t = root.textContent ?? "";
    expect(t).toContain("+61.5%");
    expect(t).toContain("−32.4%");
    // CPA up is worse; CVR down is worse; CPM up is worse; frequency has no word.
    const row = (label: string) => all(root, "tr").find((tr) => text(tr.querySelector("td")) === label)!;
    expect(text(row("CPA"))).toContain("worse");
    const freq = text(row("Daily frequency"));
    expect(freq).not.toContain("better");
    expect(freq).not.toContain("worse");
    expect(t).toContain("Share of the CPA change");
    expect(all(root, ".chip-d.primary").some((e) => /^Post-click \(CVR\)\s*85%$/.test(text(e)))).toBe(true);
    const ev = all(root, "p.tnote b").find((b) => text(b) === "moderate")!;
    expect(text(ev.parentElement)).toMatch(/^Evidence: moderate/);
    // Spend over the prior CPA is a count of conversions it would have bought,
    // and is worded as one; "6.5x its baseline CPA" read as an alarm.
    expect(t).toMatch(/would have bought\s+6\.5 conversions/);
    expect(t).not.toContain("its own baseline CPA");
  });

  it("test_daily_frequency_shows_only_when_a_rung_has_it", () => {
    const pub = load("report");
    const camp = pub.triage.find((c) => Array.isArray(c.ladder) && c.ladder.length)!;
    for (const w of camp.ladder) delete w.daily_frequency;
    pub.triage = [camp];
    const hasFreqColumn = (root: Element) => all(root, "th").some((e) => text(e) === "Daily freq");
    expect(hasFreqColumn(draw(pub))).toBe(false);
    camp.ladder[1].daily_frequency = 1.12;
    expect(hasFreqColumn(draw(pub))).toBe(true);
  });

  it("test_the_issues_carry_their_chips_and_test_plan", () => {
    const root = draw(load("report"));
    const t = root.textContent ?? "";
    const chips = all(root, ".rtype").map(text);
    for (const chip of ["ACT", "TEST", "MONITOR"]) expect(chips, chip).toContain(chip);
    const prios = all(root, "div.prio").map(text);
    for (const label of ["Primary", "Secondary", "Monitor"]) expect(prios).toContain(label);
    expect(t).toContain("Likely cause (hypothesis)");
    const arms = all(root, ".tplan .arm > b").map(text);
    expect(arms).toContain("Control");
    expect(arms).toContain("Challenger");
    expect(t).toContain("audience · budget · creative");
    expect(t).toMatch(/The review proposed ACT; the\s+evidence supports TEST\./);
    expect(t).toMatch(/gave this medium\s+confidence; the evidence supports low/);
    expect(t).toContain("test plan was dropped: the evidence does not support a test yet");
    expect(t).toContain("Do not raise the budget until CVR recovers.");
    expect(t).toContain("ACT / TEST / MONITOR is what the evidence supports, not a decision.");
  });

  it("test_the_report_counts_issues_since_the_last_review", () => {
    const t = text(draw(load("report")));
    expect(t).toContain("1 still open");
    expect(t).toContain("3 new"); // two on Alpha, one on Beta
  });

  it("test_a_missing_where_falls_back_to_the_spread_sentence", () => {
    const beta = text(campaign(draw(load("report")), "Campaign Beta"));
    expect(beta).toContain("91.0% of the spend");
    expect(beta, "an empty headline should fall back to why")
      .toContain("Steady, with too little data to call a change.");
  });

  it("test_a_report_without_a_funnel_still_renders", () => {
    const pub = load("report");
    const alpha = pub.triage[0];
    for (const k of ["funnel", "where", "ladder"]) delete alpha[k];
    const t = draw(pub).textContent ?? "";
    expect(t).not.toContain("What's causing it");
    expect(t).toContain("Conversion rate after the click fell");
  });

  it("test_a_report_with_nothing_but_issues_renders", () => {
    const pub = load("report");
    pub.triage = [{ campaign: "Bare", rating: "red", issues: [
      { observation: "o", recommendation: null, confidence: null }] }];
    const t = draw(pub).textContent ?? "";
    expect(t).toContain("Bare");
    expect(t).toContain("Unranked");
  });
});

// ---------------------------------------------------------------------------
// Round 2 (contracts2 D4): buckets, movement, folded issues, asset vs new,
// outcomes. report_v3.json is synthetic, like the other fixtures.
// ---------------------------------------------------------------------------

describe("round 2", () => {
  it("test_v3_still_folds_and_renders_every_campaign", () => {
    const pub = load("report_v3");
    const root = draw(pub);
    folded(root);
    expect(all(root, "details.tcamp")).toHaveLength(pub.triage.length);
    for (const c of pub.triage) expect(root.textContent).toContain(c.campaign);
  });

  it("test_buckets_group_in_order_act_test_monitor_no_change", () => {
    const root = draw(load("report_v3"));
    const order = all(root, "section.tlist").map((s) => [s.id, s.getAttribute("data-bucket")]);
    expect(order).toEqual([["act", "act"], ["test", "test"], ["monitor", "monitor"], ["no_change", "no_change"]]);
    // Inside a group, the order given (integration sorts by impact).
    const act = root.querySelector('section[data-bucket="act"]')!;
    const names = all(act, ".ttl").map(text);
    expect(names.indexOf("Campaign Delta")).toBeLessThan(names.indexOf("Campaign Hotel"));
    // No rating group is drawn when the entries carry buckets, and a group is
    // a list rather than a fold: every row is one line, so it shows.
    expect(root.textContent).not.toContain("Critical");
    expect(root.textContent).not.toContain("Needs changes");
    expect(all(root, ".tgroup")).toHaveLength(0);
  });

  it("test_a_long_group_folds_its_tail_behind_a_count", () => {
    const pub = load("report_v3");
    const base = pub.triage.find((c) => c.bucket === "test")!;
    for (let i = 0; i < 6; i++) pub.triage.push({ ...base, campaign: `Campaign Test ${i}` });
    const test = draw(pub).querySelector('section[data-bucket="test"]')!;
    // Seven test campaigns: five show, two fold behind a summary that names them.
    const more = test.querySelector("details.tl-more > summary")!;
    expect(text(more.querySelector("b"))).toBe("2 more");
    expect(text(more)).toContain("Campaign Test 4");
    expect(text(more)).toContain("Campaign Test 5");
    expect(all(test, "details.tcamp")).toHaveLength(7);
  });

  it("test_an_entry_without_a_bucket_still_lands_somewhere", () => {
    const pub = load("report_v3");
    pub.triage.push({ campaign: "Unsorted One", rating: "red", issues: [] });
    const root = draw(pub);
    expect(root.querySelector('[data-bucket="none"]')).not.toBeNull();
    expect(root.textContent).toContain("Unsorted One");
  });

  it("test_the_row_carries_the_reason_the_excess_and_the_cpa_moves", () => {
    const root = draw(load("report_v3"));
    const delta = summaryOf(campaign(root, "Campaign Delta"));
    // The reading from the summary; the CPA figure is in the chips, the
    // confidence in the opened panel, and the advice belongs to the issue.
    const head = text(delta.querySelector(".head"));
    expect(head).toContain("Lower conversion rate after the click appears to be the largest contributor.");
    for (const gone of ["CPA +22%", "Cut the non-converting ad.", "Medium confidence."]) {
      expect(head, gone).not.toContain(gone);
    }
    expect(text(delta), "the excess cost is missing from the row").toContain("+$310.50");
    // CPA up is worse, by sign alone; the arrow carries the sign.
    const chip = (sum: Element, label: string) =>
      all(sum, ".cc").find((e) => text(e.querySelector(".cl")) === label);
    const d7 = chip(delta, "7d")!;
    expect(d7.className).toBe("cc worse");
    expect(text(d7)).toBe("7d↑22%");
    const d14 = chip(delta, "14d")!;
    expect(d14.className).toBe("cc worse");
    expect(text(d14)).toBe("14d↑15%");
    const golf = summaryOf(campaign(root, "Campaign Golf"));
    const g7 = chip(golf, "7d")!;
    expect(g7.className).toBe("cc better");
    expect(text(g7)).toBe("7d↓12%");
    expect(chip(golf, "14d"), "a missing 14-day comparison drew a chip").toBeUndefined();
    expect(golf.querySelector(".xs"), "no impact, so no excess chip").toBeNull();
  });

  it("test_a_missing_summary_falls_back_to_the_headline", () => {
    const fox = text(summaryOf(campaign(draw(load("report_v3")), "Campaign Foxtrot")));
    expect(fox).toContain("Too few conversions to read a trend.");
    // A null metric is left out, not printed as "--".
    expect(fox).not.toContain("Link CTR");
  });

  it("test_an_act_row_never_carries_its_test_issues_advice", () => {
    // The model's one-line summary can describe the primary issue (a test)
    // while the bucket came from a secondary one (a delivery cut). The row keeps
    // the reading and drops the advice; the opened card leads with the issue
    // that put the campaign in its group.
    const pub = load("report_v3");
    pub.triage = [{
      campaign: "Campaign Kilo", bucket: "act", rating: "yellow",
      summary: "CPA +56% over 7 days, +38% over 14. Lower conversion rate appears "
        + "to be the largest contributor. Worth testing the form. Low confidence.",
      movement: { "7v7": { cpa: 55.97 }, "14v14": { cpa: 38.0 } },
      issues: [
        { priority: "primary", recommendation: { type: "test", action: "Test the lead form." },
          confidence: { level: "low" } },
        { priority: "secondary", recommendation: { type: "act", action: "Stop delivery to Ad K2." },
          confidence: { level: "medium" } }],
    }];
    const card = campaign(draw(pub), "Campaign Kilo");
    const row = text(summaryOf(card));
    expect(row).toContain("Lower conversion rate appears to be the largest contributor.");
    expect(row).not.toContain("Worth testing the form.");
    const doPanel = panel(card, "What to do")!;
    const did = text(doPanel);
    expect(did).toContain("Stop delivery to Ad K2.");
    expect(did).not.toContain("Test the lead form.");
    expect(all(doPanel, ".rtype").map(text)).toContain("ACT");
    expect(did).toContain("Medium confidence");
    expect(did).toContain("1 more issue");
  });

  it.each([
    // The four-sentence round-2 form: only the reading survives.
    ["CPA +22%. Lower CTR appears to be the largest contributor. Test new creative. "
      + "Medium-high confidence.", { "7v7": { cpa: 22 } },
      "Lower CTR appears to be the largest contributor."],
    // Evidence that is not advice stays.
    ["CPA -34.63% over 7 days, -0.89% over 14. Higher conversion rate appears to be the "
      + "largest contributor. $477.30 on non-converting ads. Medium confidence.", { "7v7": {} },
      "Higher conversion rate appears to be the largest contributor. $477.30 on non-converting ads."],
    // With no chips beside it, the CPA sentence is the only place the move is.
    ["CPA is up 4.80% on the prior 7 days. Worth watching.", null,
      "CPA is up 4.80% on the prior 7 days."],
    // Nothing would be left: the whole summary, not an empty row.
    ["CPA +8%. Monitor. Low confidence.", { "7v7": { cpa: 8 } },
      "CPA +8%. Monitor. Low confidence."],
    // A figure inside a sentence is not a sentence boundary.
    ["Not running: no spend in the last 7 settled days. Its 28-day figures are $518.39.",
      { "7v7": {} }, "Not running: no spend in the last 7 settled days. Its 28-day figures are $518.39."],
  ] as [string, Record<string, unknown> | null, string][])(
    "test_reason_line_cuts_and_never_rewrites (%s)", (summary, movement, want) => {
      const c: Record<string, unknown> = { summary };
      if (movement !== null) c.movement = movement;
      expect(reasonLine(c)).toBe(want);
    });

  it("test_reason_line_falls_back_to_the_headline", () => {
    expect(reasonLine({ summary: "", headline: "Too few to read." })).toBe("Too few to read.");
    expect(reasonLine({})).toBe("");
  });

  it("test_lead_issue_is_the_one_the_bucket_came_from", () => {
    const act2 = { priority: "secondary", recommendation: { type: "act", action: "b" } };
    const act1 = { priority: "primary", recommendation: { type: "act", action: "c" } };
    const test = { priority: "primary", recommendation: { type: "test", action: "a" } };
    expect(leadIssue({ bucket: "act", issues: [test, act2] })).toBe(act2);
    expect(leadIssue({ bucket: "act", issues: [test, act2, act1] })).toBe(act1);
    // No issue of the bucket's type: the first issue; none at all: nothing.
    expect(leadIssue({ bucket: "monitor", issues: [test] })).toBe(test);
    expect(leadIssue({ bucket: "no_change", issues: [] })).toEqual({});
  });

  it("test_a_test_card_puts_the_tag_to_copy_beside_the_action", () => {
    // The tag is what ties the new ad back to the recommendation, so a card
    // led by a test shows it where the action is, not only three folds down.
    const root = draw(load("report_v3"));
    const echoDo = panel(campaign(root, "Campaign Echo"), "What to do")!;
    const tag = echoDo.querySelector("p.tp-tag")!;
    expect(tag).not.toBeNull();
    expect(text(tag.querySelector(":scope > span"))).toBe("Tag the new ad");
    expect(text(tag.querySelector(":scope > code"))).toBe("utm_content=EXP-i0920-3-0");
    // Delta is led by its act issue; its test's tag stays in the test plan.
    const delta = campaign(root, "Campaign Delta");
    expect(panel(delta, "What to do")!.querySelector(".tp-tag")).toBeNull();
    expect(text(delta), "the test plan lost its tag").toContain("utm_content=EXP-i1005-0-1");
    // A test with no tag in its plan shows no empty line.
    const pub = load("report_v3");
    for (const c of pub.triage) for (const i of c.issues ?? []) if (isObj(i.test_plan)) delete i.test_plan.tag;
    expect(draw(pub).querySelector(".tp-tag")).toBeNull();
  });

  it("test_the_opened_card_draws_cpa_by_window_and_what_moved", () => {
    const delta = campaign(draw(load("report_v3")), "Campaign Delta");
    const moved = panel(delta, "How CPA moved")!;
    expect(moved.querySelector('[data-chart="windows"]')).not.toBeNull();
    // One dot per rung the ladder has, the unsettled one hollow.
    expect(all(moved, "circle")).toHaveLength(3);
    const mt = text(moved);
    expect(mt).toContain("(provisional)");
    expect(mt).toContain("Last 7");
    expect(mt).toContain("$106.67");
    expect(mt).toContain("$130.00");
    expect(moved.querySelector('[data-chart="paired"]')).not.toBeNull();
    for (const [label, a, b] of [["Link CTR", "−18%", "−10%"], ["Cost per click", "+9%", "+4%"],
                                 ["Conversion rate", "−4%", "+1%"]]) {
      const row = all(moved, ".pm-row").find((r) => text(r.querySelector(".pm-k")) === label);
      expect(row, label).toBeDefined();
      expect(text(row!.querySelector(".pm-v b")), label).toBe(a);
      expect(text(row!.querySelector(".pm-v span")), label).toBe(b);
    }
    const spend = text(panel(delta, "Where the spend went"));
    expect(spend).toContain("Ad D1");
    expect(spend).toContain("49%");
    expect(spend).toContain("$81.82");
  });

  it("test_the_detail_is_one_fold_further_and_in_order", () => {
    const root = draw(load("report_v3"));
    const delta = campaign(root, "Campaign Delta");
    inOrder(all(delta, "summary, h4.tsec").map(text),
            ["The full issues", "The numbers", "What changed", "Where the change came from",
             "Where it's concentrated", "What not to do yet"]);
    expect(root.textContent).not.toContain("What's causing it");
    expect(all(delta, ".chip-d.primary").some((e) =>
      /^Post-click \(CVR\) — appears to be the largest contributor\s+· 79%$/.test(text(e)))).toBe(true);
    expect(all(delta, ".chip-d:not(.primary)").some((e) => /^Click-through \(CTR\)\s+12%$/.test(text(e)))).toBe(true);
  });

  it("test_causal_and_unverified_notes_show", () => {
    const t = draw(load("report_v3")).textContent ?? "";
    expect(t).toContain("Causal wording flagged: due to.");
    expect(t).toContain("Not found in the data:");
    expect(t).toContain("$999.00");
    // The rating is internal in round 2: its note would explain a hidden label.
    expect(t).not.toContain("The review rated this");
  });

  it("test_each_issue_is_its_own_closed_fold", () => {
    const pub = load("report_v3");
    const root = draw(pub);
    const n = pub.triage.reduce((s, c) => s + c.issues.length, 0);
    const issues = all(root, "details.issue");
    expect(issues).toHaveLength(n);
    expect(all(root, "details.issue[open]")).toHaveLength(0);
    const first = summaryOf(issues[0]);
    expect(text(first.querySelector(".plabel"))).toBe("Primary");
    expect(all(first, ".rtype").map(text)).toContain("ACT");
    expect(first.querySelector(".dots")).not.toBeNull();
    expect(text(first)).toContain("Medium confidence");
    expect(text(first)).toContain("Pause Ad D2 and let the budget move to Ad D1.");
    const dts = all(issues[0], "dt").map(text);
    for (const label of ["Observation", "Evidence", "Likely cause (hypothesis)",
                         "Expected outcome", "Confidence reason"]) {
      expect(dts, label).toContain(label);
    }
  });

  it("test_the_outcome_tag_reads_the_ledger", () => {
    const root = draw(load("report_v3"));
    const t = root.textContent ?? "";
    expect(t).toContain("implemented 2026-10-03 (likely) · measuring, 4 settled days");
    expect(t).toContain("implemented 2026-09-22 (strong) · measured: improved");
    expect(all(root, "span.otag").map(text)).toContain("not yet seen in the account");
  });

  it("test_existing_asset_and_new_creative_are_labelled_blocks", () => {
    const root = draw(load("report_v3"));
    const kind = (label: string) => all(root, ".arm.kind").find((e) => text(e.querySelector("b")) === label)!;
    const asset = kind("EXISTING ASSET TO TEST");
    expect(text(asset.querySelector(".cad"))).toBe("Ad D3");
    expect(text(asset)).toContain("Its question-first opener");
    const fresh = kind("NEW CREATIVE NEEDED");
    const dd = (scope: Element, label: string) =>
      text(all(scope, "dt").find((d) => text(d) === label)?.nextElementSibling);
    for (const [k, v] of [["Format", "15-second video"], ["Angle or hook", "problem"],
                          ["Built from", "the problem hook"]]) {
      expect(dd(fresh.querySelector("dl")!, k), k).toMatch(new RegExp("^" + v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }
    expect(text(fresh)).toContain("Still paying for cover you never use?");
    expect(root.textContent).toMatch(/The review proposed an existing asset, but no ad of that name is in this goal's pool;\s+it is shown as new creative needed\./);
  });

  it("test_a_test_plan_says_how_to_set_it_up_and_how_to_tag_the_new_ad", () => {
    const root = draw(load("report_v3"));
    const dds = (scope: Element, label: string) =>
      all(scope, "dt").filter((d) => text(d) === label).map((d) => text(d.nextElementSibling));
    expect(dds(root, "Set it up")).toContain(
      "Turn Ad D3 on in the same ad set as Ad D1 and add the tag below to its URL parameters.");
    expect(dds(root, "Tag the new ad").length).toBeGreaterThan(0);
    const codes = all(root, "code").map(text);
    expect(codes).toContain("utm_content=EXP-i1005-0-1");
    expect(codes).toContain("utm_content=EXP-i0920-3-0");
    // The rows sit inside the plan's own list, and the arms keep their labels.
    const asset = all(root, ".arm.kind").find((e) => text(e.querySelector("b")) === "EXISTING ASSET TO TEST")!
      .closest(".tplan")!;
    expect(dds(asset, "Set it up")).toHaveLength(1);
    expect(text(asset)).toContain("utm_content=EXP-i1005-0-1");
    expect(all(root, ".tplan .arm > b").map(text)).toContain("Control");
  });

  it("test_a_plan_without_setup_or_tag_shows_neither_row", () => {
    const pub = load("report_v3");
    for (const c of pub.triage) for (const i of c.issues ?? []) {
      if (isObj(i.test_plan)) { delete i.test_plan.setup; delete i.test_plan.tag; }
    }
    const dts = all(draw(pub), "dt").map(text);
    expect(dts).not.toContain("Set it up");
    expect(dts).not.toContain("Tag the new ad");
    expect(dts).toContain("Decide when");
  });

  it("test_an_incomplete_issue_says_what_is_missing", () => {
    const root = draw(load("report_v3"));
    expect(root.textContent).toMatch(/Missing: evidence, test plan;\s+shown as monitor\./);
    expect(all(root, "span.itag").map(text)).toContain("incomplete");
  });

  it("test_a_no_change_campaign_says_so", () => {
    expect(text(campaign(draw(load("report_v3")), "Campaign Golf"))).toContain("No issue raised");
  });

  it.each(["old_changes", "old_problems", "report"] as Name[])(
    "test_older_files_never_reach_the_v3_markup (%s)", (name) => {
      const root = draw(load(name));
      expect(root.querySelector("[data-bucket]")).toBeNull();
      expect(root.querySelector(".tlist")).toBeNull();
      expect(root.querySelector(".tpanels")).toBeNull();
      expect(root.querySelector("details.issue")).toBeNull();
      expect(root.textContent).not.toContain("Where the change came from");
      expect(root.querySelector(".otag")).toBeNull();
    });
});

// --- typical for this kind of campaign (contracts-baselines E3) --------------

describe("typical for this kind of campaign", () => {
  it("test_the_numbers_say_what_is_typical_for_the_campaigns_family", () => {
    const delta = campaign(draw(load("report_v3")), "Campaign Delta");
    const numbers = fold(delta, "The numbers")!;
    expect(numbers).toBeDefined();
    const line = text(numbers.querySelector("p.tnote.tfam"));
    expect(line).toContain("Typical for M&A / book roll");
    expect(line).toContain("offsite conversions");
    expect(line).toContain("retargeting audiences only, 14 campaign-weeks");
    expect(line).toContain("CPA $28.10–$36.40 · link CTR 0.90–1.40% · CVR 4.10–6.20% · CPM $18.50–$23.75");
    // Where this week sits is the SQL's position, CPA first.
    expect(line).toMatch(/this week’s CPA of \$106\.67\s+sits above the range \(percentile 93\)\./);
    // A range with no quartiles (cost per click) is left out, not shown as "--".
    expect(line).not.toContain("--");
    expect(line.toLowerCase()).not.toContain("cost per");
    // It sits in "What changed", under the campaign's own windows.
    inOrder(all(numbers, "h4.tsec, p.tfam").map(text),
            ["What changed", "Typical for", "Where the change came from"]);
  });

  it("test_a_family_band_without_a_ladder_still_gets_its_own_heading", () => {
    const echo = campaign(draw(load("report_v3")), "Campaign Echo");
    const numbers = fold(echo, "The numbers")!;
    expect(numbers).toBeDefined();
    expect(all(numbers, "h4.tsec").map(text)).toContain("What changed");
    const t = text(numbers);
    expect(t).toContain("Typical for franchise");
    expect(t).toContain("all audiences, 9 campaign-weeks");
    // CPA has no position this week, so the line places link CTR instead.
    expect(t).toMatch(/this week’s link CTR of 1\.30%\s+sits within the range \(percentile 56\)/);
  });

  it("test_no_band_and_no_family_print_nothing", () => {
    const root = draw(load("report_v3"));
    // Foxtrot's family has no band level, Golf and Hotel have no family:
    // none of them opens a numbers fold for it, and no line is printed.
    expect(all(root, ".tnote.tfam")).toHaveLength(2);
    const fox = campaign(root, "Campaign Foxtrot");
    expect(fold(fox, "The numbers")).toBeUndefined();
    expect(text(fox)).not.toContain("Typical for");
    const pub = load("report_v3");
    for (const c of pub.triage) delete c.family;
    expect(draw(pub).textContent).not.toContain("Typical for");
  });

  it("test_the_shape_two_report_prints_the_typical_line_too", () => {
    const pub = load("report");
    const alpha = pub.triage.find((c) => c.campaign === "Campaign Alpha")!;
    alpha.family = { family: "b_roll", audience: null, goal: "LINK_CLICKS",
                     band_level: "family", campaign_weeks: 10,
                     metrics: { link_ctr: { cur: 2.0, p25: 1.5, p50: 1.8, p75: 2.4,
                                            position: "within", percentile: 60.0 } } };
    const root = draw(pub);
    folded(root);
    expect(all(root, ".tnote.tfam")).toHaveLength(1);
    expect(root.textContent).toContain("Typical for B-roll");
    expect(root.textContent).toContain("link CTR 1.50–2.40%");
  });
});
