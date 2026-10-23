// 后台的「配置」页（ADR-0013）：每一项同时有 .env 的值、当前生效的值和后台的覆盖，
// 状态是三种之一：使用 .env 的值、已被后台覆盖、覆盖没有生效（回退到 .env）。
// 各项的字段、表单的换算和校验都写在这里，页面只负责摆放。

import { api, json } from "@/lib/api";
import {
  BAILIAN_REGIONS,
  FISH_AUDIO_MODELS,
  IMAGE_FORMATS,
  IMAGE_MODELS,
  EXEMPTING_SERVICES,
  LLM_PROTOCOLS,
  STT_PROVIDERS,
  type OwnService,
} from "@/lib/localOwnKeys";
import { STATUS_SERVICES } from "@/lib/serviceStatus";
import { EFFORT_UNSET, effortOf, extraBodyText, parseExtraBody, type JsonObject } from "@/lib/reasoningEffort";

export type ConfigStatus = "env" | "overridden" | "failed";

/** 说话的是谁：Fish Audio，或部署者自己的 GPT-SoVITS 服务（GLOSSARY.md「本地语音」）。 */
export const VOICE_ENGINES = ["fish", "gpt_sovits"] as const;

/** 念消息的语音、模型「和通话一样」。 */
export const SAME_AS_CALLS = "same";

export type ItemName =
  | "chat_llm"
  | "memory_llm"
  | "tts"
  | "stt"
  | "image"
  | "judge"
  | "judge_thresholds"
  | "rerank"
  | "limits"
  | "affinity"
  | "reach_outs"
  | "proxy"
  | "service_status";

/** A model's extra request body is a JSON object; every other value is plain. */
type Value = string | number | boolean | null | JsonObject;

/** An item's values as the admin console shows them: of a key, `<field>_last4` only. */
export type Shown = Record<string, Value>;

/** One item of the configuration: `.env`'s value, the one in effect, the fields the
 * admin console overrides (kept even when the override is not in effect), and why not. */
export type ConfigItem = {
  status: ConfigStatus;
  env: Shown;
  effective: Shown;
  override: Shown | null;
  failure: string | null;
  /** The memory model: whether it is the chat model in effect, set apart nowhere. */
  follows_chat?: boolean;
  /** Thresholds: the model they hold for. */
  model?: string;
  /** The proxy and the service status: the services this deployment has not configured, whose switches do nothing. */
  unconfigured?: ProxiedService[];
};

/** An item `.env` does not configure, so there is nothing to change: why. */
export type UnavailableItem = { unavailable: string };

/** What can only be seen here, and why it cannot be changed. */
export type ReadOnlyItem = { name: string; value: unknown; why: string };

export type AdminConfig = {
  /** False without `EIDOLON_SECRET_KEY`: keys can be seen, not changed. */
  keys_editable: boolean;
  read_only: ReadOnlyItem[];
} & { [K in ItemName]: ConfigItem | UnavailableItem };

export function isAvailable(item: ConfigItem | UnavailableItem): item is ConfigItem {
  return !("unavailable" in item);
}

/** The page's tabs, in order. */
export const CONFIG_TABS = ["models", "voice", "image", "network", "status", "limits", "sponsor", "prices", "read_only"] as const;

export type ConfigTab = (typeof CONFIG_TABS)[number];

/** The items under each tab, in the order it shows them. Every item is under exactly one tab;
 * sponsoring, the prices and the read-only items are not items, so their tabs list none and the
 * page puts their cards in itself. */
export const TAB_ITEMS: Record<ConfigTab, ItemName[]> = {
  models: ["chat_llm", "memory_llm", "judge", "judge_thresholds", "rerank"],
  voice: ["tts", "stt"],
  image: ["image"],
  network: ["proxy"],
  status: ["service_status"],
  limits: ["limits", "affinity", "reach_outs"],
  sponsor: [],
  prices: [],
  read_only: [],
};

export function isConfigTab(value: string): value is ConfigTab {
  return (CONFIG_TABS as readonly string[]).includes(value);
}

/** The tab a link asks for in `?tab=`; the models when it asks for none or one not there. */
export function tabFromQuery(value: string | string[] | undefined): ConfigTab {
  return typeof value === "string" && isConfigTab(value) ? value : "models";
}

/** The address of the page showing `tab`: the models, the default, need no query. */
export function tabHref(pathname: string, tab: ConfigTab): string {
  return tab === "models" ? pathname : `${pathname}?tab=${tab}`;
}

