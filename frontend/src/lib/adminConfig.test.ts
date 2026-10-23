import { describe, expect, it } from "vitest";

import {
  changeOf,
  CONFIG_TABS,
  CUSTOM_MODEL,
  formOf,
  exemptingKeysOf,
  ITEM_SPECS,
  lockedOn,
  modelChoice,
  PROXIED_SERVICES,
  UNLIMITED,
  readOnlyValue,
  rows,
  statusDisplay,
  TAB_ITEMS,
  tabFromQuery,
  tabHasFailure,
  tabHref,
  type AdminConfig,
  type ConfigItem,
  type Shown,
} from "@/lib/adminConfig";
import { EFFORT_UNSET, effortChoices, REASONING_EFFORTS } from "@/lib/reasoningEffort";

const ENV: Shown = {
  protocol: "chat-completions",
  base_url: "https://env.example/v1",
  model: "env-model",
  max_tokens: null,
  reasoning_effort: null,
  extra_body: null,
  api_key_last4: "aaaa",
};
const OVERRIDE: Shown = { model: "new-model", api_key_last4: "bbbb" };

function item(status: ConfigItem["status"]): ConfigItem {
  return {
    status,
    env: ENV,
    effective: status === "overridden" ? { ...ENV, ...OVERRIDE } : ENV,
    override: status === "env" ? null : OVERRIDE,
    failure: status === "failed" ? "401 key revoked" : null,
  };
}

describe("statusDisplay", () => {
  it("marks only the overridden and failed states, restorable once overridden, with why it is not in effect", () => {
    expect(statusDisplay(item("env"))).toEqual({ variant: null, canRestore: false, failure: null });
    expect(statusDisplay(item("overridden"))).toEqual({ variant: "accent", canRestore: true, failure: null });
    expect(statusDisplay(item("failed"))).toEqual({
      variant: "warning",
      canRestore: true,
      failure: "401 key revoked",
    });
  });
});

describe("rows", () => {
  it("marks the overridden fields, in effect only while the override is, a key by its last 4", () => {
    for (const status of ["overridden", "failed"] as const) {
      const byField = Object.fromEntries(rows(item(status), ITEM_SPECS.chat_llm).map((row) => [row.field, row]));
      const inEffect = status === "overridden";
      expect(byField.model, status).toEqual({
        field: "model",
        env: "env-model",
        effective: inEffect ? "new-model" : "env-model",
        overridden: true,
        override: "new-model",
        inEffect,
      });
      expect(byField.api_key, status).toEqual({
        field: "api_key",
        env: "aaaa",
        effective: inEffect ? "bbbb" : "aaaa",
        overridden: true,
        override: "bbbb",
        inEffect,
      });
      expect(byField.max_tokens, status).toEqual({
        field: "max_tokens",
        env: null,
        effective: null,
        overridden: false,
        override: null,
        inEffect: false,
      });
    }
  });

  it("shows each key of speech recognition by the service it is for", () => {
    const stt: ConfigItem = {
      status: "overridden",
      env: { provider: "soniox", soniox_api_key_last4: "1111", dashscope_api_key_last4: null, region: null },
      effective: { provider: "bailian", soniox_api_key_last4: "1111", dashscope_api_key_last4: "2222", region: "beijing" },
      override: { provider: "bailian", dashscope_api_key_last4: "2222", region: "beijing" },
      failure: null,
    };

    const byField = Object.fromEntries(rows(stt, ITEM_SPECS.stt).map((row) => [row.field, row]));

    expect(byField.soniox_api_key).toMatchObject({ effective: "1111", overridden: false });
    expect(byField.dashscope_api_key).toMatchObject({ env: null, effective: "2222", overridden: true });
  });
});

describe("the chat model's two efforts", () => {
  const efforts = (spec: (typeof ITEM_SPECS)[keyof typeof ITEM_SPECS]) =>
    spec.form.filter(({ kind }) => kind === "effort").map(({ name }) => name);

  it("has the chat model show and take an effort for calls after the one for text, the memory model only its one", () => {
    const fields = rows(item("env"), ITEM_SPECS.chat_llm).map((row) => row.field);
    expect(fields.slice(fields.indexOf("reasoning_effort"), fields.indexOf("reasoning_effort") + 2)).toEqual([
      "reasoning_effort",
      "call_reasoning_effort",
    ]);
    expect(efforts(ITEM_SPECS.chat_llm)).toEqual(["reasoning_effort", "call_reasoning_effort"]);
    expect(rows(item("env"), ITEM_SPECS.memory_llm).map((row) => row.field)).not.toContain("call_reasoning_effort");
    expect(efforts(ITEM_SPECS.memory_llm)).toEqual(["reasoning_effort"]);
  });
});

