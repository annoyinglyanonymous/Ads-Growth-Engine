import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdPage, type AdData } from "./AdPage";
import { truncate } from "../lib/format";

// Synthetic JSON shaped like ui.ad_detail's output: `d` is {brand}, `ad` is
// metrics.ad, and the extras are days, settled, reading, totals and the two
// series.
const BASE = { nav: "overview", brand: "renegade", settled: "2026-10-05", caveat: null, until: null, today: "2026-10-09" };

function full(over: Partial<AdData> = {}): AdData {
  return {
    ...BASE,
    d: { brand: "renegade" },
    ad: {
      ad: { ad_key: "k1", name: "Ad K1", effective_status: "ACTIVE", optimization_goal: "OFFSITE_CONVERSIONS",
            format: "SINGLE_IMAGE", cta: "LEARN_MORE", link_url: "https://example.com/landing", utm_content: null,
            thumbnail_url: null, campaign_name: "Campaign C", ad_group_name: "Group G" },
      copy: { texts: [{ field: "headline", ordinal: 0, text: "Headline text" }, { field: "body", ordinal: 1, text: "Body text" }] },
      tag_is_stale: true,
      daily: [
        { day: "2026-10-01", spend: 10, impressions: 1000, link_clicks: 20, conversions: 1, frequency_this_day: 1.1, reach_this_day: 900 },
        { day: "2026-10-02", spend: 12, impressions: 1100, link_clicks: 22, conversions: 0, frequency_this_day: 1.15, reach_this_day: 950 },
        { day: "2026-10-03", spend: 8, impressions: 800, link_clicks: 15, conversions: 2, frequency_this_day: null, reach_this_day: null },
      ],
      day_count: 3,
    },
    days: 14,
    reading: {
      facts: { fatigue: { score: 2, symptoms: ["link_ctr_decline", "cpa_rise"], confident: false } },
      readings: [{ says: "Two symptoms fired." }, { says: "Spend was below the floor." }],
    },
    totals: { spend: 123.45, conversions: 0, rates: { cpa: null, link_ctr: 1.234 } },
    spend_series: [["2026-10-01", 10], ["2026-10-02", 12], ["2026-10-03", 8]],
    conv_series: [["2026-10-01", 1], ["2026-10-02", 0], ["2026-10-03", 2]],
    ...over,
  };
}

function bare(): AdData {
  const d = full({ reading: null, totals: null, spend_series: [], conv_series: [] });
  d.ad = { ...d.ad, copy: null, tag_is_stale: false, daily: [], day_count: 0 };
  d.ad.ad = { ...d.ad.ad, effective_status: "PAUSED", cta: null, link_url: "https://example.com/" + "a".repeat(70) };
  return d;
}

