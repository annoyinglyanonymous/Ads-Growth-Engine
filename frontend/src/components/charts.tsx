/* Inline SVG charts, ported from charts.py.
 *
 * The rules charts.py argued for still hold, and they hold because the
 * geometry is the same, not because a library was configured to agree:
 *
 *   - Every chart is single-series. The dashboard is blue and white, so a
 *     second series would need a second hue. Change over time is a line,
 *     comparison across items is a sorted bar chart, a trend inside a table
 *     row is a sparkline, one number is a tile.
 *   - No dual-axis chart, ever. Two charts.
 *   - Colours are CSS custom properties (var(--accent), ...), never literals,
 *     so a chart follows the palette in base.css.
 *   - Nothing here computes a METRIC. The numbers arrive already derived from
 *     ads.rate(); this file only places them.
 *   - Every point carries a native <title>, so hovering gives a tooltip with
 *     no script. LineChart adds a crosshair on top of that.
 */
import { useRef, useState, type CSSProperties, type MouseEvent } from "react";
import { f, fmt, money, signedPct } from "../lib/format";

export type Point = [string, unknown];

const f1 = (n: number) => n.toFixed(1);

/** Round an axis maximum up to something a person would have chosen. */
export function niceCeiling(v: number): number {
  if (v <= 0) return 1.0;
  const mag = 10 ** Math.floor(Math.log10(v));
  for (const step of [1, 2, 2.5, 5, 10]) if (v <= step * mag) return step * mag;
  return 10 * mag;
}

function Empty({ children }: { children: string }) {
  return <div className="empty small">{children}</div>;
}

/* ------------------------------------------------------------------ line */

/** One series over time. Title names it, so there is no legend.
 *  Fewer than two defined points is not a trend and is not drawn as one. */
export function LineChart({ points, kind = "spend", label = "", height = 150, width = 760 }: {
  points: Point[]; kind?: string; label?: string; height?: number; width?: number;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);

  const defined = points
    .map(([d, v]) => [d, f(v)] as [string, number | null])
    .filter((p): p is [string, number] => p[1] !== null);
  if (defined.length < 2) return <Empty>Not enough days in this window to draw a trend.</Empty>;

  const [padL, padR, padT, padB] = [52, 10, 12, 24];
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const vals = defined.map(([, v]) => v);
  const hi = Math.max(...vals);
  const lo = Math.min(Math.min(...vals), 0);
  const span = hi > lo ? niceCeiling(hi - lo) : 1.0;
  const top = lo + span;
  const x = (i: number) => padL + (plotW * i) / (defined.length - 1);
  const y = (v: number) => padT + plotH - ((v - lo) / (top - lo)) * plotH;
  const pts = defined.map(([, v], i) => [x(i), y(v)]);
  const path = "M " + pts.map(([px, py]) => `${f1(px)} ${f1(py)}`).join(" L ");
  const base = f1(padT + plotH);
  const area = `M ${f1(pts[0][0])} ${base} L ` +
    pts.map(([px, py]) => `${f1(px)} ${f1(py)}`).join(" L ") +
    ` L ${f1(pts[pts.length - 1][0])} ${base} Z`;

  const first = defined[0][0], last = defined[defined.length - 1][0];
  const aria = `${label || kind} from ${first} to ${last}, ranging ${fmt(lo, kind)} to ${fmt(hi, kind)}`;

  // The useful hit target is the whole vertical band, not the 2.5px dot.
  function onMove(e: MouseEvent<SVGSVGElement>) {
    const box = svg.current!.getBoundingClientRect();
    const localX = (e.clientX - box.left) / (box.width / width || 1);
    let best = 0, bestD = Infinity;
    pts.forEach(([px], i) => {
      const d = Math.abs(px - localX);
      if (d < bestD) { bestD = d; best = i; }
    });
    setHover({ i: best, x: e.clientX, y: e.clientY });
  }

  return (
    <>
      <svg ref={svg} className="chart" viewBox={`0 0 ${width} ${height}`} role="img"
           aria-label={aria} data-chart="line" onMouseMove={onMove}
           onMouseLeave={() => setHover(null)}>
        {hover && <line className="cross" x1={f1(pts[hover.i][0])} x2={f1(pts[hover.i][0])}
                        y1={0} y2={height} />}
        {[0, 0.5, 1].map((frac) => {
          const gy = padT + plotH * frac;
          const val = top - (top - lo) * frac;
          return (
            <g key={frac}>
              <line className="grid-line" x1={padL} y1={f1(gy)} x2={width - padR} y2={f1(gy)} />
              <text className="axis" x={padL - 7} y={f1(gy + 3.5)} textAnchor="end">{fmt(val, kind)}</text>
            </g>
          );
        })}
        <path className="area" d={area} />
        <path className="line" d={path} />
        {pts.map(([px, py], i) => (
          <circle key={i} className="dot" cx={f1(px)} cy={f1(py)} r={2.5}>
            <title>{`${defined[i][0]}: ${fmt(defined[i][1], kind)}`}</title>
          </circle>
        ))}
        <text className="axis" x={padL} y={height - 6}>{first}</text>
        <text className="axis" x={width - padR} y={height - 6} textAnchor="end">{last}</text>
      </svg>
      {hover && <Tip x={hover.x} y={hover.y} title={fmt(defined[hover.i][1], kind)} sub={defined[hover.i][0]} />}
    </>
  );
}

