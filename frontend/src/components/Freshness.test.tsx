/* The import-freshness card's sentences, ported from the behaviour static/app.js
 * had. Each is a different claim about the importer, and the button depends
 * on the card telling them apart. */
import { agoShort, describe as describeStatus, shortErr, type RefreshStatus } from "./Freshness";

const base: RefreshStatus = {
  ok: true, brand: "renegade", running: false, stalled: false,
  last_insights_ok: "2026-10-02T08:05:52+00:00", insights_through: "2026-10-02",
  age_hours: 3, settled_through: "2026-10-06", accounts: [], accounts_ok: 1, accounts_failed: 0,
};
const acct = (over: Partial<RefreshStatus["accounts"][number]>) => ({
  platform_account_id: "act_1", label: "Main", status: "ok", error: null, run_id: 7,
  started_at: null, finished_at: null, ...over,
});

describe("agoShort", () => {
  it("says never, minutes, hours, days", () => {
    expect(agoShort(null)).toBe("never");
    expect(agoShort(0.01)).toBe("1 min ago");
    expect(agoShort(0.5)).toBe("30 min ago");
    expect(agoShort(5.4)).toBe("5h ago");
    expect(agoShort(72)).toBe("3 days ago");
  });
});

describe("shortErr", () => {
  it("drops Meta's docs tail and our own appended advice", () => {
    expect(shortErr("Unsupported get request, refer to https://developers.facebook.com/x"))
      .toBe("Unsupported get request.");
    expect(shortErr("Permission denied for details.. Usually the account is not assigned."))
      .toBe("Permission denied for details.");
    expect(shortErr(null)).toBe("no reason recorded.");
  });
  it("cuts a long message at a word, not mid-word", () => {
    const long = Array(40).fill("word").join(" ");
    const out = shortErr(long);
    expect(out.endsWith("…")).toBe(true);
    expect(out.length).toBeLessThanOrEqual(152);
    expect(out).not.toMatch(/wor…$/);
  });
});

describe("describe", () => {
  it("is quiet when the last pull is fresh", () => {
    const v = describeStatus(base);
    expect(v.cls).toBe("");
    expect(v.note).toBe("Insights through 2026-10-02.");
    expect(v.age).toBe("3h ago");
  });
  it("is stale past a day, or with no successful pull", () => {
    expect(describeStatus({ ...base, age_hours: 30 }).cls).toBe("stale");
    const v = describeStatus({ ...base, age_hours: null, insights_through: null });
    expect(v.cls).toBe("stale");
    expect(v.note).toBe("No successful insights pull yet.");
    expect(v.age).toBe("never");
  });
  it("names an interrupted run and says nothing is running now", () => {
    const v = describeStatus({ ...base, accounts: [acct({ status: "stalled", run_id: 250 })] });
    expect(v.cls).toBe("broken");
    expect(v.noteErr).toBe(true);
    expect(v.note).toContain("(run 250). Nothing is running now.");
  });
  it("one account failing is not the brand failing", () => {
    const v = describeStatus({
      ...base, accounts_ok: 1, accounts_failed: 1,
      accounts: [acct({}), acct({ platform_account_id: "act_2", label: null, status: "failed",
                                 error: "Permission denied, refer to https://x" })],
    });
    expect(v.cls).toBe("stale");
    expect(v.noteErr).toBe(false);
    expect(v.note).toContain("1 of 2 account(s) failed — act_2: Permission denied.");
    expect(v.title).toBe("Permission denied, refer to https://x");
  });
  it("is broken only when nothing imported", () => {
    const v = describeStatus({ ...base, accounts_ok: 0, accounts_failed: 1,
                               accounts: [acct({ status: "failed", error: "down" })] });
    expect(v.cls).toBe("broken");
    expect(v.noteErr).toBe(true);
  });
});
