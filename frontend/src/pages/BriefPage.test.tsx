/* The brief, rendered against a synthetic document.
 *
 * No real campaign names, no real ad copy. What is asserted is what
 * templates/brief.html promised and tests/test_brief_and_readings.py held the
 * Python side to: an absent figure is "--" and never "$0.00", a quiet section
 * says why it is quiet instead of drawing an empty box, a delta against a
 * barely-imported prior window is withheld with the pill, and the one button
 * left on the page POSTs rather than GETs.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BriefPage } from "./BriefPage";

type Json = Record<string, unknown>;

/** A published 7-day brief for a brand whose import began four days into
 *  the prior window, with one failed section and one unhealthy check. */
function doc(): Json {
  return {
    verb: "brief", brand: "renegade", brand_id: "b", product: null,
    since: "2026-09-28", until: "2026-10-04", days: 7,
    settled_through: "2026-10-04", unsettled_days: 3, caveat: null,
    generated_at: "2026-10-05T01:52:11+00:00", provisional: true,
    summary: { text: "First paragraph of the read.\n\nSecond paragraph.", written_at: "2026-10-05T01:53:00+00:00" },
    facts: {
      trust: {
        healthy: false, problems: ["Synthetic problem one."], settled_through: "2026-10-04",
        credential: { ok: true, confirmed_as: "ads_reader" },
        coverage: { first_day: "2026-09-25", last_day: "2026-10-04", ad_days: 120, ads: 12, spend: 5000 },
        conversion_definitions: 1,
        accounts: [{ platform_account_id: "act_000", label: "Test account", currency: "USD", active: true,
                     insights_through: "2026-10-04", last_structure_ok: "2026-10-04T01:00:00+00:00" }],
        match: { approved_assets: 3, with_tracked_url: 2, matched: 1, match_rate: 0.5,
                 unmatched: [{ asset_name: "Asset A", expected_utm_content: "x-a1" }] },
      },
      spend: {
        spend: 1234.5, conversions: 40, impressions: 90000, link_clicks: 500, currencies: 1,
        rates: { cpa: null, link_ctr: 0.55, cpm: 13.7 }, mixed_currency: false,
        delta: { spend: 100, conversions: -3, cpa: null, link_ctr: 0.05 },
        pct: { spend: 8.8, conversions: -7 },
      },
      movement: {
        cpa_change: 4.2, rate_effect: 3.0, mix_effect: 1.2, unattributable_effect: 0.5,
        attributable_share: 88, attributable_ads: 3, unattributable_ads: 1,
        rows: [
          { ad_key: "k1", entity_name: "Ad One", reason: "attributable", rate_effect: 2, mix_effect: 1, total_effect: 3 },
          { ad_key: "k2", entity_name: "Ad Two", reason: "converted_only_now", rate_effect: null, mix_effect: null, total_effect: 0.5 },
        ],
        structure_changes: { available: false, why: "ads.structure_change is not present (synthetic)." },
      },
      creative: {
        rows: [{
          ad_key: "k1", entity_name: "Ad One", optimization_goal: "LEAD_GENERATION",
          days_observed: 7, spend_recent: 300, impressions_recent: 20000, score: 3,
          link_ctr_decline: true, cpm_rise: true, cpa_rise: true, frequency_rise: false, ranking_drop: false,
          recent: { rates: { cpa: 12.5, link_ctr: 0.4 } }, prior: { rates: { cpa: null, link_ctr: 0.6 } },
        }],
        suppressed_unconfident: 2,
      },
      copy: {
        format: [{ value: "video", ads_run: 5, spend: 800, conversions: 20, rates: { cpa: 40 },
                   optimization_goals: [{ goal: "LEAD_GENERATION", spend: 800 }],
                   comparable_on_cost: true, spend_sufficient: true, rank_within_goal: 1 }],
        hook: [{ value: null, ads_run: 12 }],
        offer: [],
        family: [{ value: null, optimization_goals: [{ goal: "LEAD_GENERATION", spend: 800 }, { goal: "LINK_CLICKS", spend: 200 }] }],
        audience: [],
      },
      angles: { rows: [], never_run: [], under_spent: [], prior_experiments: [] },
      untagged: { total_spend: 1234.5, tagged_spend: null, untagged_spend: 1234.5 },
      creative_labels: { ads: 12, with_a_hook: 10, with_an_offer: 9 },
      experiments: { rows: [], awaiting_launch: 0 },
    },
    waiting_on_you: { angles_unsigned: [], experiments_unlaunched: [], experiments_unconcluded: [],
                      unmapped_handles: [], untagged_spend: 1234.5, where: "http://127.0.0.1:8000/angles" },
    proposals: [],
    degraded: [{ section: "experiments", guard: "RuntimeError: synthetic failure", load_bearing: false }],
    readings: [
      { rule: "stale_import", section: "trust", subject: null, cites: [],
        says: "intel status reports this brand is not healthy, with 1 problem(s) named." },
      { rule: "fatigue_confirmed", section: "creative", cites: [],
        subject: { kind: "ad", key: "k1", name: "Ad One" },
        says: "shows 3 of 5 fatigue symptoms: link ctr decline, cpm rise, cpa rise." },
      { rule: "new_rule", section: "somewhere_new", subject: null, cites: [], says: "An orphan reading." },
    ],
  };
}

