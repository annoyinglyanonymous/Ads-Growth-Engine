/* Import freshness, and the one button in this app.
 *
 * Ported from static/app.js. POST /refresh starts a pull in a SEPARATE process
 * and returns at once, so there is nothing to await here -- the button fires
 * and the poll is what tells you how it went. Closing the tab is harmless: the
 * run is not attached to this page, or to this request, or to this browser.
 * ui.py's block above the endpoints has the long version.
 *
 * The poll does not reload the page on its own. New numbers arriving under a
 * reader mid-sentence is a rudeness; when a run finishes, the button offers a
 * reload and the reader decides.
 *
 * A SECOND card rather than two more lines in the first, because the two dates
 * answer different questions: the settled edge is a fact about Meta and this is
 * a fact about us (when the importer last succeeded).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getJson, HttpError } from "../lib/api";

export interface RefreshAccount {
  platform_account_id: string;
  label: string | null;
  status: string | null;
  error: string | null;
  run_id: number | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface RefreshStatus {
  ok: boolean;
  error?: string;
  brand: string;
  running: boolean;
  stalled: boolean;
  last_insights_ok: string | null;
  insights_through: string | null;
  age_hours: number | null;
  settled_through: string | null;
  accounts: RefreshAccount[];
  accounts_ok: number;
  accounts_failed: number;
}

/** "never", "N min ago", "Nh ago", "N days ago" -- app.js's version, which
 *  rounds half-up like the browser does; nothing in prose repeats it. */
export function agoShort(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "never";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min ago`;
  if (hours < 48) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

/** Meta's messages are one long sentence plus our own advice appended. The
 *  first sentence is the part that says what is actually wrong, and the rail
 *  is 180px wide. The whole text goes in the title attribute. */
export function shortErr(text: string | null | undefined): string {
  let t = String(text || "no reason recorded").replace(/\s+/g, " ").trim();
  t = t.split(", refer to ")[0].split(".. ")[0];
  if (t.length <= 150) return t.replace(/[.,;:]+$/, "") + ".";
  const cut = t.lastIndexOf(" ", 150);
  return t.slice(0, cut > 40 ? cut : 150) + "…";
}

type View = {
  cls: "" | "stale" | "broken";
  age: string;
  note: string;
  noteErr: boolean;
  title: string;
};

/** What the card says for a status reply. Exported so the sentences can be
 *  tested without a timer. */
export function describe(d: RefreshStatus): View {
  const through = d.insights_through
    ? `Insights through ${d.insights_through}.`
    : "No successful insights pull yet.";
  const failed = (d.accounts || []).filter((a) => a.status === "failed");
  const stuck = (d.accounts || []).filter((a) => a.status === "stalled");
  const age = agoShort(d.age_hours);

  // `stalled` is a row that still says `running` long after anything could
  // still be running. Reporting it as "pulling" is how a button greys itself
  // out forever, so it gets its own sentence and does not block the button.
  if (stuck.length) {
    return {
      cls: "broken", age, noteErr: true, title: "",
      note: `${through} A previous run was interrupted and never closed (run ${stuck[0].run_id}). Nothing is running now.`,
    };
  }
  // One account failing is not the brand failing. Red only when NOTHING imported.
  if (failed.length) {
    const who = failed.map((a) => a.label || a.platform_account_id).join(", ");
    const everything = d.accounts_ok === 0;
    return {
      cls: everything ? "broken" : "stale", age, noteErr: everything,
      title: failed[0].error || "",
      note: `${through} ${failed.length} of ${d.accounts.length} account(s) failed — ${who}: ${shortErr(failed[0].error)}`,
    };
  }
  return {
    cls: d.age_hours === null || d.age_hours > 24 ? "stale" : "",
    age, note: through, noteErr: false, title: "",
  };
}

export function Freshness({ brand }: { brand: string | null }) {
  const [view, setView] = useState<View | null>(null);
  const [running, setRunning] = useState(false);
  const [btn, setBtn] = useState<"idle" | "starting" | "reload">("idle");
  const wasRunning = useRef(false);
  const finished = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const qs = `?brand=${encodeURIComponent(brand ?? "")}`;

  const poll = useCallback(async () => {
    try {
      const d = await getJson<RefreshStatus>(`/refresh/status${qs}`);
      if (!d.ok) throw new Error(d.error || "Could not read import status.");
      if (d.running) {
        setRunning(true);
        wasRunning.current = true;
        setView({ cls: "", age: "pulling…", noteErr: false, title: "",
                  note: "An insights pull is running. This takes a few seconds." });
      } else {
        setRunning(false);
        // A run that just finished under this page. Offer the reload rather
        // than taking it -- the numbers on screen are still the old ones.
        if (wasRunning.current && !finished.current) {
          finished.current = true;
          setBtn("reload");
        } else if (!finished.current) {
          setBtn("idle");
        }
        setView(describe(d));
      }
      window.clearTimeout(timer.current);
      // Two seconds while something is running, a minute otherwise.
      timer.current = window.setTimeout(poll, d.running ? 2000 : 60000);
    } catch (e) {
      setRunning(false);
      setView({ cls: "broken", age: "unknown", noteErr: true, title: "",
                note: e instanceof Error ? e.message : "Could not read import status." });
    }
  }, [qs]);

  useEffect(() => {
    wasRunning.current = false;
    finished.current = false;
    setBtn("idle");
    void poll();
    return () => window.clearTimeout(timer.current);
  }, [poll]);

  async function refresh() {
    if (btn === "reload") { window.location.reload(); return; }
    setBtn("starting");
    try {
      const r = await fetch(`/refresh${qs}`, { method: "POST", headers: { Accept: "application/json" } });
      const d = (await r.json()) as { ok: boolean; error?: string };
      // 409 is not an error to apologise for: something else is already doing
      // the thing you asked for. Fall through to the poll, which shows it.
      if (!d.ok && r.status !== 409) throw new Error(d.error || "Could not start the pull.");
      wasRunning.current = true;
      await poll();
    } catch (e) {
      setView((v) => ({
        cls: "broken", age: v?.age ?? "—", noteErr: true, title: "",
        note: e instanceof HttpError || e instanceof Error ? e.message : "Could not start the pull.",
      }));
    } finally {
      setBtn((b) => (b === "starting" ? "idle" : b));
    }
  }

  // Hidden until the first reply, so a server that predates the endpoint shows
  // nothing rather than a card stuck on an em dash.
  if (!view) return null;
  const label = running ? "Refreshing…"
    : btn === "starting" ? "Starting…"
    : btn === "reload" ? "Reload for new numbers"
    : "Refresh now";
  return (
    <div className={`freshness${view.cls ? ` ${view.cls}` : ""}`} id="import-freshness">
      <div className="t">Imported</div>
      <div className="v">{view.age}</div>
      <div className={view.noteErr ? "n err" : "n"} title={view.title || undefined}>{view.note}</div>
      <button type="button" className="refresh-btn" onClick={refresh}
              disabled={running || btn === "starting"}>{label}</button>
    </div>
  );
}
