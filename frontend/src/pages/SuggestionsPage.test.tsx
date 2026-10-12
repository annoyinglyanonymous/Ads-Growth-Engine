/* The page as a reader meets it: fetched JSON in, markup out. The Triage
 * component is mocked -- it is another port's work -- and stands in as the
 * `tlist` section the template's include produced, so the ordering test
 * holds the same thing tests/test_triage_render.py holds. */
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import report from "../../../tests/fixtures/suggestions/report_v3.json";
import { SuggestionsPage } from "./SuggestionsPage";

vi.mock("../components/Triage", () => ({
  Triage: ({ published }: { published: Record<string, unknown> }) => (
    <section className="tlist" data-testid="triage">
      {String((published.triage as unknown[]).length)} cards
    </section>
  ),
}));

const copy = (ad: string, headline: string, body: string | null, cpa: number | null, spend: number, conv: number) =>
  ({ ad, headline, body, cpa, spend, conversions: conv });

const goalRow = (value: string | null, spend: number, conv: number, cpa: number | null, rel: string | null,
                 pctv: number | null, ev: string) =>
  ({ optimization_goal: "OFFSITE_CONVERSIONS", value, ads_run: 2, spend, conversions: conv,
     rates: { cpa }, relative: rel, relative_pct: pctv, evidence_level: ev });

/** The live pack, synthetic. Two goals: one rankable, one that never converted. */
function pack(until = "2026-10-08") {
  return {
    verb: "creative_pack",
    brand: "renegade", days: 28, since: "2026-09-11", until, settled_through: "2026-10-05",
    goals: [
      { optimization_goal: "OFFSITE_CONVERSIONS", ads_run: 4, rankable_on_cost: true, ads_with_no_conversions: 1,
        cheapest: [copy("Ad Alpha", "Cover that fits the book", "Line one.\nLine two of a body that runs on past the limit the card quotes so the cut lands on a word boundary.", 31.5, 315, 10)],
        dearest: [copy("Ad Bravo", "The dear one", null, 120, 240, 2)],
        by_spend_only: [] },
      { optimization_goal: "LINK_CLICKS", ads_run: 2, rankable_on_cost: false, ads_with_no_conversions: 2,
        cheapest: [], dearest: [],
        by_spend_only: [copy("Ad Charlie", "Clicky", "Body C", null, 80, 0)] },
    ],
    dimensions: {
      hook: [{ value: "mixed-goal hook", ads_run: 2, spend: 400, conversions: 12 },
             { value: null, ads_run: 1, spend: 5, conversions: 0 }],
      offer: [], audience: [], format: [],
    },
    dimensions_by_goal: {
      OFFSITE_CONVERSIONS: {
        hook: [goalRow("problem", 900, 12, 75, "baseline", -6.25, "directional"),
               goalRow("question", 300, 2, 150, "worse", 87.5, "insufficient"),
               goalRow(null, 20, 0, null, null, null, "insufficient")],
        offer: [goalRow("free quote", 800, 60, 13.33, "better", -83.34, "strong")],
        audience: [],
      },
    },
    tiring: [{ ad: "Ad Alpha", score: 2 }, { ad: "Ad Bravo", score: 1 }],
  };
}

function payload(published: Record<string, unknown> | null, until?: string) {
  const d = pack(until);
  return { nav: "suggestions", brand: d.brand, settled: d.settled_through, caveat: null, d,
           until: null, today: "2026-10-09", days: d.days, published };
}

function published(over: Record<string, unknown> = {}) {
  return { ...(report as Record<string, unknown>), age_hours: 26.4, ...over };
}

