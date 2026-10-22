import { afterEach, describe, expect, it, vi } from "vitest";
import {
  agreeToTerms,
  ApiError,
  CONSENT_REQUIRED,
  checkOwnImage,
  checkOwnSpeechKey,
  deleteAccount,
  getCallSetup,
  getLimitExemption,
  json,
  logout,
  logoutEverywhere,
  redirectFor,
  sendTurn,
  type TurnEvent,
} from "@/lib/api";
import { keepOwnKey, localOwnKeys } from "@/lib/localOwnKeys";

describe("redirectFor", () => {
  it("sends a browser that is not signed in to the landing page, to sign in there", () => {
    expect(redirectFor(401)).toBe("/");
  });

  it("sends a user who has not agreed to the terms to agree first", () => {
    expect(redirectFor(CONSENT_REQUIRED)).toBe("/consent");
  });

  it("lets every other response through to the page", () => {
    for (const status of [200, 204, 403, 404, 422, 429, 500, 503]) {
      expect(redirectFor(status)).toBeNull();
    }
  });
});

/** The ApiError `json()` throws for a failed response with this body. */
async function failure(status: number, body: unknown): Promise<ApiError> {
  const res = new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
  const error = await json(res).then(
    () => null,
    (e: unknown) => e,
  );
  if (!(error instanceof ApiError)) throw new Error(`expected an ApiError, got ${error}`);
  return error;
}

describe("json: the backend's reason for a failure", () => {
  it("is the detail the backend gives", async () => {
    const error = await failure(404, { detail: "没有这个角色" });
    expect(error.message).toBe("没有这个角色");
    expect(error.status).toBe(404);
  });

  it("is the first problem with a rejected request body, without pydantic's prefix", async () => {
    const error = await failure(422, {
      detail: [
        { loc: ["body", "text"], msg: "Value error, 内容不能为空", type: "value_error" },
        { loc: ["body", "other"], msg: "Field required", type: "missing" },
      ],
    });
    expect(error.message).toBe("内容不能为空");

    // A problem's message that has no prefix is kept as it is.
    expect((await failure(422, { detail: [{ msg: "Field required" }] })).message).toBe("Field required");
  });

  it("falls back to the status when the backend gives no reason", async () => {
    expect((await failure(500, {})).message).toBe("请求失败（500）");
    expect((await failure(502, { detail: [] })).message).toBe("请求失败（502）");
  });

  it("falls back to the status when the body is not JSON", async () => {
    const res = new Response("Bad Gateway", { status: 502 });
    await expect(json(res)).rejects.toThrow(new ApiError("请求失败（502）", 502));
  });
});

describe("json: over the hourly limit", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  /** Now is this local time. */
  function now(date: Date) {
    vi.useFakeTimers();
    vi.setSystemTime(date);
  }

  it("says when the user can go on, rounded up to the minute", async () => {
    now(new Date(2026, 8, 27, 14, 0, 0));
    const retryAt = new Date(2026, 8, 27, 14, 37, 12).toISOString();
    const error = await failure(429, { detail: "本小时的对话次数已用完", retry_at: retryAt });
    expect(error.message).toBe("本小时的对话次数已用完，14:38 之后可以再用。");
  });

  it("does not round a time already on the minute", async () => {
    now(new Date(2026, 8, 27, 14, 0, 0));
    const retryAt = new Date(2026, 8, 27, 14, 37, 0).toISOString();
    const error = await failure(429, { detail: "本小时的通话次数已用完", retry_at: retryAt });
    expect(error.message).toBe("本小时的通话次数已用完，14:37 之后可以再用。");
  });

  it("says the day when that is not today", async () => {
    now(new Date(2026, 8, 27, 23, 30, 0));
    const retryAt = new Date(2026, 8, 28, 0, 29, 30).toISOString();
    const error = await failure(429, { detail: "本小时的对话次数已用完", retry_at: retryAt });
    expect(error.message).toBe("本小时的对话次数已用完，9月28日 00:30 之后可以再用。");
  });

  it("asks the user to try later when the backend does not say when", async () => {
    const error = await failure(429, { detail: "本小时的对话次数已用完" });
    expect(error.message).toBe("本小时的对话次数已用完，请稍后再试。");
  });

  it("says no more when there is no time to wait for: a user on their own keys only", async () => {
    const error = await failure(429, { detail: "你的文字对话只能用自带密钥，可以在设置里填写", retry_at: null });
    expect(error.message).toBe("你的文字对话只能用自带密钥，可以在设置里填写。");
  });
});