/** Whether an override under `tab` is not in effect: the tab is marked, so it is not missed
 * from another one. */
export function tabHasFailure(config: AdminConfig, tab: ConfigTab): boolean {
  return TAB_ITEMS[tab].some((name) => {
    const item = config[name];
    return isAvailable(item) && statusDisplay(item).variant === "warning";
  });
}

/** How an item's status is shown: the StatusDot's variant, whether it can be restored to
 * `.env`'s, and why the override is not in effect, if it is not. */
export type StatusDisplay = {
  /** 状态点的颜色；常态（用 .env 的值）不标，是 null。 */
  variant: "accent" | "warning" | null;
  canRestore: boolean;
  failure: string | null;
};

export function statusDisplay(item: ConfigItem): StatusDisplay {
  switch (item.status) {
    case "env":
      // 用 .env 的值是常态，不标：八项都标同一个点只是噪音（页首说明里讲了没标的就是 .env）。
      // 被覆盖是蓝点，覆盖没生效是黄点。
      return { variant: null, canRestore: false, failure: null };
    case "overridden":
      return { variant: "accent", canRestore: true, failure: null };
    case "failed":
      return { variant: "warning", canRestore: true, failure: item.failure };
  }
}

/** What may each go through the proxy (GLOSSARY.md「网络代理」), in the order shown: the deployer's services, then users'
 * own keys, ticked apart. */
export const PROXIED_SERVICES = [
  "chat_llm",
  "memory_llm",
  "judge",
  "embedding",
  "rerank",
  "tts",
  "stt",
  "image",
  "own_llm",
  "own_tts",
  "own_stt",
  "own_image",
] as const;

export type ProxiedService = (typeof PROXIED_SERVICES)[number];

/** How a field is typed in: a choice, text, a model chosen from a list or typed in by hand (required either way), text
 * that may be emptied (sent as null), a key (left empty to keep it), max_tokens (a
 * positive whole number, empty for the protocol's default), a reasoning effort (one the
 * model's protocol can write, or unset), an extra request body (a JSON object, or empty for
 * none), a probability or score from 0 to 1, a number of seconds, a limit (a whole number
 * from 1 up, or none: sent as 0), how many at a time (a whole number from 1 to `MAX_COUNT`),
 * how many (a whole number from 1 up), a switch (on or off, in the form as "true" or "false"), or minutes chosen from a list (sent as a number). */
export type FieldKind =
  | "select"
  | "text"
  | "modelChoice"
  | "optionalText"
  | "clearableText"
  | "secret"
  | "maxTokens"
  | "effort"
  | "extraBody"
  | "probability"
  | "seconds"
  | "limit"
  | "count"
  | "positive"
  | "switch"
  | "minutes";

/** The most photos the deployer's image key may generate at a time: the backend's `MAX_IMAGE_CONCURRENCY`. */
export const MAX_COUNT = 10;

/** In a model's list, the choice of typing one in by hand. */
export const CUSTOM_MODEL = "custom";

/** What a model's list shows as chosen for `model`: the model itself if listed, else typing one in by hand. */
export function modelChoice(options: readonly string[], model: string): string {
  return options.includes(model) ? model : CUSTOM_MODEL;
}

/** How many of the latest minutes the service status may go by, as the backend takes them. */
export const STATUS_WINDOWS = ["10", "30", "60"] as const;

/** The limits, in the order they are shown. */
export const LIMIT_FIELDS = [
  "turns_per_hour",
  "calls_per_hour",
  "photos_per_hour",
  "call_minutes",
  "turns_per_call",
  "concurrent_calls",
  "history_turns",
];

/** Beside the limits: whether each service is one of the exempting keys (GLOSSARY.md「免限额密钥」), in
 * the order of `EXEMPTING_SERVICES`; at least one stays on. */
export const EXEMPTING_FIELDS = EXEMPTING_SERVICES.map((service) => `exempting_${service}`);

/** The services the limits item has on as exempting keys, in the order of `EXEMPTING_SERVICES`. */
export function exemptingKeysOf(limits: Shown): OwnService[] {
  return EXEMPTING_SERVICES.filter((service) => limits[`exempting_${service}`] === true);
}

export type FieldSpec = { name: string; kind: FieldKind; options?: readonly string[] };