/** The floating readout beside the cursor. Flips before the viewport edge:
 *  a tooltip clipped by the window is worse than none, because the number is
 *  the half that gets cut off. */
function Tip({ x, y, title, sub }: { x: number; y: number; title: string; sub: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const r = ref.current?.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (r && left + r.width > window.innerWidth - 8) left = x - r.width - 14;
  if (r && top + r.height > window.innerHeight - 8) top = y - r.height - 14;
  return (
    <div ref={ref} className="chart-tip" style={{ left: Math.max(8, left), top: Math.max(8, top) }}>
      <div className="t">{title}</div>{sub}
    </div>
  );
}

/* ------------------------------------------------------------------ bars */

/** Horizontal bars, sorted by magnitude. Rows with no value are kept and
 *  drawn as a sentence rather than dropped -- an angle with no CPA because it
 *  had no conversions is a finding. */
export function BarChart({ rows, kind = "cpa", width = 760, rowH = 28, labelW = 190 }: {
  rows: Point[]; kind?: string; width?: number; rowH?: number; labelW?: number;
}) {
  if (!rows.length) return <Empty>Nothing to compare in this window.</Empty>;
  const ranked = rows.map(([n, v]) => [String(n), f(v)] as [string, number | null])
    .map((r, i) => ({ r, i }))
    .sort((a, b) => {
      const an = a.r[1] === null, bn = b.r[1] === null;
      if (an !== bn) return an ? 1 : -1;
      return (b.r[1] ?? 0) - (a.r[1] ?? 0) || a.i - b.i;
    })
    .map(({ r }) => r);
  const values = ranked.map(([, v]) => v).filter((v): v is number => v !== null);
  const top = values.length ? niceCeiling(Math.max(...values)) : 1.0;
  const height = ranked.length * rowH + 10;
  const barW = width - labelW - 90;
  const best = values.length ? ranked[0][0] : "";
  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img"
         aria-label={`${kind} by item, ${ranked.length} items, highest is ${best}`} data-chart="bar">
      {ranked.map(([name, n], i) => {
        const yy = i * rowH + 5;
        const ty = f1(yy + rowH * 0.62);
        if (n === null) {
          return (
            <g key={i}>
              <text className="lbl" x={0} y={ty}>{name.slice(0, 28)}</text>
              <text className="axis" x={labelW} y={ty}>no conversions in this window</text>
            </g>
          );
        }
        const w = Math.max(1.0, barW * (n / top));
        return (
          <g key={i}>
            <text className="lbl" x={0} y={ty}>{name.slice(0, 28)}</text>
            <rect className="bar" x={labelW} y={yy + 4} width={f1(w)} height={rowH - 12} rx={4}>
              <title>{`${name}: ${fmt(n, kind)}`}</title>
            </rect>
            <text className="val" x={f1(labelW + w + 8)} y={ty}>{fmt(n, kind)}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** A shape, not a readout. No axis, no labels -- the table has the numbers. */
export function Sparkline({ values, width = 110, height = 26 }: {
  values: unknown[]; width?: number; height?: number;
}) {
  const nums = values.map(f);
  const defined = nums.filter((v): v is number => v !== null);
  if (defined.length < 2) return <span className="muted small">--</span>;
  const lo = Math.min(...defined), hi = Math.max(...defined);
  const span = hi - lo || 1.0;
  const step = nums.length > 1 ? width / (nums.length - 1) : width;
  const parts: string[] = [];
  let pen = false;
  nums.forEach((v, i) => {
    if (v === null) { pen = false; return; }
    const py = height - 2 - ((v - lo) / span) * (height - 4);
    parts.push(`${pen ? "L" : "M"} ${f1(i * step)} ${f1(py)}`);
    pen = true;
  });
  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={parts.join(" ")} />
    </svg>
  );
}

/** Vertical bars in the order given, with the peak picked out. A null bar is
 *  drawn as nothing, not as zero. */
export function ColumnChart({ rows, kind = "conversions", width = 360, height = 168, highlight = "max" }: {
  rows: Point[]; kind?: string; width?: number; height?: number; highlight?: "max" | "none";
}) {
  const vals = rows.map(([k, v]) => [String(k), f(v)] as [string, number | null]);
  if (!vals.some(([, v]) => v !== null)) return <Empty>No data in this window.</Empty>;
  const [padT, padB, padX] = [16, 22, 4];
  const plotH = height - padT - padB;
  const defined = vals.map(([, v], i) => [i, v] as [number, number | null])
    .filter((p): p is [number, number] => p[1] !== null);
  const top = niceCeiling(Math.max(0, ...defined.map(([, v]) => v))) || 1.0;
  let peak: number | null = null;
  if (highlight === "max" && defined.length) {
    peak = defined.reduce((a, b) => (b[1] > a[1] ? b : a))[0];
  }
  const slot = (width - padX * 2) / Math.max(vals.length, 1);
  const bw = Math.min(slot * 0.52, 26);
  const hiName = peak !== null ? vals[peak][0] : "";
  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img"
         aria-label={`${kind} by ${vals.length} periods${hiName ? `, highest ${hiName}` : ""}`}
         data-chart="column">
      {vals.map(([name, v], i) => {
        const cx = padX + slot * i + slot / 2;
        const bx = cx - bw / 2;
        let bar = null;
        if (v !== null) {
          const bh = top ? Math.max((v / top) * plotH, 1.5) : 1.5;
          const by = padT + plotH - bh;
          const isPeak = peak !== null && i === peak;
          bar = (
            <>
              <rect className={isPeak ? "col" : "col dim"} x={f1(bx)} y={f1(by)}
                    width={f1(bw)} height={f1(bh)} rx={4}>
                <title>{`${name}: ${fmt(v, kind)}`}</title>
              </rect>
              {isPeak && <text className="val" x={f1(cx)} y={f1(by - 5)} textAnchor="middle">{fmt(v, kind)}</text>}
            </>
          );
        }
        return (
          <g key={i}>
            {bar}
            <text className="axis" x={f1(cx)} y={f1(height - 6)} textAnchor="middle">{name}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ------------------------------------------------------------ miniatures */

/** One bar cut into proportional parts, in the order given. `tone` names a CSS
 *  custom property. A zero segment is dropped rather than drawn as a sliver. */
export function SplitBar({ segments, height = 12 }: {
  segments: [string, unknown, string][]; height?: number;
}) {
  const vals = segments.map(([n, v, t]) => [String(n), f(v) ?? 0, t] as [string, number, string]);
  const total = vals.reduce((s, [, v]) => s + (v > 0 ? v : 0), 0);
  if (total <= 0) return <Empty>Nothing to split in this window.</Empty>;
  let x = 0;
  const aria = vals.filter(([, v]) => v > 0).map(([n, v]) => `${n} ${pyG(v)}`).join(", ");
  return (
    <svg className="split" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none"
         style={{ height }} role="img" aria-label={aria} data-chart="split">
      {vals.filter(([, v]) => v > 0).map(([name, v, tone], i) => {
        const w = (100.0 * v) / total;
        const rect = (
          <rect key={i} x={x.toFixed(4)} y={0} width={w.toFixed(4)} height={height} fill={`var(${tone})`}>
            <title>{name}</title>
          </rect>
        );
        x += w;
        return rect;
      })}
    </svg>
  );
}

/** Python's "{:g}" for the counts split_bar's label carries. */
function pyG(v: number): string {
  return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(6)));
}

/** Signed bars around a shared zero -- the CPA bridge in miniature. Blue only:
 *  merit is carried by which side a bar sits on, and the axis names the sides. */
export function EffectBars({ rows, kind = "cpa", width = 200, rowH = 17, labelW = 30, valueW = 58 }: {
  rows: [string, unknown, number][]; kind?: string; width?: number; rowH?: number;
  labelW?: number; valueW?: number;
}) {
  const vals = rows.map(([n, v]) => [String(n), f(v)] as [string, number | null]);
  const mags = vals.map(([, v]) => v).filter((v): v is number => v !== null).map(Math.abs);
  if (!mags.length || Math.max(...mags) <= 0) return <Empty>No effect to split in this window.</Empty>;
  const top = Math.max(...mags);
  const track = width - labelW - valueW - 12;
  const half = track / 2.0;
  const mid = labelW + 6 + half;
  const axisH = 13;
  const height = vals.length * rowH + 4 + axisH;
  const last = vals.length - 1;
  const barsH = vals.length * rowH + 2;
  const what = ["cpa", "cpm", "cpc"].includes(kind) ? kind.toUpperCase() : kind.replace(/_/g, " ");
  const ay = barsH + axisH - 2;
  const aria = vals.map(([n, v]) => `${n} ${fmt(v, kind)}`).join(", ");
  return (
    <svg className="chart micro" viewBox={`0 0 ${width} ${height}`}
         style={{ width, maxWidth: "100%" }} role="img" aria-label={aria} data-chart="effect">
      {vals.map(([name, v], i) => {
        const yy = i * rowH + 2;
        const cy = f1(yy + rowH * 0.62);
        const head = (
          <>
            <text className="axis" x={labelW} y={cy} textAnchor="end">
              {name.slice(0, Math.max(9, Math.floor(labelW / 6)))}
            </text>
            <rect className="track" x={labelW + 6} y={yy + 3} width={f1(track)} height={rowH - 8} rx={2} />
          </>
        );
        if (v === null) {
          return <g key={i}>{head}<text className="axis" x={width} y={cy} textAnchor="end">--</text></g>;
        }
        const tone = i === last ? "--accent" : "--focus";
        const w = Math.max(1.0, half * (Math.abs(v) / top));
        const bx = v > 0 ? mid : mid - w;
        const shown = (v > 0 ? "+" : "") + fmt(v, kind);
        return (
          <g key={i}>
            {head}
            <rect x={f1(bx)} y={yy + 3} width={f1(w)} height={rowH - 8} rx={2} fill={`var(${tone})`}>
              <title>{`${name}: ${shown}`}</title>
            </rect>
            <text className="val" x={width} y={cy} textAnchor="end" fill="var(--accent)">{shown}</text>
          </g>
        );
      })}
      <line className="zero" x1={f1(mid)} y1={2} x2={f1(mid)} y2={barsH} />
      <text className="axis" x={labelW + 6} y={ay}>{`← lowers ${what}`}</text>
      <text className="axis" x={f1(labelW + 6 + track)} y={ay} textAnchor="end">{`raises ${what} →`}</text>
    </svg>
  );
}

/** A bar inside a table row, scaled against the column maximum the caller
 *  passes. Nothing to scale against draws an empty track, not a zero. */
export function Meter({ value, top, width = 110 }: { value: unknown; top: unknown; width?: number }) {
  const n = f(value), t = f(top);
  return (
    <span className="meter" style={{ width }} aria-hidden="true">
      {n !== null && t ? <span style={{ width: `${f1(Math.max(2.0, width * Math.min(1.0, Math.abs(n) / Math.abs(t))))}px` }} /> : null}
    </span>
  );
}

/** One figure per window, as dots on one scale -- a row of readings, not a
 *  time series, so no line joins them. Provisional windows are hollow. */
export function WindowDots({ points, current = null, width = 300, height = 74 }: {
  points: [string, unknown, boolean][]; current?: string | null; width?: number; height?: number;
}) {
  const vals = points.map(([l, v, p]) => [String(l), f(v), Boolean(p)] as [string, number | null, boolean]);
  const shown = vals.map(([, v]) => v).filter((v): v is number => v !== null);
  if (shown.length < 2) return null;
  const lo = Math.min(...shown), hi = Math.max(...shown);
  const span = hi - lo || 1.0;
  const x0 = 26.0, x1 = width - 26.0;
  const step = (x1 - x0) / Math.max(1, vals.length - 1);
  const top = 22.0, bottom = height - 22.0;
  const aria = vals.filter(([, v]) => v !== null).map(([l, v]) => `${l} ${money(v)}`).join(", ");
  return (
    <svg className="chart micro" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={aria}
         data-chart="windows">
      <line className="grid-line" x1={x0} y1={f1(bottom + 4)} x2={x1} y2={f1(bottom + 4)} />
      {vals.map(([lab, v, prov], i) => {
        const x = x0 + i * step;
        const label = <text className="axis" x={f1(x)} y={height - 4} textAnchor="middle">{lab}</text>;
        if (v === null) return <g key={i}>{label}</g>;
        const yy = bottom - ((v - lo) / span) * (bottom - top);
        const cur = lab === current;
        const fill = prov ? "var(--surface)" : cur ? "var(--accent)" : "var(--accent-hi)";
        const stroke = cur ? "var(--accent)" : "var(--accent-hi)";
        return (
          <g key={i}>
            {label}
            <circle cx={f1(x)} cy={f1(yy)} r={cur ? 5 : 4} fill={fill} stroke={stroke} strokeWidth={2}>
              <title>{`${lab}: ${money(v)}${prov ? " (provisional)" : ""}`}</title>
            </circle>
            <text className="val" x={f1(x)} y={f1(yy - 9)} textAnchor="middle">{money(v)}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** Two signed bars per metric -- last 7 days dark, last 14 pale -- around one
 *  zero. A long dark bar over a short pale one is a wobble; two bars the same
 *  way is a move. */
export function PairedMoves({ rows, width = 136 }: { rows: [string, unknown, unknown][]; width?: number }) {
  const vals = rows.map(([n, a, b]) => [String(n), f(a), f(b)] as [string, number | null, number | null])
    .filter(([, a, b]) => a !== null || b !== null);
  const mags = vals.flatMap(([, a, b]) => [a, b]).filter((v): v is number => v !== null).map(Math.abs);
  if (!vals.length || !mags.length) return null;
  const top = Math.max(...mags) || 1.0;
  const half = width / 2.0;
  const bar = (v: number | null, tone: string) => {
    if (v === null) return <span className="pm-b" />;
    const w = v ? Math.max(1.0, (half * Math.abs(v)) / top) : 0.0;
    const style: CSSProperties = { width: `${f1(w)}px`, background: `var(${tone})` };
    style[v >= 0 ? "left" : "right"] = `${f1(half)}px`;
    return <span className="pm-b"><span style={style} /></span>;
  };
  return (
    <div className="pm" data-chart="paired">
      {vals.map(([name, a, b], i) => (
        <div className="pm-row" key={i}>
          <span className="pm-k">{name}</span>
          <span className="pm-t" style={{ width }}>{bar(a, "--accent")}{bar(b, "--focus")}</span>
          <span className="pm-v"><b>{a !== null ? signedPct(a, 0) : "--"}</b>{" "}
            <span>{b !== null ? signedPct(b, 0) : "--"}</span></span>
        </div>
      ))}
    </div>
  );
}