describe("json: refused while she is in a cooldown", () => {
  it("says until when, told apart from the limit's 429", async () => {
    const until = "2026-10-14T06:30:00+00:00";
    const error = await failure(409, { detail: "她现在不想说话，晚些时候再来吧", cooldown_until: until });
    const over = await failure(429, { detail: "本小时的对话次数已用完", retry_at: until });

    expect(error).toMatchObject({ status: 409, cooldownUntil: until });
    expect(over.cooldownUntil).toBeNull();
  });
});

describe("sendTurn", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** The backend answers the turn with an event stream arriving in these chunks. */
  function answerWith(chunks: string[]) {
    const fetch = vi.fn(async (_path: string, _init: RequestInit) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          const encoder = new TextEncoder();
          for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
          controller.close();
        },
      });
      return new Response(body, { headers: { "content-type": "text/event-stream" } });
    });
    vi.stubGlobal("fetch", fetch);
    return fetch;
  }

  async function events(conversationId: string, text: string, userId = "u1"): Promise<TurnEvent[]> {
    const all: TurnEvent[] = [];
    for await (const event of sendTurn(conversationId, text, userId)) all.push(event);
    return all;
  }

  it("posts the text to the conversation's turns, bringing the user's own LLM kept in this browser as own_keys", async () => {
    const llm = {
      api_key: "sk-my-own-key-1234",
      base_url: "https://llm.example.com/v1",
      model: "my-model",
      protocol: "chat-completions",
    };
    const items = new Map([["eidolon.ownKeys.u1", JSON.stringify({ llm })]]);
    vi.stubGlobal("localStorage", { getItem: (key: string) => items.get(key) ?? null });
    const fetch = answerWith([]);

    await events("c1", "おはよう", "u1");
    await events("c1", "おはよう", "u2");

    const [path, init] = fetch.mock.calls[0];
    expect(path).toBe("/api/conversations/c1/turns");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      text: "おはよう", own_keys: { llm }, web: false,
    });
    expect(JSON.parse(fetch.mock.calls[1][1].body as string)).toEqual({
      text: "おはよう", web: false,
    });
  });

  it("yields each event once its frame is complete, however the stream is split", async () => {
    answerWith([
      'data: {"type":"user","te',
      'xt":"おはよう"}\n',
      '\ndata: {"type":"delta","segment":0,"lang":"ja","text":"おは"}\n\ndata: {"type":"de',
      'lta","segment":0,"lang":"zh","text":"早"}\n\n',
    ]);
    expect(await events("c1", "おはよう")).toEqual([
      { type: "user", text: "おはよう" },
      { type: "delta", segment: 0, lang: "ja", text: "おは" },
      { type: "delta", segment: 0, lang: "zh", text: "早" },
    ]);
  });

  it("joins a frame's data lines with newlines", async () => {
    answerWith(['event: turn\ndata: {"type":"error",\ndata: "message":"失败了"}\n\n']);
    expect(await events("c1", "おはよう")).toEqual([{ type: "error", message: "失败了" }]);
  });

  it("skips a frame with no data", async () => {
    answerWith([': keep-alive\n\ndata: {"type":"user","text":"hi"}\n\n']);
    expect(await events("c1", "hi")).toEqual([{ type: "user", text: "hi" }]);
  });

  it("throws the backend's reason when the turn is refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail: "本小时的对话次数已用完" }, { status: 429 })),
    );
    await expect(events("c1", "hi")).rejects.toThrow("本小时的对话次数已用完，请稍后再试。");
  });
});