describe("formOf and changeOf", () => {
  const llm = ITEM_SPECS.chat_llm;
  const form = { ...formOf(llm, { ...ENV, max_tokens: 1024 }), api_key: " sk-new " };

  it("starts from the values in effect, the key left empty", () => {
    expect(formOf(llm, { ...ENV, max_tokens: 1024 })).toEqual({
      protocol: "chat-completions",
      base_url: "https://env.example/v1",
      model: "env-model",
      max_tokens: "1024",
      reasoning_effort: EFFORT_UNSET,
      call_reasoning_effort: EFFORT_UNSET,
      extra_body: "",
      api_key: "",
    });
  });

  it("sends the key only when it can be changed and was typed", () => {
    expect(changeOf(llm, form, true)).toEqual({
      change: {
        protocol: "chat-completions",
        base_url: "https://env.example/v1",
        model: "env-model",
        max_tokens: 1024,
        reasoning_effort: null,
        call_reasoning_effort: null,
        extra_body: null,
        api_key: "sk-new",
      },
    });
    expect(changeOf(llm, form, false)).toEqual({ change: expect.not.objectContaining({ api_key: expect.anything() }) });
    expect(changeOf(llm, { ...form, api_key: "  " }, true)).toEqual({
      change: expect.not.objectContaining({ api_key: expect.anything() }),
    });
  });

  it("takes an empty max_tokens as the default and names what keeps the form from being saved", () => {
    expect(changeOf(llm, { ...form, max_tokens: "" }, true)).toMatchObject({ change: { max_tokens: null } });
    for (const bad of ["0", "-1", "1.5", "abc"]) {
      expect(changeOf(llm, { ...form, max_tokens: bad }, true), bad).toEqual({
        problems: [{ field: "max_tokens", reason: "maxTokens" }],
      });
    }
    expect(changeOf(llm, { ...form, model: " " }, true)).toEqual({ problems: [{ field: "model", reason: "required" }] });
  });

  it("names every problem at once, in the form's order", () => {
    expect(changeOf(llm, { ...form, base_url: "", model: " ", max_tokens: "0" }, true)).toEqual({
      problems: [
        { field: "base_url", reason: "required" },
        { field: "model", reason: "required" },
        { field: "max_tokens", reason: "maxTokens" },
      ],
    });
  });

  it("starts from the effort and the extra body in effect, the extra body as indented JSON", () => {
    const effective = { ...ENV, reasoning_effort: "high", extra_body: { thinking: { type: "disabled" } } };
    expect(formOf(llm, effective)).toMatchObject({
      reasoning_effort: "high",
      extra_body: ["{", '  "thinking": {', '    "type": "disabled"', "  }", "}"].join("\n"),
    });
  });

  it("sends the effort chosen, or none when it is unset", () => {
    expect(changeOf(llm, { ...form, reasoning_effort: "none" }, true)).toMatchObject({ change: { reasoning_effort: "none" } });
    expect(changeOf(llm, { ...form, reasoning_effort: EFFORT_UNSET }, true)).toMatchObject({
      change: { reasoning_effort: null },
    });
  });

  it("refuses an effort the protocol has no way to write: Anthropic has no minimal", () => {
    expect(changeOf(llm, { ...form, reasoning_effort: "minimal" }, true)).toMatchObject({
      change: { reasoning_effort: "minimal" },
    });
    expect(changeOf(llm, { ...form, protocol: "anthropic", reasoning_effort: "minimal" }, true)).toEqual({
      problems: [{ field: "reasoning_effort", reason: "effort" }],
    });
    expect(changeOf(llm, { ...form, protocol: "anthropic", reasoning_effort: "max" }, true)).toMatchObject({
      change: { reasoning_effort: "max" },
    });
  });

  it("sends the extra body as the JSON object typed, none when left empty", () => {
    expect(changeOf(llm, { ...form, extra_body: ' {"enable_thinking": false} ' }, true)).toMatchObject({
      change: { extra_body: { enable_thinking: false } },
    });
    expect(changeOf(llm, { ...form, extra_body: "  " }, true)).toMatchObject({ change: { extra_body: null } });
  });

  it("refuses an extra body that is not JSON, or not an object", () => {
    expect(changeOf(llm, { ...form, extra_body: "{enable_thinking: false}" }, true)).toEqual({
      problems: [{ field: "extra_body", reason: "json" }],
    });
    for (const bad of ["[1]", '"x"', "3", "null"]) {
      expect(changeOf(llm, { ...form, extra_body: bad }, true), bad).toEqual({
        problems: [{ field: "extra_body", reason: "jsonObject" }],
      });
    }
  });

  it("takes thresholds from 0 to 1 and seconds above 0", () => {
    const thresholds = ITEM_SPECS.judge_thresholds;
    const affinity = { out_of_line: "0.7", manipulation: "1", photo: "0.8" };
    expect(changeOf(thresholds, { recall: "0.7", emotion: "0", ...affinity }, true)).toEqual({
      change: { recall: 0.7, emotion: 0, out_of_line: 0.7, manipulation: 1, photo: 0.8 },
    });
    for (const bad of ["1.1", "-0.1", "", "high"]) {
      const others = { recall: "0.5", emotion: "0.5", manipulation: "0.5", photo: "0.5" };
      expect(changeOf(thresholds, { ...others, out_of_line: bad }, true), bad).toEqual({
        problems: [{ field: "out_of_line", reason: "probability" }],
      });
    }
    const judge = { provider: "jev", model: "typesafe/jev-1.13", account_id: "", api_key: "" };
    expect(changeOf(ITEM_SPECS.judge, { ...judge, emotion_timeout: "0" }, true)).toEqual({
      problems: [{ field: "emotion_timeout", reason: "seconds" }],
    });
    expect(changeOf(ITEM_SPECS.judge, { ...judge, emotion_timeout: "1.5" }, true)).toEqual({
      change: { provider: "jev", model: "typesafe/jev-1.13", emotion_timeout: 1.5 },
    });
    // 百炼的业务空间和地域：没填、没选的不发，由服务器沿用或取默认的北京。
    const bailian = { ...judge, provider: "bailian", model: "decision-model-preview", emotion_timeout: "1.5" };
    expect(changeOf(ITEM_SPECS.judge, { ...bailian, workspace_id: " llm-ws1 ", region: "singapore" }, true)).toEqual({
      change: {
        provider: "bailian", model: "decision-model-preview", workspace_id: "llm-ws1", region: "singapore", emotion_timeout: 1.5,
      },
    });
    expect(changeOf(ITEM_SPECS.judge, { ...bailian, workspace_id: "", region: "" }, true)).toEqual({
      change: { provider: "bailian", model: "decision-model-preview", emotion_timeout: 1.5 },
    });
  });
});

