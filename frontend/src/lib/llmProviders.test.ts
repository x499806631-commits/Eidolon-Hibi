import { describe, expect, it } from "vitest";
import { distinctOffers, matchesOffer, matchesProvider, providerAt, type LLMOffer, type LLMProvider } from "@/lib/llmProviders";

const provider = (
  id: string,
  name: string,
  base_url: string | null = null,
  base_urls = base_url ? [base_url] : [],
): LLMProvider => ({
  id,
  name,
  protocol: base_url ? "chat-completions" : null,
  base_url,
  base_urls,
});

const offer = (model: string, name: string, base_url = "https://llm.example/v1"): LLMOffer => ({
  model,
  name,
  protocol: "chat-completions",
  base_url,
});

describe("matchesProvider", () => {
  it("matches the name and the id, in any case", () => {
    expect(matchesProvider(provider("deepseek", "DeepSeek"), "deeps")).toBe(true);
    expect(matchesProvider(provider("cline-pass", "ClinePass"), "CLINE")).toBe(true);
    expect(matchesProvider(provider("deepseek", "DeepSeek"), "openai")).toBe(false);
  });

  it("matches the Chinese names models.dev does not write", () => {
    expect(matchesProvider(provider("zhipuai-coding-plan", "Zhipu AI Coding Plan"), "智谱")).toBe(true);
    expect(matchesProvider(provider("volcengine-coding-plan", "Volcengine Ark Coding Plan"), "火山 套餐")).toBe(true);
    expect(matchesProvider(provider("volcengine", "Volcengine"), "套餐")).toBe(false);
    expect(matchesProvider(provider("google", "Google"), "gemini")).toBe(true);
  });

  it("matches Chinese words typed together as well as apart", () => {
    const plan = provider("zhipuai-coding-plan", "Zhipu AI Coding Plan");
    expect(matchesProvider(plan, "智谱套餐")).toBe(true);
    expect(matchesProvider(plan, "智谱订阅")).toBe(true);
    expect(matchesProvider(provider("zhipuai", "Zhipu AI"), "智谱订阅")).toBe(false);
    expect(matchesProvider(plan, "智订")).toBe(false);
  });

  it("matches every one when nothing is typed", () => {
    expect(matchesProvider(provider("deepseek", "DeepSeek"), "  ")).toBe(true);
  });
});

describe("matchesOffer", () => {
  it("matches the name and the model's id", () => {
    expect(matchesOffer(offer("glm-5.3", "GLM-5.3"), "glm 5.3")).toBe(true);
    expect(matchesOffer(offer("anthropic/claude-sonnet-5", "Claude Sonnet 5"), "anthropic/")).toBe(true);
    expect(matchesOffer(offer("glm-5.3", "GLM-5.3"), "qwen")).toBe(false);
  });
});

describe("providerAt", () => {
  const none = new Map<string, LLMOffer[]>();
  const providers = [
    provider("google", "Google"),
    provider("deepseek", "DeepSeek", "https://api.deepseek.com"),
    provider("zenmux", "ZenMux", "https://zenmux.example/v1", [
      "https://zenmux.example/anthropic",
      "https://zenmux.example/v1",
    ]),
  ];

  it("finds the provider at an address, whatever its trailing slash and case", () => {
    expect(providerAt(providers, "https://API.deepseek.com/ ", "", none)?.id).toBe("deepseek");
  });

  it("finds the provider one of whose models is at an address of its own", () => {
    expect(providerAt(providers, "https://zenmux.example/anthropic", "claude-sonnet-5", none)?.id).toBe("zenmux");
  });

  it("finds, of providers at one address, the one serving the model there", () => {
    const at = "https://api.minimax.example/anthropic/v1";
    const shared = [provider("minimax", "MiniMax", at), provider("minimax-coding-plan", "MiniMax Coding Plan", at)];
    const served = new Map([
      ["minimax", [offer("minimax-m3", "MiniMax-M3", at)]],
      ["minimax-coding-plan", [offer("minimax-m3-plan", "MiniMax-M3 Plan", at)]],
    ]);

    expect(providerAt(shared, at, "minimax-m3-plan", served)?.id).toBe("minimax-coding-plan");
    expect(providerAt(shared, at, "minimax-m3", served)?.id).toBe("minimax");
  });

  it("finds none at an address no provider is at, nor at none", () => {
    expect(providerAt(providers, "https://relay.example/v1", "", none)).toBeUndefined();
    expect(providerAt(providers, "", "", none)).toBeUndefined();
  });
});

describe("distinctOffers", () => {
  it("keeps the first of a model listed twice", () => {
    expect(distinctOffers([offer("m", "First"), offer("n", "N"), offer("m", "Second")]).map((o) => o.name)).toEqual([
      "First",
      "N",
    ]);
  });
});