/** The /api/brief envelope ui._page builds around the document. */
function page(d: Json, extra: Json = {}): Json {
  return {
    nav: "brief", brand: d.brand, settled: d.settled_through, caveat: null, d,
    until: null, today: "2026-10-09",
    days: d.days, product: null, live: false,
    prior_since: "2026-09-21",
    ...extra,
  };
}

const reply = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

function stub(data: Json, answer: Json = { ok: true, answer: "The model's answer.\n\nAnd its second paragraph." }) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.startsWith("/api/brief")) return reply(data);
    if (url.startsWith("/brief/summary.json")) return reply(answer);
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mount() {
  return render(
    <MemoryRouter initialEntries={["/brief?brand=renegade"]}>
      <BriefPage />
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("the published brief", () => {
  it("renders the headline tiles and the findings band", async () => {
    stub(page(doc()));
    const { container } = mount();
    await screen.findByText("What this window says");

    const tiles = container.querySelector("#window")!;
    // money() drops the cents at or above a thousand, as charts.py does.
    expect(within(tiles as HTMLElement).getByText("Spend").nextElementSibling).toHaveTextContent("$1,234");
    expect(within(tiles as HTMLElement).getByText("Conversions").nextElementSibling).toHaveTextContent("40");
    expect(within(tiles as HTMLElement).getByText("Link CTR").nextElementSibling).toHaveTextContent("0.55%");
    expect(within(tiles as HTMLElement).getByText("Settled through").nextElementSibling).toHaveTextContent("2026-10-04");
    expect(tiles).toHaveTextContent("3 unsettled day(s) in this window — Meta restates for about three");
    expect(tiles).toHaveTextContent("the copy is labelled: offer on 9 of 12 ads, hook on 10");
    // Percentage POINTS, not pct().
    expect(tiles).toHaveTextContent("0.05 pp vs prior · link clicks, not all clicks");

    // Readings are the rules' sentences, verbatim, ranked, with the subject
    // in bold and a link to the evidence.
    const findings = container.querySelectorAll("#findings .finding");
    expect(findings).toHaveLength(2);
    expect(findings[0]).toHaveTextContent("intel status reports this brand is not healthy, with 1 problem(s) named.");
    expect(findings[1].querySelector(".rank")).toHaveTextContent("2");
    expect(findings[1].querySelector(".says strong")).toHaveTextContent("Ad One");
    expect(findings[1]).toHaveTextContent("shows 3 of 5 fatigue symptoms: link ctr decline, cpm rise, cpa rise.");
    expect(findings[1].querySelector("a.go")).toHaveAttribute("href", "#tiring");
    expect(screen.getByText("readings of the figures below, not decisions about them")).toBeInTheDocument();
  });

  it("tints exactly one finding row, the trust one, and only when the checks failed", async () => {
    stub(page(doc()));
    const { container } = mount();
    await screen.findByText("What this window says");
    const flagged = container.querySelectorAll(".finding.flag");
    expect(flagged).toHaveLength(1);
    expect(flagged[0]).toHaveTextContent("intel status reports this brand is not healthy");
    // The trust fold is open because the checks failed, and says how many.
    const details = container.querySelector("#trust details")!;
    expect(details).toHaveAttribute("open");
    expect(details).toHaveTextContent("1 problem(s)");
    expect(details).toHaveTextContent("Synthetic problem one.");
  });

  it("does not tint anything when the checks passed", async () => {
    const d = doc();
    (d.facts as Json).trust = { ...((d.facts as Json).trust as Json), healthy: true, problems: [] };
    stub(page(d));
    const { container } = mount();
    await screen.findByText("What this window says");
    expect(container.querySelectorAll(".finding.flag")).toHaveLength(0);
    expect(container.querySelector("#trust details")).not.toHaveAttribute("open");
    expect(screen.getByText("no problem reported")).toBeInTheDocument();
  });

  it("says why a quiet section is quiet instead of drawing an empty box", async () => {
    stub(page(doc()));
    const { container } = mount();
    await screen.findByText("Nothing to report, and why");
    const rows = container.querySelectorAll(".quiet-row");
    const texts = [...rows].map((r) => r.textContent ?? "");
    // The section that failed to load says so, with the guard verbatim.
    const exps = rows[[...rows].findIndex((r) => r.id === "experiments")];
    expect(exps).toHaveTextContent("Tests in flight");
    expect(exps.querySelector(".pill")).toHaveTextContent("could not be read");
    expect(exps.querySelector(".pill")).toHaveClass("warn");
    expect(exps).toHaveTextContent("RuntimeError: synthetic failure");
    // The ones that loaded and found nothing say that, and why.
    expect(texts.some((t) => t.includes("Which angles earned their place") && t.includes("nothing to show")
      && t.includes("The angle bank has no members"))).toBe(true);
    expect(texts.some((t) => t.includes("Waiting on you") && t.includes("clear")
      && t.includes("No angle awaits a signature"))).toBe(true);
    expect(texts.some((t) => t.includes("What we've never said")
      && t.includes("becomes a finding the day the first angle is signed"))).toBe(true);
    // And none of those sections drew its full body.
    expect(container.querySelector("section#angles")).toBeNull();
    expect(container.querySelector("section#experiments")).toBeNull();
    expect(container.querySelector("section#waiting")).toBeNull();
    // The change log's absence is an absence of evidence, printed verbatim.
    expect(screen.getByText(/ads\.structure_change is not present \(synthetic\)\./)).toBeInTheDocument();
    expect(screen.getByText(/This is not the same as/)).toBeInTheDocument();
  });

  it("renders an absent figure as a dash, never as $0.00", async () => {
    stub(page(doc()));
    const { container } = mount();
    await screen.findByText("What this window says");
    const tiles = container.querySelector("#window") as HTMLElement;
    expect(within(tiles).getByText("CPA").nextElementSibling).toHaveTextContent("--");
    expect(tiles).toHaveTextContent("no prior CPA — nothing converted in one of the two windows");
    // A fatigue row whose prior window had no conversions carries no CPA.
    const tiring = container.querySelector("#tiring tbody tr")!;
    const cells = tiring.querySelectorAll("td");
    expect(cells[3]).toHaveTextContent("--");
    expect(cells[4]).toHaveTextContent("$12.50");
    // A mover with no split says so in each column rather than printing zero.
    const two = screen.getByText("Ad Two").closest("tr")!;
    expect(two.querySelectorAll("td")[1]).toHaveTextContent("--");
    expect(two).toHaveTextContent("converted only now");
    expect(container.textContent).not.toContain("$0.00");
  });

  it("withholds spend and conversions deltas when the prior window is incomplete", async () => {
    stub(page(doc()));
    const { container } = mount();
    await screen.findByText("What this window says");
    const pills = screen.getAllByText("prior window incomplete");
    expect(pills).toHaveLength(2);
    const tiles = container.querySelector("#window") as HTMLElement;
    const spendTile = within(tiles).getByText("Spend").closest(".stat")!;
    const convTile = within(tiles).getByText("Conversions").closest(".stat")!;
    expect(spendTile).toHaveTextContent("prior window incomplete");
    expect(convTile).toHaveTextContent("prior window incomplete");
    // The withheld figures are not on the page at all.
    expect(spendTile).not.toHaveTextContent("vs prior window");
    expect(convTile).not.toHaveTextContent("vs prior window");
  });

  it("shows the deltas when the prior window is covered", async () => {
    const d = doc();
    // The import began a month before the prior window started.
    ((d.facts as Json).trust as Json).coverage = { first_day: "2026-09-01", last_day: "2026-10-04", ad_days: 1, ads: 1, spend: 1 };
    stub(page(d, { prior_since: "2026-09-21" }));
    const { container } = mount();
    await screen.findByText("What this window says");
    expect(screen.queryByText("prior window incomplete")).toBeNull();
    const tiles = container.querySelector("#window") as HTMLElement;
    const spendDelta = within(tiles).getByText("Spend").closest(".stat")!.querySelector(".delta")!;
    expect(spendDelta).toHaveTextContent("$100.00 (8.80%) vs prior window");
    expect(spendDelta).toHaveClass("up");
    expect(spendDelta).not.toHaveClass("good");
    expect(spendDelta).not.toHaveClass("bad");
    const convDelta = within(tiles).getByText("Conversions").closest(".stat")!.querySelector(".delta")!;
    expect(convDelta).toHaveTextContent("-3 (-7.00%) vs prior window");
    expect(convDelta).toHaveClass("down");
    expect(convDelta).toHaveClass("bad");
  });

  it("carries the published summary as paragraphs and names the window it was written against", async () => {
    stub(page(doc()));
    const { container } = mount();
    await screen.findByText("What this window says");
    const read = container.querySelector("#brief-summary")!;
    expect(read.querySelector(".pill")).toHaveTextContent("AI read");
    expect(read.querySelector(".state")).toHaveTextContent(
      "written 2026-10-05 · from the 7 days to 2026-10-04 · 3 unsettled day(s)");
    const paras = read.querySelectorAll("p");
    expect(paras).toHaveLength(2);
    expect(paras[0]).toHaveTextContent("First paragraph of the read.");
    expect(paras[1]).toHaveTextContent("Second paragraph.");
    // Published: the chips date it, and there is no button.
    expect(screen.getByText("week to 2026-10-04")).toBeInTheDocument();
    expect(screen.getByText("published 2026-10-05")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Explain/ })).toBeNull();
  });

  it("renders the copy section with its goal caveat and names the absent dimensions", async () => {
    stub(page(doc()));
    mount();
    await screen.findByText("What this window says");
    expect(screen.getByText("Cost is not comparable across these rows.")).toBeInTheDocument();
    expect(screen.getByText("By format")).toBeInTheDocument();
    expect(screen.getByText("Hook, offer, audience, family are absent rather than empty.")).toBeInTheDocument();
    // An enum reads as words.
    expect(screen.getAllByText("Lead generation").length).toBeGreaterThan(0);
  });

  it("prints a reading no band claims rather than dropping it", async () => {
    stub(page(doc()));
    mount();
    await screen.findByText("Readings this page could not place");
    expect(screen.getByText("An orphan reading.")).toBeInTheDocument();
  });
});

