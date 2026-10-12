import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Layout, PageFrame } from "./Layout";

function Page() {
  return <PageFrame nav="why" title="Why CPA moved" brand="renegade" settled="2026-10-06">
    <p>body</p></PageFrame>;
}

function mount(path: string, status: object = { ok: false, error: "no status" }) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(status), { status: 200 })));
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route element={<Layout />}><Route path="/why" element={<Page />} /></Route></Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("the shell", () => {
  it("marks the current page in the rail and carries the brand on every link", async () => {
    const { container } = mount("/why?brand=renegade");
    await screen.findByText("body");
    const current = container.querySelector('.rail-link[aria-current="page"]');
    expect(current?.textContent).toBe("Why CPA moved");
    const hrefs = [...container.querySelectorAll("a.rail-link")].map((a) => a.getAttribute("href"));
    expect(hrefs).toHaveLength(7);
    for (const h of hrefs) expect(h).toMatch(/\?brand=renegade$/);
  });

  it("reports the settled edge the page gave it", async () => {
    const { container } = mount("/why?brand=renegade");
    await waitFor(() => expect(container.querySelector(".rail-foot .freshness .v")?.textContent).toBe("2026-10-06"));
    expect(container.textContent).toContain("Meta restates for ~3 days.");
  });

  it("draws the import card from /refresh/status", async () => {
    const { container } = mount("/why?brand=renegade", {
      ok: true, running: false, stalled: false, age_hours: 2, insights_through: "2026-10-02",
      accounts: [], accounts_ok: 1, accounts_failed: 0,
    });
    await waitFor(() => expect(container.querySelector("#import-freshness")).not.toBeNull());
    expect(container.querySelector("#import-freshness .v")?.textContent).toBe("2h ago");
    expect(screen.getByRole("button", { name: "Refresh now" })).toBeEnabled();
  });

  it("puts the topbar ask box on a page unless it hides it", async () => {
    mount("/why?brand=renegade");
    await screen.findByText("body");
    expect(screen.getByLabelText("Ask a question about this account")).toBeInTheDocument();
  });
});
