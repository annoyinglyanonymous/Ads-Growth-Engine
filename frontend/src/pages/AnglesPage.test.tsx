import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AnglesPage, type AnglesData } from "./AnglesPage";
import { truncate } from "../lib/format";

// Synthetic JSON shaped like ui.angles's output: `d` is angles.coverage, the
// extras are days, candidates, queue, labels and bars.
const BASE = { nav: "angles", brand: "renegade", settled: "2026-10-05", caveat: null, until: null, today: "2026-10-09" };

const LABELS: AnglesData["labels"] = {
  creative_labels: { ads: 40, with_a_hook: 15, with_an_offer: 12 },
  angle_attribution: { total_spend: 5000, tagged_spend: 0, untagged_spend: 5000 },
};

function inert(over: Partial<AnglesData> = {}): AnglesData {
  return {
    ...BASE,
    d: { brand: "renegade", product: null, tested: [], under_spent: [], never_run: [], prior_experiments: [] },
    days: 90, candidates: [], queue: { rows: [] }, labels: LABELS, bars: [],
    ...over,
  };
}

const LONG = "A headline that is long enough to be cut by the truncate filter at sixty characters";

function active(): AnglesData {
  return inert({
    d: {
      brand: "renegade", product: null,
      tested: [{ angle_slug: "a1", angle_name: "Angle one", family: "fam", cpa: 40 },
               { angle_slug: "a2", angle_name: "Angle two", family: "fam", cpa: 55 }],
      under_spent: [{ angle_slug: "a3", angle_name: "Angle three", family: "fam", cpa: null }],
      never_run: [{ angle_slug: "a4", angle_name: "Angle four", family: "Family X" }],
      prior_experiments: [{ name: "Test of four", conclusion: null, angle_slug: "a4" }],
    },
    bars: [["Angle one", 40], ["Angle two", 55], ["Angle three", null]],
    candidates: [{ observed: "handle-a", reviews: 3, last_seen: "2026-09-30T10:00:00" }],
    queue: { rows: [
      { ad_key: "k1", entity_name: "Ad K1", first_headline: LONG, spend: 1200, conversions: 0,
        inheritable: false, campaign_angle_name: null, angle_observed: "trust" },
      { ad_key: "k2", entity_name: "Ad K2", first_headline: null, spend: 300.5, conversions: 2,
        inheritable: true, campaign_angle_name: "Angle one", angle_observed: null },
      { ad_key: "k3", entity_name: "Ad K3", first_headline: "Short", spend: null, conversions: null,
        inheritable: false, campaign_angle_name: null, angle_observed: null },
    ] },
  });
}

function mockFetch(data: unknown) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, status: 200, json: async () => data }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

function mount(path = "/angles?brand=renegade") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/angles" element={<AnglesPage />} /></Routes>
    </MemoryRouter>,
  );
}

const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();

afterEach(() => vi.unstubAllGlobals());

describe("the angles page while the bank is inert", () => {
  it("leads with the reason and the steps, and folds the empty sections into one line each", async () => {
    const fetch = mockFetch(inert());
    mount();
    const answer = await screen.findByLabelText("Angles in one line");
    expect(fetch).toHaveBeenCalledWith("/api/angles?brand=renegade", expect.anything());
    expect(text(answer.querySelector("p"))).toBe(
      "No angle is active in the bank, so no ad can carry one. " +
      "Hook, offer and audience are labelled on 12 of 40 ads that spent — only angle is empty.");
    expect(screen.getByRole("link", { name: "How an angle becomes active" })).toHaveAttribute("href", "#steps");

    // The two coverages say different things.
    const labels = screen.getByLabelText("Creative labels");
    expect(text(labels.querySelector(".big"))).toBe("12 of 40 ads");
    expect(labels).toHaveTextContent("hook on 15 · offer on 12");
    const angle = screen.getByLabelText("Angle");
    expect(text(angle.querySelector(".big"))).toBe("$0.00 of $5,000");
    expect(angle).toHaveTextContent("zero until the bank has an active member");

    // The steps, with the one command and no button that signs anything.
    const steps = screen.getByLabelText("How an angle becomes active");
    expect(steps).toHaveTextContent("python -m intel record --kind angle_proposal");
    expect(steps).toHaveTextContent("A person signs it in growth-engine, under their own name. Nothing on this site does this step.");
    expect(within(steps).queryByRole("button")).toBeNull();

    // Nothing to report, and why.
    const quiet = screen.getByLabelText("Nothing to report");
    const rows = quiet.querySelectorAll(".quiet-row");
    expect(rows).toHaveLength(3);
    const quietRow = (r: Element) => [".t", ".pill", ".w"].map((s) => text(r.querySelector(s)));
    expect(quietRow(rows[0])).toEqual(["CPA by angle", "nothing to show", "No active angle carried spend in the last 90 days."]);
    expect(quietRow(rows[1])).toEqual(["Never run", "nothing to show", "A set difference against a bank with no active members."]);
    expect(quietRow(rows[2])).toEqual(["Unmapped handles", "clear", "Every handle a reviewer wrote maps to an angle."]);
    expect(rows[2].querySelector(".pill")).toHaveClass("ok");
    expect(rows[0].querySelector(".pill")).toHaveClass("quiet");
    expect(screen.getByText("Every ad that spent in this window carries a label.")).toBeInTheDocument();
  });

  it("renders an absent coverage as -- rather than as zero dollars", async () => {
    mockFetch(inert({ labels: { creative_labels: {}, angle_attribution: {} } }));
    mount();
    const answer = await screen.findByLabelText("Angles in one line");
    expect(text(answer.querySelector("p"))).toBe("No angle is active in the bank, so no ad can carry one.");
    expect(text(screen.getByLabelText("Angle").querySelector(".big"))).toBe("-- of --");
    expect(text(screen.getByLabelText("Creative labels").querySelector(".big"))).toBe("-- of -- ads");
    expect(document.body.textContent).not.toContain("$0.00");
  });
});