function mount(body: unknown, url = "/suggestions?brand=renegade") {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => body })));
  return render(<MemoryRouter initialEntries={[url]}><SuggestionsPage /></MemoryRouter>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("the AI read", () => {
  it("has no button and no script, published or not", async () => {
    let { container } = mount(payload(null));
    await screen.findByText(/No suggestion has been published/);
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    cleanup();
    ({ container } = mount(payload(published())));
    await screen.findByTestId("triage");
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("says so when nothing has been published, and names the command", async () => {
    const { container } = mount(payload(null));
    const p = await screen.findByText(/No suggestion has been published/);
    expect(p.textContent).toContain("No suggestion has been published for this brand yet.");
    expect(p.textContent).toContain("python scripts/sync.py");
    expect(p.textContent).toContain("python scripts/suggest.py --brand renegade");
    expect(p.textContent).toContain("The evidence below is live either way.");
    expect(container.querySelector(".ai-read-head .state")?.textContent).toBe("nothing published yet");
    expect(container.querySelector(".ai-read")?.getAttribute("data-shape")).toBeNull();
    expect(container.querySelector(".caveat")).toBeNull();
    expect(screen.queryByTestId("triage")).toBeNull();
  });

  it("puts what matters today and the scorecard above the groups", async () => {
    const { container } = mount(payload(published(), "2026-10-04"));
    await screen.findByTestId("triage");
    const html = container.innerHTML;
    const today = html.indexOf('aria-label="What matters today"');
    const followed = html.indexOf('aria-label="Recommendations followed up"');
    const groups = html.indexOf('class="tlist"');
    expect(today).toBeGreaterThan(-1);
    expect(today).toBeLessThan(followed);
    expect(followed).toBeLessThan(groups);

    const panel = container.querySelector('[aria-label="What matters today"]')!;
    // A tile per goal: the move, with the arrow carrying the sign.
    expect(panel.querySelectorAll(".today-goal")).toHaveLength(2);
    expect(panel.textContent).toContain("Offsite conversions");
    expect(panel.textContent).toContain("↑ 18.20%");
    expect(panel.textContent).toContain("Link clicks");
    expect(panel.textContent).toContain("↓ 6.10%");
    expect(panel.textContent).toContain("Moderate evidence");
    // The campaigns that account for the rise, each with its share, and the
    // last one's cumulative share as the total -- read, never added up.
    expect(panel.textContent).toContain("Campaign Delta");
    expect(panel.textContent).toContain("41.5%");
    expect(panel.textContent).toContain("26.9%");
    expect(panel.textContent).toContain("Together, 68.4% of the rise");
    // The goal's own sentence restates the tile and is not printed.
    expect(panel.textContent).not.toContain("on the prior 7 days; 2 campaigns account");
    // The computed lines, with diagnose's identifiers as words.
    expect(panel.textContent).toContain("2 have issues with low current impact.");
    expect(panel.textContent).toContain("(creative engagement)");
    expect(panel.textContent).not.toContain("creative_engagement");
    expect(panel.textContent).toContain("The review’s read");
    expect(panel.textContent).toContain("Most of the rise sits in two conversion campaigns");

    const card = container.querySelector('[aria-label="Recommendations followed up"]')!;
    expect(card.innerHTML).toMatch(/<b>6<\/b>recommended/);
    expect(card.innerHTML).toMatch(/<b>3<\/b>carried out/);
    expect(card.innerHTML).toMatch(/<b>1<\/b>improved/);
    expect(card.innerHTML).toMatch(/<b>2<\/b>too early to read/);
    expect(card.textContent).toContain("a reading, not a conclusion");
    expect(card.innerHTML).toContain("<td>Creative test</td>");
    expect(card.innerHTML).toContain("<td>Delivery cut</td>");
    expect(card.textContent).toContain("Campaign Echo");
    expect(card.innerHTML).toContain("improved</b> after 9 settled days");
    expect(card.textContent).toContain("(strong)");

    // The bucket counts jump to their groups.
    const counts = container.querySelector('[aria-label="Campaigns by action"]')!;
    for (const [key, n] of [["act", "2"], ["test", "1"], ["monitor", "1"], ["no_change", "1"]]) {
      const a = counts.querySelector(`a[href="#${key}"]`)!;
      expect(a).not.toBeNull();
      expect(a.querySelector("b")!.textContent).toBe(n);
    }
    const read = container.querySelector(".ai-read")!;
    expect(read.getAttribute("data-shape")).toBe("triage");
    expect(read.querySelector("details[open]")).toBeNull();
    expect(read.querySelector(".ai-read-head .state")!.textContent)
      .toBe("written 2026-10-05, 26h ago, on the window to 2026-10-04");
    // The whole published object reaches the triage.
    expect(screen.getByTestId("triage").textContent).toBe("5 cards");
  });

  it("says which half moved when an import has landed since", async () => {
    const { container } = mount(payload(published(), "2026-10-08"));
    await screen.findByTestId("triage");
    const caveat = container.querySelector(".ai-read .caveat")!;
    expect(caveat).not.toBeNull();
    expect(caveat.textContent).toContain("Written against the window ending 2026-10-04");
    expect(caveat.textContent).toContain("the evidence below now runs to 2026-10-08");
    expect(caveat.textContent).toContain("The next import republishes it.");
  });

  it("stays quiet when the evidence runs to the window it was written against", async () => {
    const { container } = mount(payload(published(), "2026-10-04"));
    await screen.findByTestId("triage");
    expect(container.querySelector(".ai-read .caveat")).toBeNull();
  });

  it("renders an older prose file as paragraphs, under the same stamp", async () => {
    const { container } = mount(payload({ published_at: "2026-09-01T08:00:00", until: "2026-08-31",
                                          age_hours: 900, suggestion: "First paragraph.\n\n\n\nSecond one." }));
    await screen.findByText("First paragraph.");
    const read = container.querySelector(".ai-read")!;
    expect(read.querySelectorAll(":scope > p")).toHaveLength(2);
    expect(read.getAttribute("data-shape")).toBeNull();
    expect(screen.queryByTestId("triage")).toBeNull();
    expect(read.querySelector(".state")!.textContent).toBe("written 2026-09-01, 37 days ago, on the window to 2026-08-31");
    expect(read.querySelector(".caveat")).not.toBeNull();
  });
});

describe("the wording it quotes", () => {
  it("is folded until a goal is asked for, then opens on that goal", async () => {
    let { container } = mount(payload(null));
    await screen.findByText(/No suggestion has been published/);
    let fold = container.querySelector("details.evidence-fold")!;
    expect(fold.hasAttribute("open")).toBe(false);
    expect(fold.querySelector("summary")!.textContent).toContain("2 goals");
    let tabs = container.querySelectorAll('nav[aria-label="Optimization goal"] a');
    expect(tabs).toHaveLength(2);
    expect(tabs[0].getAttribute("aria-current")).toBe("page");
    expect(tabs[0].textContent).toBe("Offsite conversions4");
    expect(tabs[1].getAttribute("href")).toBe("/suggestions?brand=renegade&goal=LINK_CLICKS#evidence");
    // The rankable goal: cheapest and dearest side by side, the quote cut at a word.
    expect(container.querySelectorAll(".boards .board")).toHaveLength(2);
    expect(container.textContent).toContain("Ad Alpha");
    expect(container.textContent).toContain("“Line one. Line two of a body that runs on past the limit the card quotes so the cut lands on a word boundary.”");
    expect(container.textContent).toContain("1 ad(s) under offsite conversions spent without converting and are not ranked.");
    cleanup();

    ({ container } = mount(payload(null), "/suggestions?brand=renegade&goal=LINK_CLICKS"));
    await screen.findByText(/No suggestion has been published/);
    fold = container.querySelector("details.evidence-fold")!;
    expect(fold.hasAttribute("open")).toBe(true);
    tabs = container.querySelectorAll('nav[aria-label="Optimization goal"] a');
    expect(tabs[0].getAttribute("aria-current")).toBeNull();
    expect(tabs[1].getAttribute("aria-current")).toBe("page");
    // Nothing converted under it, so no cost ranking: by spend only.
    expect(container.querySelector('[aria-label="By spend"] h2')!.textContent)
      .toBe("By spend — nothing converted under link clicks, so there is no cost ranking");
    expect(container.textContent).toContain("Ad Charlie");
    expect(container.textContent).not.toContain("Ad Alpha");
  });

  it("reads the labels under the open goal, and falls back to spend by label without them", async () => {
    let { container } = mount(payload(null));
    await screen.findByText(/No suggestion has been published/);
    const byGoal = container.querySelector(".goal-dims")!;
    expect(byGoal).not.toBeNull();
    expect(byGoal.textContent).toContain("ads.facet_by_goal");
    const hook = byGoal.querySelector('[aria-label="Hook it opens with"]')!;
    expect(hook.textContent).toContain("problem");
    expect(hook.textContent).toContain("$900.00");
    expect(hook.innerHTML).toContain('class="rel worse"');
    expect(hook.textContent).toContain("+87.5%");
    expect(hook.textContent).toContain("Not tagged");
    // Few conversions are marked rather than hidden: directional, and both insufficient rows.
    expect(hook.querySelectorAll(".ev")).toHaveLength(3);
    const untagged = hook.querySelectorAll("tbody tr")[2].querySelectorAll("td");
    expect(untagged[0].textContent).toBe("Not tagged");
    expect(untagged[3].textContent).toBe("--");
    expect(untagged[4].querySelector(".muted")!.textContent).toBe("--");
    expect(byGoal.querySelector('[aria-label="Who it addresses"]')!.textContent)
      .toContain("No ad under this goal carries this label yet.");
    // The mixed-goal panels stay away while the goal has its own tables.
    expect(container.querySelector('section[aria-label="What the writing is doing"]')).toBeNull();
    expect(container.textContent).not.toContain("ads.facet_performance");
    cleanup();

    ({ container } = mount(payload(null), "/suggestions?brand=renegade&goal=LINK_CLICKS"));
    await screen.findByText(/No suggestion has been published/);
    expect(container.querySelector(".goal-dims")).toBeNull();
    const mixed = container.querySelector('section[aria-label="What the writing is doing"]')!;
    expect(mixed).not.toBeNull();
    expect(mixed.textContent).toContain("mixed goals, so no cost ranking");
    expect(mixed.textContent).toContain("ads.facet_performance");
    expect(mixed.querySelectorAll(".dim-row")).toHaveLength(1);
    expect(mixed.textContent).toContain("mixed-goal hook");
    expect(mixed.textContent).toContain("$400.00");
    expect(mixed.querySelector('[aria-label="Offer it asks for"]')!.textContent).toContain("No ad carries this label yet.");
  });

  it("folds the typical ranges under the evidence and marks a thin range", async () => {
    const { container } = mount(payload(published(), "2026-10-04"));
    await screen.findByTestId("triage");
    const bands = container.querySelector("details.family-bands")!;
    expect(bands).not.toBeNull();
    expect(bands.hasAttribute("open")).toBe(false);
    expect(bands.textContent).toContain("2026-03-29 – 2026-09-24");
    const rows = bands.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("M&A / book roll");
    expect(rows[0].textContent).toContain("$30.25–$38.90");
    expect(rows[0].textContent).toContain("0.95–1.38%");
    expect(rows[0].querySelectorAll("td")[6].textContent).toBe("21");
    expect(rows[1].textContent).toContain("Hiring");
    expect(rows[1].querySelectorAll("td")[2].textContent).toBe("--");
  });

  it("counts the ads wearing out and points at Fatigue", async () => {
    const { container } = mount(payload(null));
    await screen.findByText(/No suggestion has been published/);
    expect(container.textContent).toContain("1 ad(s) show two or more fatigue symptoms and need new copy first.");
    expect(container.querySelector('a[href="/creative?brand=renegade"]')!.textContent).toBe("Open Fatigue →");
  });
});
