import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { CreativePage, type CreativeData } from "./CreativePage";

// Synthetic JSON shaped like ui.creative's output: `d` is metrics.fatigue,
// the extras are window, min_spend, unconfident and spark.
const BASE = { nav: "creative", brand: "renegade", settled: "2026-10-05", caveat: null, until: null, today: "2026-10-09" };

type Row = CreativeData["d"]["rows"][number];

function row(over: Partial<Row> & { ad_key: string }): Row {
  return {
    entity_name: `Ad ${over.ad_key}`, optimization_goal: "OFFSITE_CONVERSIONS", spend_recent: 250.5, confident: true,
    link_ctr_decline: false, cpm_rise: false, cpa_rise: false, frequency_rise: false, ranking_drop: false, score: 0,
    recent: { rates: { cpa: 41.44 }, daily_frequency_avg: 1.12 },
    prior: { rates: { cpa: 30 }, daily_frequency_avg: 1.05 },
    ...over,
  };
}

function payload(rows: Row[], over: Partial<CreativeData> = {}): CreativeData {
  return {
    ...BASE,
    d: { brand: "renegade", rows, suppressed_unconfident: 0 },
    window: 7, min_spend: 100, unconfident: 0, spark: {},
    ...over,
  };
}

const FOUR = [
  row({ ad_key: "a", score: 3, link_ctr_decline: true, cpa_rise: true, cpm_rise: true }),
  row({ ad_key: "b", score: 2, link_ctr_decline: true, frequency_rise: true }),
  row({ ad_key: "c", score: 1, link_ctr_decline: true, confident: false,
        recent: { rates: { cpa: null }, daily_frequency_avg: null }, prior: { rates: {}, daily_frequency_avg: 1.2 } }),
  row({ ad_key: "d", score: 0 }),
];

function mockFetch(data: unknown) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, status: 200, json: async () => data }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

function mount(path = "/creative?brand=renegade") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/creative" element={<CreativePage />} /></Routes>
    </MemoryRouter>,
  );
}

const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();

afterEach(() => vi.unstubAllGlobals());

describe("the fatigue page", () => {
  it("asks its /api twin with the page's query string", async () => {
    const fetch = mockFetch(payload(FOUR));
    mount("/creative?brand=renegade&window=7");
    await screen.findByLabelText("Fatigue in one line");
    expect(fetch).toHaveBeenCalledWith("/api/creative?brand=renegade&window=7", expect.anything());
  });

  it("counts the symptoms into the headline sentence", async () => {
    mockFetch(payload(FOUR, { d: { brand: "renegade", rows: FOUR, suppressed_unconfident: 2 } }));
    mount();
    const answer = await screen.findByLabelText("Fatigue in one line");
    expect(text(answer.querySelector("p"))).toBe(
      "Of 4 ads with enough spend to read, 1 show three or more symptoms and 1 show two. " +
      "Click-through down is the most common symptom, on 3.");
    // The pictures, the fold, and the stamp.
    expect(screen.getByLabelText("How many symptoms each ad carries")).toBeInTheDocument();
    expect(screen.getByLabelText("Which symptom fires")).toBeInTheDocument();
    expect(screen.getByText("ads.fatigue")).toBeInTheDocument();
    expect(text(document.querySelector("details.more > summary"))).toBe(
      "1 readable ad(s) carry no symptom and are not listed.");
    // Counted, not listed: the ads below the floor, with the link that shows them.
    const show = screen.getByRole("link", { name: "show them" });
    expect(show).toHaveAttribute("href", "/creative?brand=renegade&window=7&unconfident=1");
    expect(text(show.parentElement)).toBe("2 more below the floor · show them");
  });

  it("lists only the ads carrying a symptom and never prints an absent CPA as money", async () => {
    mockFetch(payload(FOUR));
    mount();
    await screen.findByLabelText("The ads");
    expect(screen.getByRole("link", { name: "Ad a" })).toHaveAttribute("href", "/ad/a?brand=renegade");
    expect(screen.queryByRole("link", { name: "Ad d" })).toBeNull();
    const c = screen.getByRole("link", { name: "Ad c" }).closest("tr")!;
    expect(text(c.cells[4])).toBe("-- → --");
    expect(text(c.cells[5])).toBe("1.20 → --");
    expect(c).toHaveTextContent("low volume");
    expect(document.body.textContent).not.toContain("$0.00");
    // The grid says which symptoms, by position and by name.
    const a = screen.getByRole("link", { name: "Ad a" }).closest("tr")!;
    expect(a.querySelector(".sym")).toHaveAttribute("aria-label",
      "click-through down, impression cost up, acquisition cost up");
    expect(a.querySelectorAll(".sym span.on")).toHaveLength(3);
  });

  it("says why there is nothing to compare on a new account", async () => {
    mockFetch(payload([]));
    mount();
    const empty = await screen.findByText(/No ad ran in both/);
    expect(text(empty)).toBe(
      "No ad ran in both the recent 7 days and the 7 before them, so there is nothing to compare. " +
      "That is the usual answer on a new account.");
  });

  it("says so when every readable ad is symptom-free", async () => {
    mockFetch(payload([row({ ad_key: "d" }), row({ ad_key: "e" })]));
    mount();
    expect(await screen.findByText("No readable ad carries a symptom in this window.")).toBeInTheDocument();
    const answer = screen.getByLabelText("Fatigue in one line");
    expect(text(answer.querySelector("p"))).toBe(
      "Of 2 ads with enough spend to read, 0 show three or more symptoms and 0 show two.");
  });

  it("switches the window by link and the filters by navigation", async () => {
    const fetch = mockFetch(payload(FOUR));
    mount();
    await screen.findByLabelText("Fatigue in one line");
    expect(screen.getByRole("link", { name: "7d" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "14d" })).toHaveAttribute("href", "/creative?brand=renegade&window=14");
    fireEvent.change(document.getElementById("unconfident")!, { target: { value: "1" } });
    await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(
      expect.stringMatching(/^\/api\/creative\?.*unconfident=1/), expect.anything()));
  });
});

it("stays blue and white", () => {
  const src = readFileSync(join(__dirname, "CreativePage.tsx"), "utf-8");
  expect(src).not.toMatch(/var\(--(ok|warn|err|good|bad)[a-z-]*\)/);
  expect(src).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
});
