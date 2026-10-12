/* Charts, without a database. Ported from tests/test_charts_and_templates.py.
 *
 * Mostly degenerate input -- a flat series, one point, all zeros, a null in
 * the middle. Those are what actually turn up on a new ad account, and each
 * of them is a division by zero waiting in a naive implementation.
 */
import { render } from "@testing-library/react";
import { BarChart, ColumnChart, EffectBars, LineChart, Meter, PairedMoves, Sparkline, SplitBar, WindowDots, niceCeiling } from "./charts";

// Day strings, not Date objects: the data arrives ISO from jsonable_encoder,
// and toISOString() on a local Date is a day out under this machine's UTC+5:45.
function series(values: unknown[]): [string, unknown][] {
  return values.map((v, i) => [`2026-09-${String(i + 1).padStart(2, "0")}`, v]);
}

const html = (el: React.ReactElement) => render(el).container.innerHTML;

describe("LineChart", () => {
  it("renders svg", () => {
    const h = html(<LineChart points={series([1, 5, 3, 9])} kind="spend" />);
    expect(h).toContain("<svg");
    expect(h).toContain('data-chart="line"');
    expect(h).toContain("$9.00");
  });
  it("refuses to imply a trend from one point", () => {
    expect(html(<LineChart points={series([4])} />)).toContain("Not enough days");
    expect(html(<LineChart points={[]} />)).toContain("Not enough days");
  });
  it("survives a flat series", () => {
    expect(html(<LineChart points={series([5, 5, 5])} />)).toContain("<svg");
  });
  it("survives all zeros", () => {
    expect(html(<LineChart points={series([0, 0, 0])} />)).toContain("<svg");
  });
  it("skips undefined points rather than plotting zero", () => {
    const h = html(<LineChart points={series([1, null, 3])} />);
    expect((h.match(/class="dot"/g) ?? []).length).toBe(2);
  });
  it("carries a native title per point", () => {
    const h = html(<LineChart points={series([1, 2])} kind="spend" />);
    expect(h).toContain("<title>2026-09-01: $1.00</title>");
  });
});

describe("BarChart", () => {
  it("sorts by magnitude", () => {
    const h = html(<BarChart rows={[["a", 1], ["b", 9], ["c", 4]]} />);
    expect(h.indexOf(">b<")).toBeLessThan(h.indexOf(">c<"));
    expect(h.indexOf(">c<")).toBeLessThan(h.indexOf(">a<"));
  });
  it("keeps rows with no value", () => {
    const h = html(<BarChart rows={[["a", 1], ["none", null]]} />);
    expect(h).toContain("no conversions in this window");
    expect(h.indexOf(">a<")).toBeLessThan(h.indexOf(">none<"));
  });
  it("handles empty and all-null", () => {
    expect(html(<BarChart rows={[]} />)).toContain("Nothing to compare");
    expect(html(<BarChart rows={[["a", null]]} />)).toContain("<svg");
  });
  it("escapes labels", () => {
    const { container } = render(<BarChart rows={[["<b>x</b>", 1]]} />);
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("text.lbl")?.textContent).toBe("<b>x</b>");
  });
});

describe("Sparkline", () => {
  it("needs two points", () => {
    expect(html(<Sparkline values={[1]} />)).toContain("--");
    expect(html(<Sparkline values={[1, null]} />)).toContain("--");
  });
  it("breaks the line across gaps", () => {
    const h = html(<Sparkline values={[1, 2, null, 3, 4]} />);
    expect((h.match(/M /g) ?? []).length).toBe(2);
  });
});

describe("ColumnChart", () => {
  it("keeps the given order and picks out the peak", () => {
    const h = html(<ColumnChart rows={[["Mon", 2], ["Tue", 9], ["Wed", 4]]} />);
    expect(h.indexOf(">Mon<")).toBeLessThan(h.indexOf(">Tue<"));
    expect((h.match(/class="col"/g) ?? []).length).toBe(1);
    expect((h.match(/class="col dim"/g) ?? []).length).toBe(2);
  });
  it("draws a null bar as nothing, not as zero", () => {
    const h = html(<ColumnChart rows={[["a", null], ["b", 1]]} />);
    expect((h.match(/<rect/g) ?? []).length).toBe(1);
  });
  it("says so when every bar is null", () => {
    expect(html(<ColumnChart rows={[["a", null]]} />)).toContain("No data in this window");
  });
});

describe("the miniatures", () => {
  it("draw nothing rather than a guess", () => {
    expect(html(<SplitBar segments={[["a", 0, "--accent"]]} />)).toContain("Nothing to split");
    expect(html(<EffectBars rows={[["a", null, -1]]} />)).toContain("No effect to split");
    expect(html(<EffectBars rows={[["a", 0, -1]]} />)).toContain("No effect to split");
    expect(html(<WindowDots points={[["7d", 1, false]]} />)).toBe("");
    expect(html(<PairedMoves rows={[["CPA", null, null]]} />)).toBe("");
  });
  it("drop a zero segment rather than drawing a sliver", () => {
    const h = html(<SplitBar segments={[["a", 3, "--accent"], ["b", 0, "--focus"], ["c", 1, "--line"]]} />);
    expect((h.match(/<rect/g) ?? []).length).toBe(2);
  });
  it("name the two sides of an effect axis", () => {
    const h = html(<EffectBars rows={[["Rate", 3, -1], ["Net", -2, -1]]} kind="cpa" />);
    expect(h).toContain("lowers CPA");
    expect(h).toContain("raises CPA");
    expect(h).toContain("+$3.00");
  });
  it("draw a provisional window hollow", () => {
    const h = html(<WindowDots points={[["prior 7", 10, false], ["last 7", 12, false], ["unsettled", 11, true]]} current="last 7" />);
    expect(h).toContain("(provisional)");
    expect(h).toContain('fill="var(--surface)"');
  });
  it("meter draws an empty track with nothing to scale against", () => {
    expect(html(<Meter value={3} top={null} />)).not.toContain("<span style");
    expect(html(<Meter value={3} top={6} />)).toContain("width: 55px");
  });
});

describe("palette", () => {
  it("every chart colour is a token", () => {
    const h = [
      html(<LineChart points={series([1, 2, 3])} />),
      html(<BarChart rows={[["a", 1]]} />),
      html(<SplitBar segments={[["a", 1, "--accent"]]} />),
      html(<EffectBars rows={[["a", 1, -1], ["n", 1, -1]]} />),
      html(<WindowDots points={[["a", 1, false], ["b", 2, true]]} current="a" />),
      html(<PairedMoves rows={[["CPA", 10, -5]]} />),
    ].join("");
    expect(h).not.toMatch(/#[0-9a-f]{3,6}\b/i);
    expect(h).not.toMatch(/rgb\(/);
  });
  it("the miniatures are blue and white", () => {
    const h = html(<EffectBars rows={[["a", 1, -1], ["b", -1, 1], ["n", 1, 0]]} />)
      + html(<WindowDots points={[["a", 1, false], ["b", 2, true]]} />)
      + html(<PairedMoves rows={[["CPA", 10, -5]]} />);
    expect(h).not.toMatch(/var\(--(ok|warn|err|good|bad)/);
  });
});

it("niceCeiling rounds an axis maximum up to something a person would choose", () => {
  expect(niceCeiling(0)).toBe(1);
  expect(niceCeiling(7)).toBe(10);
  expect(niceCeiling(23)).toBe(25);
  expect(niceCeiling(1500)).toBe(2000);
});
