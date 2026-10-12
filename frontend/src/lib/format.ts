/* The formatters, the same ones charts.py exports to the prose.
 *
 * Python still formats every figure a model or a rule writes into a sentence
 * (intel/prose.py, intel/readings.py), and this file formats the tiles beside
 * those sentences. If the two disagreed by a cent, the reader would see the
 * sentence and the tile contradict each other. So they are pinned to ONE set
 * of answers: src/lib/format.cases.json, which tests/test_format_parity.py
 * checks against charts.py and format.test.ts checks against this file.
 *
 * Python formats with round-half-even on the exact binary value; toFixed and
 * Intl round half-up. They differ only on exact ties (0.125 -> "0.12" in
 * Python), which is why `fixed` works on the exact decimal expansion rather
 * than trusting either.
 */

export type Num = number | string | null | undefined;

/** charts._f: a number, or null for anything that is not one. */
export function f(v: unknown): number | null {
  if (v === null || v === undefined || typeof v === "boolean") return null;
  if (typeof v === "number") return Number.isNaN(v) ? null : v;
  if (typeof v === "string") {
    if (!v.trim()) return null;
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  }
  return null;
}

const MINUS = "−";

/** |x| to `places` decimals, rounded half-even on the exact binary value, as
 *  Python's format() does. No grouping, no sign. */
function fixedAbs(x: number, places: number): string {
  const a = Math.abs(x);
  if (!Number.isFinite(a) || a >= 1e21) return a.toFixed(places);
  const exact = a.toFixed(100); // exact for every double below 1e21
  const [int, frac] = exact.split(".");
  const keep = frac.slice(0, places);
  const rest = frac.slice(places);
  let digits = BigInt(int + keep);
  const lastOdd = digits % 2n === 1n;
  if (rest[0] > "5" || (rest[0] === "5" && (/[1-9]/.test(rest.slice(1)) || lastOdd))) {
    digits += 1n;
  }
  let s = digits.toString().padStart(places + 1, "0");
  if (places > 0) s = s.slice(0, -places) + "." + s.slice(-places);
  return s;
}

function group(s: string): string {
  const [i, d] = s.split(".");
  const g = i.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return d === undefined ? g : `${g}.${d}`;
}

/** Python's f"{x:,.{places}f}", including its "-" on a negative zero. */
function pyFixed(x: number, places: number): string {
  const neg = x < 0 || Object.is(x, -0);
  return (neg ? "-" : "") + group(fixedAbs(x, places));
}

/** Python's round(): half-even. */
export function pyRound(x: number): number {
  const s = fixedAbs(x, 0);
  return (x < 0 ? -1 : 1) * Number(s);
}

const nonzero = (shown: string) => /[1-9]/.test(shown);

/** $41.44, $15,748, and −$0.15 -- the sign goes in front of the currency. */
export function money(v: unknown, currency = "$"): string {
  const n = f(v);
  if (n === null) return "--";
  const a = Math.abs(n);
  const shown = group(fixedAbs(a, a < 1000 ? 2 : 0));
  const sign = n < 0 && nonzero(shown) ? MINUS : "";
  return `${sign}${currency}${shown}`;
}

export function num(v: unknown, places = 0): string {
  const n = f(v);
  return n === null ? "--" : pyFixed(n, places);
}

export function pct(v: unknown, places = 2): string {
  const n = f(v);
  return n === null ? "--" : `${pyFixed(n, places)}%`;
}

/** +12.3%, −8.1%: a change always says which way it went. */
export function signedPct(v: unknown, places = 1): string {
  const n = f(v);
  if (n === null) return "--";
  const shown = group(fixedAbs(n, places));
  if (!nonzero(shown)) return `${shown}%`;
  return `${n > 0 ? "+" : MINUS}${shown}%`;
}

/** An enum as a reader says it: QUALITY_LEAD -> "Quality lead". */
export function words(v: unknown): string {
  const s = v === null || v === undefined ? "" : String(v).trim();
  if (!s) return "--";
  const t = s.replace(/_/g, " ").toLowerCase();
  return t.slice(0, 1).toUpperCase() + t.slice(1);
}

const MONEY_KINDS = new Set(["cpa", "cpc", "cpm", "cost_per_link_click", "spend"]);
const PCT_KINDS = new Set(["ctr", "link_ctr", "conversion_rate", "lp_view_rate"]);

/** Format by metric kind, so a CPA never renders as a bare float. */
export function fmt(v: unknown, kind: string): string {
  if (MONEY_KINDS.has(kind)) return money(v);
  if (PCT_KINDS.has(kind)) return pct(v);
  return num(v);
}

/** How long ago something was written, the way a person says it. */
export function ago(hours: unknown): string {
  const h = f(hours);
  if (h === null) return "--";
  if (h < 1) return `${Math.max(1, pyRound(h * 60))} min ago`;
  if (h < 48) return `${pyRound(h)}h ago`;
  return `${Math.floor(h / 24)} days ago`;
}

/** Jinja's `truncate(length)`, which the templates used on headlines, URLs
 *  and hypotheses: unchanged within a 5-character leeway, otherwise cut at
 *  the last space before `length - 3` and ended with "...". */
export function truncate(s: string, length: number, end = "...", leeway = 5): string {
  if (s.length <= length + leeway) return s;
  const head = s.slice(0, length - end.length);
  const at = head.lastIndexOf(" ");
  return (at >= 0 ? head.slice(0, at) : head) + end;
}
