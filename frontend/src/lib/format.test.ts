import cases from "./format.cases.json";
import { ago, fmt, money, num, pct, signedPct, words } from "./format";

// The same answers tests/test_format_parity.py holds charts.py to.
type Row = unknown[];
const c = cases as Record<string, Row[]>;

describe("format.ts agrees with charts.py", () => {
  it.each(c.money)("money(%j)", (v, want) => expect(money(v)).toBe(want));
  it.each(c.num)("num(%j, %j)", (v, p, want) => expect(num(v, p as number)).toBe(want));
  it.each(c.pct)("pct(%j, %j)", (v, p, want) => expect(pct(v, p as number)).toBe(want));
  it.each(c.signed_pct)("signedPct(%j, %j)", (v, p, want) =>
    expect(signedPct(v, p as number)).toBe(want));
  it.each(c.fmt)("fmt(%j, %j)", (v, k, want) => expect(fmt(v, k as string)).toBe(want));
  it.each(c.ago)("ago(%j)", (h, want) => expect(ago(h)).toBe(want));
  it.each(c.words)("words(%j)", (w, want) => expect(words(w)).toBe(want));
});

it("never renders an absent figure as zero", () => {
  expect(money(null)).toBe("--");
  expect(money(undefined)).toBe("--");
  expect(money(0)).toBe("$0.00");
});
