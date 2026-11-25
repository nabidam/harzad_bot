// api/client.ts is the single fetch site (v2 spec §5). These tests run in
// node, where the Telegram SDK is absent — the initData comes through the
// setTestInitData() seam (theme.ts).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, coverSrc, jobAlive, withInit } from "../api/client";
import { setTestInitData } from "../theme";

interface Call {
  url: string;
  options: RequestInit | undefined;
}

let calls: Call[] = [];
let nextResponses: Array<{ ok: boolean; status: number; json: unknown }> = [];

function mockFetch() {
  calls = [];
  nextResponses = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      calls.push({ url, options });
      const r = nextResponses.shift();
      if (!r) throw new Error("no mock response queued");
      return {
        ok: r.ok,
        status: r.status,
        json: async () => r.json,
      } as Response;
    }),
  );
}

function queue(ok: boolean, status: number, json: unknown) {
  nextResponses.push({ ok, status, json });
}

beforeEach(() => {
  mockFetch();
  setTestInitData(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("withInit (GET initData transport)", () => {
  it("returns the url unchanged without initData", () => {
    expect(withInit("/api/library?page=1")).toBe("/api/library?page=1");
  });

  it("appends initData with ? when the url has no query", () => {
    setTestInitData("user_id=1");
    expect(withInit("/api/library")).toBe(
      "/api/library?initData=" + encodeURIComponent("user_id=1"),
    );
  });

  it("appends initData with & when the url already has a query", () => {
    setTestInitData("user_id=1&auth_date=2");
    expect(withInit("/api/library?view=latest&page=1")).toBe(
      "/api/library?view=latest&page=1&initData=" +
        encodeURIComponent("user_id=1&auth_date=2"),
    );
  });
});

describe("api() POST (body initData transport)", () => {
  it("merges initData into the JSON body, empty string outside Telegram", async () => {
    queue(true, 200, { user: null });
    const data = await api("/api/auth", {});
    expect(calls).toHaveLength(1);
    const c = calls[0];
    expect(c.url).toBe("/api/auth");
    expect(c.options?.method).toBe("POST");
    const body = JSON.parse(c.options?.body as string);
    expect(body.initData).toBe("");
    expect(data).toEqual({ user: null });
  });

  it("keeps the caller's body keys alongside initData", async () => {
    setTestInitData("auth");
    queue(true, 200, { reply: "hi" });
    await api("/api/chat", { messages: [{ role: "user", content: "yo" }] });
    const body = JSON.parse(calls[0].options?.body as string);
    expect(body.initData).toBe("auth");
    expect(body.messages).toEqual([{ role: "user", content: "yo" }]);
  });
});

describe("api() GET", () => {
  it("sends the path untouched (caller adds initData via withInit)", async () => {
    queue(true, 200, { state: "done" });
    setTestInitData("auth");
    await api(withInit("/api/jobs/abc123"));
    const c = calls[0];
    expect(c.options?.method).toBe("GET");
    expect(c.url).toBe("/api/jobs/abc123?initData=" + encodeURIComponent("auth"));
  });
});

describe("ApiError", () => {
  it("carries the server's error message and status", async () => {
    queue(false, 401, { error: "The library requires Telegram auth." });
    let caught: unknown;
    try {
      await api("/api/library");
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).message)
      .toBe("The library requires Telegram auth.");
    expect((caught as ApiError).status).toBe(401);
  });

  it("falls back to HTTP <status> when the body has no error", async () => {
    queue(false, 503, {});
    await expect(api("/api/library")).rejects.toThrow("HTTP 503");
  });
});

describe("jobAlive", () => {
  it("true while the job row exists (no initData — the uuid4 id is the capability)", async () => {
    setTestInitData("auth");
    queue(true, 200, { state: "done" });
    await expect(jobAlive("abc123")).resolves.toBe(true);
    expect(calls[0].url).toBe("/api/jobs/abc123");
  });

  it("false after a day of pruning (404)", async () => {
    queue(false, 404, { error: "Not found" });
    await expect(jobAlive("old")).resolves.toBe(false);
  });
});

describe("coverSrc", () => {
  it("null for tracks without a cover", () => {
    expect(coverSrc({ track_key: "a_b", has_cover: false })).toBeNull();
  });

  it("proxies the cover with initData", () => {
    setTestInitData("auth");
    expect(coverSrc({ track_key: "a b", has_cover: true })).toBe(
      "/api/library/cover/" + encodeURIComponent("a b") +
        "?initData=" + encodeURIComponent("auth"),
    );
  });
});
