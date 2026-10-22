import { describe, expect, it } from "vitest";

import { hiddenBySwitch } from "@/components/app/AppNav";

describe("导航里不显示用户自己关掉的玩法（GLOSSARY.md「玩法开关」）", () => {
  it("关掉日记、来信时不显示那一项，开着或部署没有时照旧", () => {
    expect(hiddenBySwitch("diary", { diary: false })).toBe(true);
    expect(hiddenBySwitch("letters", { letters: false, diary: true })).toBe(true);
    expect(hiddenBySwitch("diary", { diary: true })).toBe(false);
    expect(hiddenBySwitch("letters", {})).toBe(false);
    expect(hiddenBySwitch("settings", { diary: false })).toBe(false);
    expect(hiddenBySwitch("diary", undefined)).toBe(false);
  });
});
