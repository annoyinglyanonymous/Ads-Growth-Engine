import { brandOf, getJson, HttpError, staleServer, withParams } from "./api";

describe("withParams", () => {
  it("adds, replaces and removes query parameters, keeping the rest", () => {
    expect(withParams("?brand=renegade&days=7", { days: 28 })).toBe("?brand=renegade&days=28");
    expect(withParams("?brand=renegade", { level: "ad_group" })).toBe("?brand=renegade&level=ad_group");
    expect(withParams("?brand=renegade&goal=X", { goal: null })).toBe("?brand=renegade");
    expect(withParams("?goal=X", { goal: "" })).toBe("");
  });
});

describe("brandOf", () => {
  it("reads the brand or falls back to the default", () => {
    expect(brandOf("?brand=agency")).toBe("agency");
    expect(brandOf("")).toBe("renegade");
    expect(brandOf("?brand=")).toBe("renegade");
  });
});

describe("getJson", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("names a stale server on 404 rather than a broken feature", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    await expect(getJson("/refresh/status?brand=x")).rejects.toThrow(staleServer("/refresh/status"));
    await expect(getJson("/refresh/status?brand=x")).rejects.toBeInstanceOf(HttpError);
  });

  it("prefers the server's own sentence on another error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ ok: false, error: "ADS_DATABASE_URL_RO is not set." }), { status: 503 })));
    await expect(getJson("/api/why")).rejects.toThrow("ADS_DATABASE_URL_RO is not set.");
  });

  it("asks for JSON", async () => {
    const fetch = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await getJson("/api/");
    expect((fetch.mock.calls[0] as unknown[])[1]).toMatchObject({ headers: { Accept: "application/json" } });
  });
});
