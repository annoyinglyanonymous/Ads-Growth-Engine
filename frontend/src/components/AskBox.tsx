/* The ask box, ported from templates/_ask.html and the matching block of
 * static/app.js.
 *
 * Two fetches per question, and only ONE of them is ever drawn on its own.
 * The router (/ask.json) returns in about a second and the session
 * (/ask/prose.json) takes the better part of two minutes. The JSON was never
 * the answer -- it is the receipt for the answer -- so it waits and arrives
 * underneath the prose, collapsed. It still runs, because the receipt needs
 * it and it is the fallback if the session fails.
 *
 * Everything printed is a question a person typed or a value that came back
 * from the server; nothing is rendered as HTML.
 */
import { useRef, useState, type FormEvent } from "react";
import { getJson } from "../lib/api";
import { Icon } from "./Icon";

export interface Routed {
  verb?: string;
  says?: string;
  args?: unknown;
  result?: unknown;
  error?: string;
  verbs?: string[];
}

export interface Prose {
  ok: boolean;
  answer?: string;
  cost_usd?: number;
  turns?: number;
  error?: string;
}

type Row =
  | { who: "me"; text: string }
  | { who: "bot"; state: "thinking" }
  | { who: "bot"; state: "done"; prose: Prose | null; routed: Routed | null; failure: string | null };

const SUGGESTIONS: [string, string][] = [
  ["why did CPA move in the last 7 days", "Why did CPA move?"],
  ["what creatives are fatiguing", "What's fatiguing?"],
  ["which angles have we never tested", "Untested angles"],
  ["status", "Is this data fresh?"],
];

/** The verb's own output, as the whole bubble. */
function RoutedAnswer({ routed }: { routed: Routed }) {
  if (routed.error) return <>{routed.error}</>;
  return (
    <>
      <div><strong>{routed.verb}</strong>{` — ${routed.says ?? ""}`}</div>
      <pre>{JSON.stringify(routed.result, null, 2)}</pre>
    </>
  );
}

function Bubble({ row }: { row: Row }) {
  if (row.who === "me") return <div className="ask-bubble">{row.text}</div>;
  if (row.state === "thinking") return <div className="ask-bubble ask-thinking">Thinking…</div>;

  const { prose, routed, failure } = row;
  const routedOk = routed && !routed.error;
  if (prose?.ok && prose.answer) {
    return (
      <div className="ask-bubble">
        <div className="ask-prose">{prose.answer}</div>
        {/* Just the cost. What it can and cannot do belongs in the header, once. */}
        <div className="n" style={{ marginTop: 8 }}>{prose.cost_usd ? `$${prose.cost_usd.toFixed(2)}` : ""}</div>
        {routedOk && (
          <details style={{ marginTop: 8 }}>
            <summary className="n">the {routed.verb} output this came from</summary>
            <pre>{JSON.stringify(routed.result, null, 2)}</pre>
          </details>
        )}
      </div>
    );
  }
  // The session failed but the router may have answered. Keep whatever was
  // drawn rather than replacing a good answer with an error, and say why the
  // written one is missing.
  return (
    <div className="ask-bubble">
      {routedOk ? <RoutedAnswer routed={routed} /> : null}
      <div className="n" style={{ marginTop: routedOk ? 8 : 0 }}>
        {failure ?? prose?.error ?? routed?.error ?? "No answer."}
      </div>
    </div>
  );
}

export function AskBox({ brand }: { brand: string | null }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const log = useRef<HTMLDivElement>(null);

  function scroll() {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }

  async function send(text: string) {
    if (busy || !text.trim()) return;
    setBusy(true);
    setQ("");
    setRows((r) => [...r, { who: "me", text }, { who: "bot", state: "thinking" }]);
    requestAnimationFrame(scroll);

    const qs = `?q=${encodeURIComponent(text)}&brand=${encodeURIComponent(brand ?? "")}`;
    const fast = getJson<Routed>(`/ask.json${qs}`).catch(() => null);
    let prose: Prose | null = null, failure: string | null = null;
    try {
      prose = await getJson<Prose>(`/ask/prose.json${qs}`);
    } catch (e) {
      failure = e instanceof Error ? e.message : "Could not reach the server for the written answer.";
    }
    const routed = await fast;
    setRows((r) => [...r.slice(0, -1), { who: "bot", state: "done", prose, routed, failure }]);
    setBusy(false);
    requestAnimationFrame(scroll);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void send(q);
  }

  return (
    <section className="card ask" id="ask" aria-labelledby="ask-t">
      <div className="card-head">
        <h2 className="card-t" id="ask-t">Ask</h2>
        <span className="more">read-only</span>
      </div>
      <div className="n">Routes your question to one of the same verbs this page calls.
        It reports; it does not conclude.</div>

      <div className="ask-log" id="ask-log" ref={log} aria-live="polite" aria-atomic="false">
        <div className="ask-row">
          <span className="ask-orb" aria-hidden="true" />
          <div className="ask-bubble">
            Try <em>why did CPA move</em>, <em>what is fatiguing</em>, or
            <em> which angles have we never tested</em>. Add a window like
            <em> last 14 days</em> and it will use it.
          </div>
        </div>
        {rows.map((row, i) => (
          <div key={i} className={`ask-row${row.who === "me" ? " me" : ""}`}>
            {row.who !== "me" && <span className="ask-orb" aria-hidden="true" />}
            <Bubble row={row} />
          </div>
        ))}
      </div>

      <div className="ask-sugg">
        {SUGGESTIONS.map(([ask, label]) => (
          <button key={ask} type="button" onClick={() => void send(ask)} disabled={busy}>{label}</button>
        ))}
      </div>

      <form className="ask-form" id="ask-form" onSubmit={submit}>
        <Icon name="ask" />
        <label className="sr-only" htmlFor="ask-q">Ask a question</label>
        <input id="ask-q" name="q" type="text" autoComplete="off" value={q}
               onChange={(e) => setQ(e.target.value)}
               placeholder="Ask about spend, CPA, fatigue, angles…" />
        <button type="submit" aria-label="Ask" disabled={busy}><Icon name="arrow-up" /></button>
      </form>
    </section>
  );
}