function mockFetch(page: unknown, reply: unknown = { ok: true, answer: "First paragraph.\n\nSecond paragraph." }) {
  const fn = vi.fn(async (_url: string, init?: RequestInit) => ({
    ok: true, status: 200, json: async () => (init?.method === "POST" ? reply : page),
  }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

function mount(path = "/ad/k1?brand=renegade") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/ad/:adKey" element={<AdPage />} /></Routes>
    </MemoryRouter>,
  );
}

const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
const tile = (k: string) => screen.getByText(k).closest(".stat")!;

afterEach(() => vi.unstubAllGlobals());

describe("one ad's page", () => {
  it("asks /api/ad/<key> and names the ad in the topbar", async () => {
    const fetch = mockFetch(full());
    mount();
    expect(await screen.findByRole("heading", { level: 1, name: "Ad K1" })).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/ad/k1?brand=renegade", expect.anything());
    expect(text(document.querySelector(".topbar .sub"))).toBe("Campaign C › Group G");
    const status = screen.getByText("Active");
    expect(status).toHaveClass("pill", "ok");
    expect(screen.getByText("Offsite conversions")).toHaveClass("chip");
    expect(screen.getByText("Single image")).toHaveClass("chip");
    expect(screen.getByText(/Stale tag\./)).toBeInTheDocument();
  });

  it("draws the ad's own window as tiles and never an absent CPA as money", async () => {
    mockFetch(full());
    mount();
    await screen.findByRole("heading", { level: 1, name: "Ad K1" });
    expect(text(tile("Spend, 14d").querySelector(".v"))).toBe("$123.45");
    expect(text(tile("Conversions").querySelector(".v"))).toBe("0");
    expect(text(tile("CPA").querySelector(".v"))).toBe("--");
    expect(text(tile("Link CTR").querySelector(".v"))).toBe("1.23%");
    const fat = tile("Fatigue");
    expect(fat.querySelector(".sym")).toHaveAttribute("aria-label", "link ctr decline, cpa rise");
    expect(fat.querySelectorAll(".sym span.on")).toHaveLength(2);
    expect(text(fat.querySelector(".sub"))).toBe("2 of 5 symptoms · too little spend to read");
  });

  it("leads with what the rules say, then the copy", async () => {
    mockFetch(full());
    mount();
    const rules = await screen.findByLabelText("What the rules say");
    expect(rules).toHaveTextContent("What the rules say about it");
    expect(rules).toHaveTextContent("readings of the figures on this page, not decisions about them");
    expect(rules.querySelectorAll(".finding")).toHaveLength(2);
    const second = rules.querySelectorAll(".finding")[1];
    expect(text(second.querySelector(".rank"))).toBe("2");
    expect(text(second.querySelector(".says"))).toBe("Spend was below the floor.");

    const copy = screen.getByLabelText("The copy");
    expect(copy).toHaveTextContent("2 variant(s)");
    const eyebrows = [...copy.querySelectorAll(".eyebrow")].map(text);
    expect(eyebrows).toEqual(["headline", "body 1"]);
    expect(copy).toHaveTextContent("Headline text");
    const foot = copy.querySelector("p.small:not(.muted)")!;
    expect(text(foot.querySelector(".pill"))).toBe("Learn more");
    expect([...foot.querySelectorAll(".mono")].map(text)).toEqual(["no utm_content", "https://example.com/landing"]);
  });

  it("has the one button, which POSTs to the analyse endpoint and lands the answer beside the copy", async () => {
    const fetch = mockFetch(full());
    mount();
    await screen.findByRole("heading", { level: 1, name: "Ad K1" });
    expect(document.getElementById("ad-analysis")).toBeNull();
    const button = screen.getByRole("button", { name: "Ask about this ad" });
    fireEvent.click(button);
    expect(await screen.findByText("First paragraph.")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/ad/k1/analyse.json?brand=renegade&days=14",
      expect.objectContaining({ method: "POST" }));
    const panel = document.getElementById("ad-analysis")!;
    expect(within(panel).getByText("AI read")).toBeInTheDocument();
    expect(panel).toHaveTextContent("Second paragraph.");
    expect(panel).toHaveTextContent("Written by a Claude Code session from this ad's own numbers. It reports; it decides nothing.");
    expect(screen.getByRole("button", { name: "Ask again about this ad" })).toBeInTheDocument();
  });

  it("folds the daily table behind one line", async () => {
    mockFetch(full());
    mount();
    await screen.findByRole("heading", { level: 1, name: "Ad K1" });
    const fold = document.querySelector("details.more")!;
    expect(fold).not.toHaveAttribute("open");
    expect(text(fold.querySelector("summary"))).toBe(
      "Show 3 days — spend, impressions, link clicks, conversions, daily frequency and reach that day.");
    const rows = fold.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(3);
    expect([...(rows[2] as HTMLTableRowElement).cells].map(text)).toEqual(["2026-10-03", "$8.00", "800", "15", "2", "--", "--"]);
    expect(screen.getByLabelText("Spend per day")).toBeInTheDocument();
    expect(screen.getByLabelText("Conversions per day")).toBeInTheDocument();
  });

  it("renders without the panel, the copy or the rows, and says why each is missing", async () => {
    mockFetch(bare());
    mount();
    await screen.findByRole("heading", { level: 1, name: "Ad K1" });
    expect(screen.getByText("Paused")).toHaveClass("pill", "quiet");
    expect(screen.queryByText(/Stale tag\./)).toBeNull();
    expect(text(tile("Spend, 14d").querySelector(".v"))).toBe("--");
    expect(text(tile("CPA").querySelector(".v"))).toBe("--");
    expect(text(tile("Link CTR").querySelector(".v"))).toBe("--");
    expect(text(tile("Fatigue").querySelector(".sub"))).toBe("not in both windows");
    expect(screen.queryByLabelText("What the rules say")).toBeNull();
    expect(screen.getByText("No copy imported.")).toBeInTheDocument();
    expect(screen.getByText("No insights rows for this ad.")).toBeInTheDocument();
    expect(screen.getAllByText("Not enough days in this window to draw a trend.")).toHaveLength(2);
    // The button pill shows -- for no CTA, and a long URL is cut the way Jinja cut it.
    const copy = screen.getByLabelText("The copy");
    const foot = copy.querySelector("p.small:not(.muted)")!;
    expect(text(foot.querySelector(".pill"))).toBe("--");
    expect([...foot.querySelectorAll(".mono")].map(text)).toEqual(
      ["no utm_content", `${("https://example.com/" + "a".repeat(70)).slice(0, 67)}...`]);
    expect(document.body.textContent).not.toContain("$0.00");
  });
});

it("truncates the way Jinja's filter did", () => {
  expect(truncate("https://example.com/landing", 70)).toBe("https://example.com/landing");
  expect(truncate("a".repeat(90), 70)).toBe("a".repeat(67) + "...");
});

it("stays blue and white", () => {
  for (const file of ["AdPage.tsx"]) {
    const src = readFileSync(join(__dirname, file), "utf-8");
    expect(src).not.toMatch(/var\(--(ok|warn|err|good|bad)[a-z-]*\)/);
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
  }
});