describe("checkOwnSpeechKey", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks the server to try the key for the speech recognition service it is for, keeping nothing", async () => {
    const fetch = vi.fn(async (_path: string, _init: RequestInit) => Response.json({ region: "singapore" }));
    vi.stubGlobal("fetch", fetch);

    expect(await checkOwnSpeechKey("stt", "sk-my-dashscope-dddd", { provider: "bailian" })).toEqual({
      region: "singapore",
    });
    const [path, init] = fetch.mock.calls[0];
    expect(path).toBe("/api/account/keys/stt/check");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ api_key: "sk-my-dashscope-dddd", provider: "bailian" });
  });

  it("asks the server to try the user's own image generation, by generating one, keeping nothing", async () => {
    const image = {
      api_format: "novelai",
      base_url: "https://image.novelai.net/",
      api_key: "pst-my-own-image-key",
      model: "nai-diffusion-5-curated",
    } as const;
    const fetch = vi.fn(async (_path: string, _init: RequestInit) =>
      Response.json({ base_url: "https://image.novelai.net", model: "nai-diffusion-5-curated" }),
    );
    vi.stubGlobal("fetch", fetch);

    expect(await checkOwnImage(image)).toEqual({ base_url: "https://image.novelai.net", model: "nai-diffusion-5-curated" });
    const [path, init] = fetch.mock.calls[0];
    expect(path).toBe("/api/account/keys/image/check");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(image);
  });

  it("asks the server to try a Fish Audio key with the model chosen", async () => {
    const fetch = vi.fn(async (_path: string, _init: RequestInit) => Response.json({ region: null }));
    vi.stubGlobal("fetch", fetch);

    await checkOwnSpeechKey("tts", "fish-my-key", { model: "drama-3" });
    const [path, init] = fetch.mock.calls[0];
    expect(path).toBe("/api/account/keys/tts/check");
    expect(JSON.parse(init.body as string)).toEqual({ api_key: "fish-my-key", model: "drama-3" });
  });
});

describe("getCallSetup", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("names the conversation, and the services the call is to bring the user's own keys for, but none of the keys", async () => {
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) =>
      Response.json({ iceServers: [], maxCallSeconds: null, callExempt: false }),
    );
    vi.stubGlobal("fetch", fetch);

    await getCallSetup("c1", ["llm", "tts", "stt"]);
    await getCallSetup("c1", []);

    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/ice-servers?conversation=c1&own=llm,tts,stt",
      "/api/ice-servers?conversation=c1",
    ]);
  });
});

describe("getLimitExemption", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("names the services the user keeps their own keys for, and none of the keys, and hands back the server's answer", async () => {
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => Response.json({ turns: true, calls: false }));
    vi.stubGlobal("fetch", fetch);

    expect(await getLimitExemption(["llm", "tts"])).toEqual({ turns: true, calls: false });
    await getLimitExemption([]);

    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/account/keys/exemption?own=llm,tts",
      "/api/account/keys/exemption",
    ]);
  });
});

describe("agreeToTerms", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the browser back to the landing page to sign in when the session has expired", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { pathname: "/consent", assign } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail: "Not signed in" }, { status: 401 })),
    );

    await expect(agreeToTerms()).rejects.toThrow();

    expect(assign).toHaveBeenCalledWith("/");
  });
});

describe("signing out and deleting the account", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** This browser keeps own keys for two users; the backend answers with `status`. */
  function keptFor(status: number) {
    const items = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => items.get(key) ?? null,
      setItem: (key: string, value: string) => void items.set(key, value),
      removeItem: (key: string) => void items.delete(key),
    });
    keepOwnKey("u1", "tts", { api_key: "fish-my-own-key-aaaa" });
    keepOwnKey("u2", "tts", { api_key: "fish-their-own-key" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => (status === 204 ? new Response(null, { status }) : Response.json({}, { status }))),
    );
  }

  it.each([
    ["signing out", () => logout("u1")],
    ["signing out everywhere", () => logoutEverywhere("u1")],
    ["deleting the account", () => deleteAccount("u1")],
  ])("forgets the user's own keys in this browser after %s, and no one else's", async (_, done) => {
    keptFor(204);

    await done();

    expect(localOwnKeys("u1")).toEqual({});
    expect(localOwnKeys("u2")).toEqual({ tts: { api_key: "fish-their-own-key" } });
  });

  it("keeps them when the backend did not sign the user out", async () => {
    keptFor(500);

    await expect(logout("u1")).rejects.toThrow();

    expect(localOwnKeys("u1")).toEqual({ tts: { api_key: "fish-my-own-key-aaaa" } });
  });
});