describe("a brief with no summary", () => {
  it("offers the Explain button on a live window and POSTs /brief/summary.json", async () => {
    const d = doc();
    d.summary = null;
    d.days = 14;
    const fetchMock = stub(page(d, { days: 14, live: true }));
    const { container } = mount();
    await screen.findByText("What this window says");

    expect(screen.getByText("live · 14 days to 2026-10-04")).toBeInTheDocument();
    expect(screen.queryByText(/^published/)).toBeNull();
    const read = container.querySelector("#brief-summary")!;
    expect(read.querySelector("[data-analysis-state]")).toHaveTextContent("no summary was published for this window");
    expect(read).toHaveTextContent("The findings below stand on their own.");
    expect(read.querySelector("[data-analysis-foot]")).toHaveAttribute("hidden");

    const btn = screen.getByRole("button", { name: "Explain this brief" });
    fireEvent.click(btn);
    expect(fetchMock).toHaveBeenCalledWith(
      "/brief/summary.json?brand=renegade&days=14",
      expect.objectContaining({ method: "POST" }));

    await screen.findByText("The model's answer.");
    expect(screen.getByText("And its second paragraph.")).toBeInTheDocument();
    expect(read.querySelector("[data-analysis-foot]")).not.toHaveAttribute("hidden");
    expect(read).toHaveTextContent("Written by a Claude Code session from the numbers below. It reports; it decides nothing.");
    expect(screen.getByRole("button", { name: "Explain again" })).toBeInTheDocument();
  });

  it("says 'week' on a published brief whose model call failed", async () => {
    const d = doc();
    d.summary = null;
    stub(page(d));
    mount();
    await screen.findByText("What this window says");
    expect(screen.getByText("no summary was published for this week")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Explain this brief" })).toBeInTheDocument();
  });

  it("reports a failed session in the slot and does not pretend", async () => {
    const d = doc();
    d.summary = null;
    stub(page(d), { ok: false, error: "The session returned nothing." });
    const { container } = mount();
    await screen.findByText("What this window says");
    fireEvent.click(screen.getByRole("button", { name: "Explain this brief" }));
    await screen.findByText("The session returned nothing.");
    const read = container.querySelector("#brief-summary")!;
    expect(read.querySelector("[data-analysis-state]")).toHaveTextContent("failed");
    expect(read.querySelector("[data-analysis-foot]")).toHaveAttribute("hidden");
  });
});

describe("mixed currency", () => {
  it("withholds every money figure rather than adding two currencies", async () => {
    const d = doc();
    ((d.facts as Json).spend as Json).mixed_currency = true;
    stub(page(d));
    const { container } = mount();
    await screen.findByText("What this window says");
    const tiles = container.querySelector("#window") as HTMLElement;
    expect(within(tiles).getByText("Spend").nextElementSibling).toHaveTextContent("withheld");
    expect(within(tiles).getByText("Spend with no angle").nextElementSibling).toHaveTextContent("withheld");
    expect(tiles).not.toHaveTextContent("$1,234");
  });
});