export type ItemSpec = {
  /** The fields the table shows, a key by its name (shown as its last 4 characters). */
  rows: string[];
  secrets: string[];
  form: FieldSpec[];
  /** Whether the server tries a change before keeping it (a service is called); thresholds and limits are only range-checked. */
  tried: boolean;
};

const LLM: ItemSpec = {
  rows: ["protocol", "base_url", "model", "max_tokens", "reasoning_effort", "extra_body", "api_key"],
  secrets: ["api_key"],
  form: [
    { name: "protocol", kind: "select", options: LLM_PROTOCOLS },
    { name: "base_url", kind: "text" },
    { name: "model", kind: "text" },
    { name: "max_tokens", kind: "maxTokens" },
    { name: "reasoning_effort", kind: "effort" },
    { name: "extra_body", kind: "extraBody" },
    { name: "api_key", kind: "secret" },
  ],
  tried: true,
};

/** 对话模型多一项通话的思考强度，紧跟在文字那一项后面；后台模型只有一项。 */
const CHAT_LLM: ItemSpec = {
  ...LLM,
  rows: LLM.rows.flatMap((field) => (field === "reasoning_effort" ? [field, "call_reasoning_effort"] : [field])),
  form: LLM.form.flatMap((field): FieldSpec[] =>
    field.name === "reasoning_effort" ? [field, { name: "call_reasoning_effort", kind: "effort" }] : [field],
  ),
};

const THRESHOLD = (names: string[]): ItemSpec => ({
  rows: names,
  secrets: [],
  form: names.map((name) => ({ name, kind: "probability" })),
  tried: false,
});

export const ITEM_SPECS: Record<ItemName, ItemSpec> = {
  chat_llm: CHAT_LLM,
  memory_llm: LLM,
  // 语音合成：通话和念消息（GLOSSARY.md「朗读」）各用 Fish Audio 还是自己的 GPT-SoVITS（GLOSSARY.md「本地语音」）。
  tts: {
    rows: ["engine", "model", "read_engine", "read_model", "gpt_sovits_url", "api_key"],
    secrets: ["api_key"],
    form: [
      { name: "engine", kind: "select", options: VOICE_ENGINES },
      { name: "model", kind: "select", options: FISH_AUDIO_MODELS },
      { name: "read_engine", kind: "select", options: [SAME_AS_CALLS, ...VOICE_ENGINES] },
      { name: "read_model", kind: "select", options: [SAME_AS_CALLS, ...FISH_AUDIO_MODELS] },
      { name: "gpt_sovits_url", kind: "text" },
      { name: "api_key", kind: "secret" },
    ],
    tried: true,
  },
  stt: {
    rows: ["provider", "soniox_api_key", "dashscope_api_key", "region"],
    secrets: ["soniox_api_key", "dashscope_api_key"],
    form: [
      { name: "provider", kind: "select", options: STT_PROVIDERS },
      { name: "api_key", kind: "secret" },
    ],
    tried: true,
  },
  judge: {
    rows: ["provider", "model", "account_id", "workspace_id", "region", "emotion_timeout", "api_key"],
    secrets: ["api_key"],
    form: [
      { name: "provider", kind: "select", options: ["jev", "cloudflare", "bailian"] },
      { name: "model", kind: "text" },
      { name: "account_id", kind: "optionalText" },
      { name: "workspace_id", kind: "optionalText" },
      { name: "region", kind: "select", options: BAILIAN_REGIONS },
      { name: "emotion_timeout", kind: "seconds" },
      { name: "api_key", kind: "secret" },
    ],
    tried: true,
  },
  // 生图（GLOSSARY.md「生图」）：接口形式、地址、key、模型、同时几张、画风。保存前真生成一张来检查。
  image: {
    rows: ["api_format", "base_url", "model", "concurrency", "style", "api_key"],
    secrets: ["api_key"],
    form: [
      { name: "api_format", kind: "select", options: IMAGE_FORMATS },
      { name: "base_url", kind: "text" },
      { name: "model", kind: "modelChoice", options: IMAGE_MODELS },
      { name: "concurrency", kind: "count" },
      { name: "style", kind: "clearableText" },
      { name: "api_key", kind: "secret" },
    ],
    tried: true,
  },
  judge_thresholds: THRESHOLD(["recall", "emotion", "out_of_line", "manipulation", "photo"]),
  rerank: THRESHOLD(["references", "memories"]),
  limits: {
    rows: [...LIMIT_FIELDS, ...EXEMPTING_FIELDS],
    secrets: [],
    form: [
      ...LIMIT_FIELDS.map((name): FieldSpec => ({ name, kind: "limit" })),
      ...EXEMPTING_FIELDS.map((name): FieldSpec => ({ name, kind: "switch" })),
    ],
    tried: false,
  },
  // 好感度和礼物（GLOSSARY.md「好感度」）：只有一个开关；没配判断模型时整项不可用。
  affinity: {
    rows: ["enabled"],
    secrets: [],
    form: [{ name: "enabled", kind: "switch" }],
    tried: false,
  },
  // 搭话（GLOSSARY.md「搭话」）：总开关，每位用户每天最多几次。不试调。
  reach_outs: {
    rows: ["enabled", "per_day"],
    secrets: [],
    form: [
      { name: "enabled", kind: "switch" },
      { name: "per_day", kind: "positive" },
    ],
    tried: false,
  },
  // 网络代理（GLOSSARY.md「网络代理」）：总开关、地址、密码，每项服务一个开关。保存前把换了路线的服务连一遍。
  proxy: {
    rows: ["enabled", "url", "password", ...PROXIED_SERVICES],
    secrets: ["password"],
    form: [
      { name: "enabled", kind: "switch" },
      { name: "url", kind: "clearableText" },
      { name: "password", kind: "secret" },
      ...PROXIED_SERVICES.map((name): FieldSpec => ({ name, kind: "switch" })),
    ],
    tried: true,
  },
  // 服务状态（GLOSSARY.md「服务状态」）：统计时段，每项服务显不显示。不试调。
  service_status: {
    rows: ["window_minutes", ...STATUS_SERVICES],
    secrets: [],
    form: [
      { name: "window_minutes", kind: "minutes", options: STATUS_WINDOWS },
      ...STATUS_SERVICES.map((name): FieldSpec => ({ name, kind: "switch" })),
    ],
    tried: false,
  },
};

