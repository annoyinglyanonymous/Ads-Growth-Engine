/* The intent of tests/test_experiment_flow.py's template tests, against the
 * React page: a detected launch renders with the exact statement a person
 * runs; a proposal nobody has launched gets no statement; `intel propose`'s
 * ideas render only while the ledger is empty; a recommended test shows its
 * tag; and the page has no button and no script. Synthetic JSON, no server.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExperimentsPage, confirmSql, type ExperimentsData } from "./ExperimentsPage";
import { truncate } from "../lib/format";

const NAME = "i1 | Camp A | creative: it's new";

const CONFIRM_SQL =
  "update ads.experiment\n" +
  "   set started_on = date '2026-10-03'\n" +
  " where brand_id = (select id from ads.brand where slug = 'renegade')\n" +
  "   and name     = 'i1 | Camp A | creative: it''s new'\n" +
  "   and started_on is null;";

type Row = ExperimentsData["d"]["rows"] extends (infer R)[] | null ? R : never;

function row(over: Partial<Row> = {}): Row {
  return {
    id: "uuid-1", name: NAME, state: "proposed", question: "q", hypothesis: "h",
    primary_metric: "cpa", minimum_effect_pct: 10, minimum_spend_per_arm: 250,
    arms: [{ label: "A control" }, { label: "B challenger" }],
    conclusion: null, concluded_by: null, launch: null, ...over,
  };
}

const LAUNCH = {
  issue_id: "i1", implemented_on: "2026-10-03", match: "strong",
  matched_by: "tagged utm_content=EXP-i1", tag: "EXP-i1",
};

function rec(over: Partial<ExperimentsData["recommended"] extends (infer R)[] | null ? R : never> = {}) {
  return {
    id: "i2", campaign: "Camp A", ad_group: "Group 1", kind: "post_click", variable: "form",
    control: "Control Ad", challenger: "one fewer question", tag: "EXP-i2",
    setup: "Duplicate the ad.", recommended_on: "2026-10-05", status: "not_detectable",
    reason: null, ...over,
  };
}

function data(extra: Partial<ExperimentsData> = {}): ExperimentsData {
  return {
    nav: "experiments", brand: "renegade", settled: "2026-10-07", caveat: null, until: null,
    today: "2026-10-10", days: 14, ideas: null, has_ledger: true,
    d: { brand: "renegade", rows: [], awaiting_launch: 0 },
    recommended: [], unfiled: [], launches: [], ...extra,
  };
}

function serve(json: ExperimentsData) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => json })));
}

function mount() {
  return render(
    <MemoryRouter initialEntries={["/experiments?brand=renegade"]}>
      <ExperimentsPage />
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("ExperimentsPage", () => {
  it("shows the detected launch and the exact confirm SQL, off the ledger", async () => {
    const launched = row({ launch: LAUNCH });
    const older = row({ id: "uuid-2", name: "older", state: "running", minimum_effect_pct: 20,
                        minimum_spend_per_arm: 500, arms: [] });
    serve(data({
      d: { brand: "renegade", rows: [launched, older], awaiting_launch: 1 },
      launches: [launched],
      recommended: [rec(), rec({ id: "i3", tag: null, challenger: "a new video", recommended_on: "2026-09-20" })],
      unfiled: [{ id: "i5", campaign: "Camp A", variable: "creative", implemented_on: "2026-10-03",
                  match: "strong", matched_by: "tagged utm_content=EXP-i5", why: "no decision spend" }],
    }));
    const { container } = mount();

    expect(await screen.findByText("Launch detected 2026-10-03 (strong, tagged utm_content=EXP-i1)")).toBeInTheDocument();
    // The statement, verbatim, name and date filled in, quotes doubled.
    const pres = container.querySelectorAll("pre");
    expect(pres).toHaveLength(1);
    expect(pres[0].textContent).toBe(CONFIRM_SQL);
    expect(screen.getByText(`python -m intel experiment --brand renegade --name "${NAME}"`)).toBeInTheDocument();

    // The log carries the launch line under the state pill.
    const log = screen.getByRole("region", { name: "The experiment log" });
    expect(within(log).getByText("Launch detected 2026-10-03")).toBeInTheDocument();
    expect(within(log).getByText("proposed")).toHaveClass("pill", "quiet");
    expect(within(log).getByText("running")).toHaveClass("pill", "warn");
    expect(within(log).getByText("A control vs B challenger")).toBeInTheDocument();
    expect(within(log).getByText("$250.00")).toBeInTheDocument();

    // The scoreboard: the launch and the never-launched proposal are counted.
    expect(screen.getByText("1 never launched")).toBeInTheDocument();
    expect(screen.getByText("a person confirms started_on")).toBeInTheDocument();

    // Tests the report recommends: the ledger's, not propose's.
    expect(screen.getByRole("region", { name: "Tests the report recommends" })).toBeInTheDocument();
    expect(screen.getByText("utm_content=EXP-i2")).toBeInTheDocument();
    expect(screen.getByText("one fewer question")).toBeInTheDocument();
    expect(screen.getByText("2026-10-05")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "/suggestions" })).toHaveAttribute("href", "/suggestions?brand=renegade");
    for (const a of screen.getAllByRole("link", { name: "the report" })) {
      expect(a).toHaveAttribute("href", "/suggestions?brand=renegade");
    }
    expect(screen.queryByText("intel propose")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Ideas" })).not.toBeInTheDocument();

    expect(screen.getByRole("region", { name: "Launched, not filed yet" })).toBeInTheDocument();
    expect(screen.getByText(/launch detected 2026-10-03 \(strong, tagged utm_content=EXP-i5\)\. no decision spend/)).toBeInTheDocument();

    // Read-only: a person runs the SQL under their own name.
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("gives a proposal nobody has launched no statement, only its state", async () => {
    serve(data({ d: { brand: "renegade", rows: [row()], awaiting_launch: 1 } }));
    const { container } = mount();

    const log = await screen.findByRole("region", { name: "The experiment log" });
    expect(within(log).getByText("proposed")).toHaveClass("pill", "quiet");
    expect(within(log).queryByText(/Launch detected/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Launch detected" })).not.toBeInTheDocument();
    expect(container.querySelector("pre")).toBeNull();
    expect(container.textContent).not.toContain("update ads.experiment");
    expect(screen.getByText("1 never launched")).toBeInTheDocument();
    expect(screen.queryByText("a person confirms started_on")).not.toBeInTheDocument();
    // The log is not empty, so the "nothing to report" row stays away.
    expect(screen.queryByText("Nothing to report, and why")).not.toBeInTheDocument();
    expect(container.querySelector("button")).toBeNull();
  });

  it("falls back to intel propose's ideas only while the ledger is empty", async () => {
    serve(data({
      has_ledger: false,
      ideas: {
        proposal_count: 1,
        proposals: [{
          source: "fatigue", title: "Refresh: Ad One", question: "Does a new execution recover cpa?",
          why: "Link CTR fell 12%.", arms: [{ label: "current" }, { label: "replacement", blocked_on: "x" }],
          would_register: { primary_metric: "cpa" }, fileable: false,
          blocked_on: "the replacement creative, which does not exist yet -- file this once it is live",
          already_tested: true, how_to_file: "python -m intel record --kind experiment --json <path>",
          moved: [{ metric: "link CTR", prior: 1.5, recent: 1.32, pct: -12 },
                  { metric: "CPA", prior: 40, recent: 48, pct: 20 }],
          ad_copy: { first_headline: "Headline one", first_body: "Body one" },
          styles_tried: ["static"], styles_untried: ["ugc", "video"],
          brief: { angle: "ad one", broke: "link CTR -12% (attention)", change: "The opening seconds.",
                   beat: { metric: "cpa", current: 48 }, never_run_as: ["ugc", "video"],
                   written_where: "cd ..\\growth-engine\npython -m engine context \"<campaign>\" --stage meta_ads" },
        }],
        diagnoses: [{
          title: "Ad Two", diagnosis: { stage: "landing", says: "Clicks held up.", change: "The destination." },
          moved: [{ metric: "LP views", prior: 0.8, recent: 0.6, pct: -25 }], ad_copy: null,
          spend_recent: 120, window: "14 day(s) to 2026-10-07",
          not_an_experiment: "The creative is still doing its job.",
        }],
        observations: [{ says: "static carries most of the spend." }],
      },
    }));
    const { container } = mount();

    const ideas = await screen.findByRole("region", { name: "Ideas" });
    expect(within(ideas).getByText("intel propose")).toBeInTheDocument();
    expect(ideas.querySelector(".stage-head .state")?.textContent).toBe("1 · none filed · Source: intel propose");
    expect(screen.queryByRole("region", { name: "Tests the report recommends" })).not.toBeInTheDocument();
    expect(screen.queryByText("Tests the report recommends")).not.toBeInTheDocument();

    // The scoreboard swaps its last tiles for the fallback's.
    expect(screen.getByText("0 ready to file")).toBeInTheDocument();
    expect(screen.getByText("Not a creative problem", { selector: ".stat .k" })).toBeInTheDocument();

    // One row per idea, folded; the status says why it cannot be filed yet.
    expect(within(ideas).getByText("not fileable yet")).toHaveClass("pill", "quiet");
    expect(within(ideas).getByText("waits on the replacement creative, which does not exist yet — file this once it is live")).toBeInTheDocument();
    expect(within(ideas).getByText("tested before")).toHaveClass("pill", "warn");
    expect(within(ideas).getByText("The full card").closest("details")).not.toHaveAttribute("open");
    // Chips carry the verb's own percentage; the arrow is the sign.
    expect(within(ideas).getByText("link CTR ↓12%")).toHaveClass("chip-m");
    expect(within(ideas).getByText("CPA ↑20%")).toHaveClass("chip-m");
    // Merit is a word: a CPA rise and a CTR fall are both "bad".
    expect(within(ideas).getByText("20%", { selector: ".delta" })).toHaveClass("delta", "bad", "up");
    expect(within(ideas).getByText("-12%", { selector: ".delta" })).toHaveClass("delta", "bad", "down");
    expect(within(ideas).getByText("Headline one")).toBeInTheDocument();
    // Once as the styles never run, once in the brief's "never run as" line.
    expect(within(ideas).getByText("ugc, video", { selector: "strong" })).toBeInTheDocument();
    expect(within(ideas).getByText(/^never run as/).closest("div")?.textContent).toBe("never run as ugc, video");
    expect(ideas.querySelector("pre")?.textContent)
      .toBe("cd ..\\growth-engine\npython -m engine context \"<campaign>\" --stage meta_ads");

    const dx = screen.getByRole("region", { name: "Not a creative problem" });
    expect(within(dx).getByText("landing")).toHaveClass("pill", "stop");
    expect(within(dx).getByText("$120.00")).toBeInTheDocument();
    expect(within(dx).getByText("LP views ↓25%")).toHaveClass("chip-m");

    expect(screen.getByText("static carries most of the spend.")).toBeInTheDocument();

    // Empty log: the one-line fold at the bottom, with the by-hand statement.
    expect(screen.getByText("Nothing to report, and why")).toBeInTheDocument();
    expect(screen.getByText("python -m intel record --kind experiment --json <path>", { selector: ".quiet-row code" })).toBeInTheDocument();

    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("shows the tag a recommended test is launched under, or says there is none yet", async () => {
    serve(data({ recommended: [rec(), rec({ id: "i3", tag: null, challenger: "a new video" })] }));
    mount();

    const recs = await screen.findByRole("region", { name: "Tests the report recommends" });
    expect(recs.querySelector(".stage-head .state")?.textContent).toBe("2 not launched · Source: /suggestions");
    expect(within(recs).getByText("utm_content=EXP-i2").tagName).toBe("CODE");
    expect(within(recs).getByText("no tag yet; the next review adds it")).toHaveClass("muted");
    expect(within(recs).getAllByText("form")[0]).toHaveClass("pill", "quiet");
    expect(within(recs).getAllByText("against Control Ad")).toHaveLength(2);
    expect(screen.getByText("not launched yet")).toBeInTheDocument();
  });

  it("says every recommended test has been launched when none is waiting", async () => {
    serve(data());
    mount();
    expect(await screen.findByText(/Every test the report has recommended has been launched/)).toHaveClass("empty");
    expect(screen.getByText("Nothing to report, and why")).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("confirmSql doubles the quotes in the name and the brand", () => {
    expect(confirmSql("renegade", NAME, "2026-10-03")).toBe(CONFIRM_SQL);
    expect(confirmSql("o'brien", "x", "2026-01-02")).toContain("slug = 'o''brien'");
  });

  it("truncate is Jinja's: leeway of five, cut at a word, three dots", () => {
    expect(truncate("a".repeat(95), 90)).toBe("a".repeat(95));
    const long = Array.from({ length: 30 }, (_, i) => `w${i}`).join(" ");
    const got = truncate(long, 90);
    expect(got.endsWith("...")).toBe(true);
    expect(got.length).toBeLessThanOrEqual(90);
    expect(long.startsWith(got.slice(0, -3))).toBe(true);
    expect(got.slice(0, -3).endsWith(" ")).toBe(false);
    expect(truncate("a".repeat(120), 90)).toBe("a".repeat(87) + "...");
  });
});
