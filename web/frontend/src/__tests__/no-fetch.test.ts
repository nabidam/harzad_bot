// Structural pin (v2 spec §5): fetch is centralized in api/client.ts so
// initData injection cannot regress per-call. Any other fetch site — or an
// API call that forgets initData — breaks this test.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) out.push(...walk(p));
    else if (/\.(ts|tsx)$/.test(name)) out.push(p);
  }
  return out;
}

describe("fetch centralization", () => {
  const files = walk(SRC);

  it("fetch() appears only in api/client.ts", () => {
    const offenders = files.filter(
      (f) =>
        f.endsWith("client.ts") === false &&
        f.includes("__tests__") === false &&
        /(^|[^.\w])fetch\s*\(/.test(readFileSync(f, "utf8")),
    );
    expect(offenders).toEqual([]);
  });

  it("the fetch site carries the initData + encoding machinery", () => {
    const client = readFileSync(join(SRC, "api", "client.ts"), "utf8");
    expect(client).toContain("initData");
    expect(client).toContain("encodeURIComponent");
  });

  it("the app code handles the auth failure vocabulary", () => {
    // The 401/503 branches in music.ts map server errors to the no-auth and
    // unavailable state boxes — both wordings must stay in the tree.
    const music = readFileSync(join(SRC, "music.ts"), "utf8");
    expect(music).toContain("no-auth");
    expect(music).toMatch(/Telegram\|Unauthorized/);
  });
});