/** One field of an item, as the table shows it: `.env`'s, the one in effect, and whether
 * the admin console overrides it, with what. `inEffect` marks an override the effective
 * value comes from; one that is not in effect is shown all the same. */
export type FieldRow = {
  field: string;
  env: Value;
  effective: Value;
  overridden: boolean;
  override: Value;
  inEffect: boolean;
};

function valueOf(shown: Shown, field: string, secrets: string[]): Value | undefined {
  return secrets.includes(field) ? shown[`${field}_last4`] : shown[field];
}

export function rows(item: ConfigItem, spec: ItemSpec): FieldRow[] {
  return spec.rows.map((field) => {
    const override = item.override ? valueOf(item.override, field, spec.secrets) : undefined;
    return {
      field,
      env: valueOf(item.env, field, spec.secrets) ?? null,
      effective: valueOf(item.effective, field, spec.secrets) ?? null,
      overridden: override !== undefined,
      override: override ?? null,
      inEffect: override !== undefined && item.status === "overridden",
    };
  });
}

/** An item's form, as typed: starts from the values in effect, every key empty. A limit
 * with none is `UNLIMITED`, never "0", so that 0 is not typed in to mean anything. */
export type Form = Record<string, string>;

export const UNLIMITED = "unlimited";

export function formOf(spec: ItemSpec, effective: Shown): Form {
  return Object.fromEntries(
    spec.form.map(({ name, kind }) => {
      const value = kind === "secret" ? null : effective[name];
      if (kind === "limit" && !value) return [name, UNLIMITED];
      if (kind === "effort") return [name, value === null || value === undefined ? EFFORT_UNSET : String(value)];
      if (kind === "extraBody") return [name, extraBodyText(value)];
      return [name, value === null || value === undefined ? "" : String(value)];
    }),
  );
}

/** Whether the switch `field` is the last exempting key on, which cannot be turned off: the
 * server refuses them all off. */
export function lockedOn(form: Form, field: string): boolean {
  const on = EXEMPTING_FIELDS.filter((name) => form[name] === "true");
  return on.length === 1 && on[0] === field;
}

export type Problem = {
  field: string;
  reason:
    | "required"
    | "maxTokens"
    | "effort"
    | "json"
    | "jsonObject"
    | "probability"
    | "seconds"
    | "limit"
    | "count"
    | "positive";
};

export type Change = Record<string, string | number | boolean | null | JsonObject>;