describe("limits", () => {
  const limits = ITEM_SPECS.limits;
  const form = {
    turns_per_hour: "2",
    calls_per_hour: UNLIMITED,
    photos_per_hour: "5",
    call_minutes: "10",
    turns_per_call: "40",
    concurrent_calls: "10",
    history_turns: "20",
    exempting_llm: "true",
    exempting_tts: "false",
    exempting_stt: "true",
  };

  it("starts a limit of 0 as none, not as a number", () => {
    expect(formOf(limits, { ...form, calls_per_hour: 0, turns_per_hour: 2 }).calls_per_hour).toBe(UNLIMITED);
    expect(formOf(limits, { ...form, calls_per_hour: 0, turns_per_hour: 2 }).turns_per_hour).toBe("2");
  });

  it("takes whole numbers from 1 up, and sends none as 0", () => {
    expect(changeOf(limits, form, true)).toEqual({
      change: {
        turns_per_hour: 2,
        calls_per_hour: 0,
        photos_per_hour: 5,
        call_minutes: 10,
        turns_per_call: 40,
        concurrent_calls: 10,
        history_turns: 20,
        exempting_llm: true,
        exempting_tts: false,
        exempting_stt: true,
      },
    });
  });

  it("never has image generation among the exempting keys, though users may bring their own", () => {
    expect(exemptingKeysOf({ exempting_llm: true, exempting_tts: true, exempting_stt: true, exempting_image: true })).toEqual([
      "llm",
      "tts",
      "stt",
    ]);
  });

  it("reads which services are the exempting keys from the effective limits", () => {
    expect(exemptingKeysOf({ turns_per_hour: 60, exempting_llm: true, exempting_tts: false, exempting_stt: true })).toEqual([
      "llm",
      "stt",
    ]);
    expect(exemptingKeysOf({ exempting_llm: false, exempting_tts: true, exempting_stt: false })).toEqual(["tts"]);
  });

  it("keeps the last exempting key on from being turned off, and no other switch", () => {
    const llmAlone = { ...form, exempting_stt: "false" };
    expect(lockedOn(llmAlone, "exempting_llm")).toBe(true);
    expect(lockedOn(llmAlone, "exempting_tts")).toBe(false); // off: it can be turned on
    expect(lockedOn(form, "exempting_llm")).toBe(false); // another is on
    expect(lockedOn({ ...llmAlone, enabled: "true" }, "enabled")).toBe(false);
  });

  it("refuses one that is 0, negative, not whole or empty", () => {
    for (const bad of ["0", "-1", "1.5", "", "十"]) {
      expect(changeOf(limits, { ...form, calls_per_hour: bad }, true), bad).toEqual({
        problems: [{ field: "calls_per_hour", reason: "limit" }],
      });
    }
  });
});