describe("the angles page once angles are active", () => {
  it("counts the bank into the headline and brings every section back", async () => {
    mockFetch(active());
    mount();
    const answer = await screen.findByLabelText("Angles in one line");
    expect(text(answer.querySelector("p"))).toBe(
      "2 active angle(s) ran in this window, 1 too little to read and 1 not at all. " +
      "Hook, offer and audience are labelled on 12 of 40 ads that spent.");
    expect(screen.queryByLabelText("How an angle becomes active")).toBeNull();
    expect(screen.queryByText("Nothing to report, and why")).toBeNull();

    const cpa = screen.getByLabelText("CPA by angle");
    expect(cpa).toHaveTextContent("intel coverage");
    expect(cpa.querySelector("svg")).toHaveAttribute("aria-label", expect.stringContaining("3 items"));
    expect(cpa).toHaveTextContent("no conversions in this window");
    expect(cpa).toHaveTextContent("Only comparable between angles whose ads ran under the same optimization goal.");

    const never = screen.getByLabelText("Never run");
    expect(text(never.querySelector(".pill"))).toBe("Family X");
    expect(text(never.querySelector("strong"))).toBe("Angle four");
    expect(text(never.querySelector(".why"))).toBe("tested before: Test of four — no conclusion recorded");

    const handles = screen.getByLabelText("Unmapped handles");
    const first = handles.querySelector("tbody tr") as HTMLTableRowElement;
    expect([...first.cells].map(text)).toEqual(["handle-a", "3", "2026-09-30"]);
  });

  it("ranks the unlabelled ads by spend and says what each one needs", async () => {
    mockFetch(active());
    mount();
    await screen.findByLabelText("Still unlabelled");
    expect(screen.getByText("intel queue")).toBeInTheDocument();
    const k1 = screen.getByRole("link", { name: "Ad K1" }).closest("tr")!;
    expect(k1.querySelector("a")).toHaveAttribute("href", "/ad/k1?brand=renegade");
    expect(text(k1.cells[1]).endsWith("...")).toBe(true);
    expect(text(k1.cells[1]).length).toBeLessThanOrEqual(60);
    expect(text(k1.cells[2])).toBe("$1,200");
    expect(text(k1.cells[3])).toBe("0");
    expect(text(k1.cells[4])).toBe("reviewer said “trust”");
    const k2 = screen.getByRole("link", { name: "Ad K2" }).closest("tr")!;
    expect(text(k2.cells[1])).toBe("--");
    expect(text(k2.cells[2])).toBe("$300.50");
    expect(k2.cells[4].querySelector(".pill")).toHaveClass("ok");
    expect(text(k2.cells[4])).toBe("Angle one");
    const k3 = screen.getByRole("link", { name: "Ad K3" }).closest("tr")!;
    expect(text(k3.cells[2])).toBe("--");
    expect(text(k3.cells[3])).toBe("--");
    expect(text(k3.cells[4])).toBe("needs reading");
  });

  it("switches the window by link and the product by navigation", async () => {
    const fetch = mockFetch(active());
    mount();
    await screen.findByLabelText("Angles in one line");
    expect(screen.getByRole("link", { name: "90d" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "365d" })).toHaveAttribute("href", "/angles?brand=renegade&days=365");
    const input = document.getElementById("product") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "trucking" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(fetch).toHaveBeenLastCalledWith("/api/angles?brand=renegade&product=trucking", expect.anything()));
  });
});

it("truncates the way Jinja's filter did", () => {
  expect(truncate("Short", 60)).toBe("Short");
  expect(truncate("a".repeat(65), 60)).toBe("a".repeat(65)); // within the leeway
  expect(truncate("one two three four", 10)).toBe("one...");
  expect(truncate("a".repeat(70), 10)).toBe("aaaaaaa...");
});

it("stays blue and white", () => {
  const src = readFileSync(join(__dirname, "AnglesPage.tsx"), "utf-8");
  expect(src).not.toMatch(/var\(--(ok|warn|err|good|bad)[a-z-]*\)/);
  expect(src).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
});