/** What saving the form sends, or every problem that keeps it from being saved (one per
 * field, in the form's order). A key left empty, or one that cannot be changed here, stays
 * as it is; so does an optional field left empty. */
export function changeOf(spec: ItemSpec, form: Form, keysEditable: boolean): { change: Change } | { problems: Problem[] } {
  const change: Change = {};
  const problems: Problem[] = [];
  const refuse = (field: string, reason: Problem["reason"]) => problems.push({ field, reason });
  for (const { name, kind } of spec.form) {
    const typed = (form[name] ?? "").trim();
    switch (kind) {
      case "select":
        // 没选过的不发（判断模型的地域只有百炼用）。
        if (form[name]) change[name] = form[name];
        break;
      case "text":
      case "modelChoice":
        if (!typed) refuse(name, "required");
        else change[name] = typed;
        break;
      case "optionalText":
        if (typed) change[name] = typed;
        break;
      case "clearableText":
        change[name] = typed || null;
        break;
      case "secret":
        if (keysEditable && typed) change[name] = typed;
        break;
      case "maxTokens":
        if (typed && !/^[1-9]\d*$/.test(typed)) refuse(name, "maxTokens");
        else change[name] = typed ? Number(typed) : null;
        break;
      case "effort": {
        // 换成 Anthropic 时，原来选的「极低」还留在表单里：它写不出这一档，要换一档。
        const effort = effortOf(form[name], form.protocol);
        if (effort === undefined) refuse(name, "effort");
        else change[name] = effort;
        break;
      }
      case "extraBody": {
        const parsed = parseExtraBody(form[name] ?? "");
        if ("problem" in parsed) refuse(name, parsed.problem);
        else change[name] = parsed.value;
        break;
      }
      case "probability": {
        const value = Number(typed);
        if (!typed || !(value >= 0 && value <= 1)) refuse(name, "probability");
        else change[name] = value;
        break;
      }
      case "limit":
        if (typed === UNLIMITED) change[name] = 0;
        else if (!/^[1-9]\d*$/.test(typed)) refuse(name, "limit");
        else change[name] = Number(typed);
        break;
      case "count":
        if (!/^[1-9]\d*$/.test(typed) || Number(typed) > MAX_COUNT) refuse(name, "count");
        else change[name] = Number(typed);
        break;
      case "positive":
        if (!/^[1-9]\d*$/.test(typed)) refuse(name, "positive");
        else change[name] = Number(typed);
        break;
      case "switch":
        change[name] = form[name] === "true";
        break;
      case "minutes":
        change[name] = Number(form[name]);
        break;
      case "seconds": {
        const value = Number(typed);
        if (!typed || !(value > 0 && value <= 30)) refuse(name, "seconds");
        else change[name] = value;
        break;
      }
    }
  }
  return problems.length > 0 ? { problems } : { change };
}

/** A read-only setting's value, in words: a list joined, a setting set or not, the parts
 * of one with several. */
export function readOnlyValue(value: unknown, words: { set: string; unset: string; none: string }): string {
  if (value === null || value === undefined || value === "") return words.none;
  if (typeof value === "boolean") return value ? words.set : words.unset;
  if (Array.isArray(value)) return value.length ? value.map((v) => readOnlyValue(v, words)).join("、") : words.none;
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, part]) => `${key}：${readOnlyValue(part, words)}`)
      .join("；");
  }
  return String(value);
}

export async function getAdminConfig(): Promise<AdminConfig> {
  return json(await api("/api/admin/config"));
}

/** Try the item and keep it; fails with what the service said if the try fails. */
export async function saveItem(name: ItemName, change: Change): Promise<AdminConfig> {
  return json(
    await api(`/api/admin/config/${name}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(change),
    }),
  );
}

/** The models the service offers the key, as it lists them, for a model slot to pick one from: the key
 * in effect now if none is given, at its own address only (ADR-0027). */
export async function listItemModels(
  name: "chat_llm" | "memory_llm",
  asked: { protocol: string; base_url: string; api_key?: string },
): Promise<string[]> {
  const listed = await api(`/api/admin/config/${name}/models`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(asked),
  });
  return (await json<{ models: string[] }>(listed)).models;
}

/** Back to `.env`'s, from the next turn and call on. */
export async function restoreItem(name: ItemName): Promise<AdminConfig> {
  return json(await api(`/api/admin/config/${name}`, { method: "DELETE" }));
}