describe("effortChoices", () => {
  it("lists unset first, then every effort the protocol can write, in order", () => {
    expect(effortChoices("chat-completions")).toEqual([EFFORT_UNSET, ...REASONING_EFFORTS]);
    expect(effortChoices("anthropic")).toEqual([EFFORT_UNSET, "none", "low", "medium", "high", "xhigh", "max"]);
  });
});

describe("readOnlyValue", () => {
  const words = { set: "已设置", unset: "未设置", none: "—" };

  it("says a setting is set, never what it is, and lists the parts of the others", () => {
    expect(readOnlyValue(true, words)).toBe("已设置");
    expect(readOnlyValue(false, words)).toBe("未设置");
    expect(readOnlyValue(["github:1001", "google:g-1"], words)).toBe("github:1001、google:g-1");
    expect(readOnlyValue([], words)).toBe("—");
    expect(readOnlyValue(null, words)).toBe("—");
    expect(readOnlyValue({ ice_servers: ["stun:a"], turn: [] }, words)).toBe("ice_servers：stun:a；turn：—");
  });
});

describe("tried", () => {
  it("is set for the items whose change the server tries, not for thresholds and limits", () => {
    const tried = Object.entries(ITEM_SPECS)
      .filter(([, spec]) => spec.tried)
      .map(([name]) => name);
    expect(tried).toEqual(["chat_llm", "memory_llm", "tts", "stt", "judge", "image", "proxy"]);
  });
});

describe("proxy", () => {
  const proxy = ITEM_SPECS.proxy;
  const effective: Shown = {
    enabled: true,
    url: "http://me@127.0.0.1:7890",
    password_last4: "cret",
    ...Object.fromEntries(PROXIED_SERVICES.map((service) => [service, service === "chat_llm"])),
  };

  it("starts from the switches and the address in effect, the password left empty", () => {
    const form = formOf(proxy, effective);
    expect(form).toMatchObject({ enabled: "true", url: "http://me@127.0.0.1:7890", password: "", chat_llm: "true", tts: "false" });
  });

  it("sends every switch and the address, an emptied address as none, the password only when typed", () => {
    const form = { ...formOf(proxy, effective), url: "  ", tts: "true" };
    expect(changeOf(proxy, form, true)).toEqual({
      change: {
        enabled: true,
        url: null,
        ...Object.fromEntries(PROXIED_SERVICES.map((service) => [service, service === "chat_llm" || service === "tts"])),
      },
    });
    const typed = changeOf(proxy, { ...form, url: "socks5://127.0.0.1:7891", password: "secret" }, true);
    expect(typed).toMatchObject({ change: { url: "socks5://127.0.0.1:7891", password: "secret" } });
  });

  it("shows the password by its last 4, with a switch for every service", () => {
    const shown = rows({ status: "env", env: effective, effective, override: null, failure: null }, proxy);
    expect(shown.map((row) => row.field)).toEqual(["enabled", "url", "password", ...PROXIED_SERVICES]);
    expect(shown.find((row) => row.field === "password")?.effective).toBe("cret");
  });
});

describe("image", () => {
  const image = ITEM_SPECS.image;
  const effective: Shown = {
    api_format: "novelai",
    base_url: "https://image.novelai.net",
    api_key_last4: "1234",
    model: "nai-diffusion-5-curated",
    concurrency: 1,
    style: "",
  };
  const form = formOf(image, effective);

  it("starts from the values in effect, the key left empty", () => {
    expect(form).toEqual({
      api_format: "novelai",
      base_url: "https://image.novelai.net",
      model: "nai-diffusion-5-curated",
      concurrency: "1",
      style: "",
      api_key: "",
    });
  });

  it("sends the format, the address, the model typed, how many at a time and the art style, an empty one as none", () => {
    const typed = { ...form, api_format: "openai", base_url: " https://relay.example/v1 ", model: " relay-model ", concurrency: "3" };
    expect(changeOf(image, { ...typed, style: " 0.7::artist:example:: ", api_key: "pst-new" }, true)).toEqual({
      change: {
        api_format: "openai",
        base_url: "https://relay.example/v1",
        model: "relay-model",
        concurrency: 3,
        style: "0.7::artist:example::",
        api_key: "pst-new",
      },
    });
    expect(changeOf(image, typed, true)).toMatchObject({ change: { style: null } });
  });

  it("takes from 1 to 10 at a time, and a model only if one is typed", () => {
    for (const bad of ["0", "11"]) {
      expect(changeOf(image, { ...form, concurrency: bad }, true), bad).toEqual({
        problems: [{ field: "concurrency", reason: "count" }],
      });
    }
    expect(changeOf(image, { ...form, model: " " }, true)).toEqual({ problems: [{ field: "model", reason: "required" }] });
  });

  it("chooses a listed model from the list, any other as typed in by hand", () => {
    const listed = image.form.find((field) => field.name === "model")?.options ?? [];
    expect(modelChoice(listed, "nai-diffusion-4-5-full")).toBe("nai-diffusion-4-5-full");
    expect(modelChoice(listed, "relay-model")).toBe(CUSTOM_MODEL);
  });
});

describe("reach-outs", () => {
  const spec = ITEM_SPECS.reach_outs;

  it("send the switch and how many a day, a whole number from 1 up", () => {
    const form = formOf(spec, { enabled: false, per_day: 3 });
    expect(form).toEqual({ enabled: "false", per_day: "3" });
    expect(changeOf(spec, { enabled: "true", per_day: " 12 " }, true)).toEqual({
      change: { enabled: true, per_day: 12 },
    });
    for (const bad of ["0", "", "1.5", "-2"]) {
      expect(changeOf(spec, { ...form, per_day: bad }, true), bad).toEqual({
        problems: [{ field: "per_day", reason: "positive" }],
      });
    }
  });
});

describe("tabs", () => {
  it("put every item under exactly one tab", () => {
    const placed = CONFIG_TABS.flatMap((tab) => TAB_ITEMS[tab]);
    expect([...placed].sort()).toEqual(Object.keys(ITEM_SPECS).sort());
  });

  it("take a tab from the address and fall back to the models", () => {
    expect(tabFromQuery("voice")).toBe("voice");
    expect(tabFromQuery("network")).toBe("network");
    for (const value of [undefined, "nope"]) expect(tabFromQuery(value), String(value)).toBe("models");
  });

  it("leave the default tab out of the address", () => {
    expect(tabHref("/admin/config", "models")).toBe("/admin/config");
    expect(tabHref("/admin/config", "read_only")).toBe("/admin/config?tab=read_only");
  });

  it("mark a tab holding an override not in effect, not one holding an unavailable item", () => {
    const config = {
      ...Object.fromEntries(Object.keys(ITEM_SPECS).map((name) => [name, item("env")])),
      stt: item("failed"),
      judge: { unavailable: "没有配置判断模型" },
    } as unknown as AdminConfig;
    expect(CONFIG_TABS.filter((tab) => tabHasFailure(config, tab))).toEqual(["voice"]);
  });
});
