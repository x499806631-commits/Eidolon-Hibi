import type { LLMProtocol } from "@/lib/localOwnKeys";

// 选 LLM 时的服务商和模型（ADR-0027）：后端从 models.dev 整理出来，设置页的自带 LLM 和后台的模型共用。
// 选了只是把接口协议、地址和模型名填进表单，存的仍是这三项，不记选的是哪家。

/**
 * 一家服务商：默认在哪个地址、用哪种协议提供模型；没写或不是我们支持的协议时两项都是 null，只能从它的模型里选。
 * `base_urls` 是它各个模型所在的地址，认已存的 LLM 是哪家时用。
 */
export type LLMProvider = {
  id: string;
  name: string;
  protocol: LLMProtocol | null;
  base_url: string | null;
  base_urls: string[];
};

/** 服务商提供的一个模型：`model` 是调用时写的模型名，`name` 是给人看的名字；地址和协议按这个模型自己的。 */
export type LLMOffer = { model: string; name: string; protocol: LLMProtocol; base_url: string };

/**
 * 搜索时额外认的中文名和俗称，按服务商 id 的开头配：models.dev 只写英文名，用户搜「智谱」「火山」也要能搜到。
 * 只管搜索，不影响显示和填进去的值。
 */
const ALIASES: [prefix: string, words: string][] = [
  ["deepseek", "深度求索"],
  ["zhipuai", "智谱 清言 GLM"],
  ["zai", "智谱 GLM"],
  ["moonshotai", "月之暗面 Kimi"],
  ["kimi", "月之暗面 Kimi"],
  ["alibaba", "阿里 阿里云 百炼 通义 千问 Qwen DashScope"],
  ["volcengine", "火山 火山引擎 方舟 豆包 字节"],
  ["minimax", "海螺 稀宇"],
  ["siliconflow", "硅基流动"],
  ["tencent", "腾讯 混元"],
  ["xiaomi", "小米 MiMo"],
  ["stepfun", "阶跃星辰"],
  ["modelscope", "魔搭"],
  ["qiniu", "七牛"],
  ["iflowcn", "心流"],
  ["bailing", "百灵 蚂蚁"],
  ["longcat", "美团"],
  ["google", "Gemini 谷歌"],
  ["anthropic", "Claude"],
  ["openai", "ChatGPT GPT"],
  ["xai", "Grok"],
];

/** 订阅制的编程套餐（Coding Plan、Token Plan 之类）另认的词。 */
const PLAN_WORDS = "套餐 订阅 编程";

function searchText(provider: LLMProvider): string {
  const aliases = ALIASES.filter(([prefix]) => provider.id.startsWith(prefix)).map(([, words]) => words);
  const plan = /plan|coding|pass/.test(provider.id) ? [PLAN_WORDS] : [];
  return [provider.name, provider.id, ...aliases, ...plan].join(" ").toLowerCase();
}

const HAN = /\p{Script=Han}/u;

/** `word` 在不在 `text` 里：带汉字的词也可以拆成几段（每段至少两个字），各段都在就算，所以「智谱套餐」和「智谱 套餐」一样。 */
function contains(text: string, word: string): boolean {
  if (text.includes(word)) return true;
  if (!HAN.test(word)) return false;
  for (let i = 2; i <= word.length - 2; i++) {
    if (text.includes(word.slice(0, i)) && contains(text, word.slice(i))) return true;
  }
  return false;
}

/** 搜索框里的字分成几个词，每个词都要在里面出现。 */
function matches(text: string, query: string): boolean {
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => contains(text, word));
}

/** 这家服务商对不对得上搜索框里的字：名字、id、中文名都算。 */
export function matchesProvider(provider: LLMProvider, query: string): boolean {
  return matches(searchText(provider), query);
}

/** 这个模型对不对得上搜索框里的字：名字和模型名都算。 */
export function matchesOffer(offer: Pick<LLMOffer, "model" | "name">, query: string): boolean {
  return matches(`${offer.name} ${offer.model}`.toLowerCase(), query);
}

const bare = (url: string) => url.trim().replace(/\/+$/, "").toLowerCase();

const isAt = (url: string) => (at: string | null) => at !== null && bare(at) === url;

/** 在 `baseUrl`（不管末尾的斜杠和大小写）有模型的几家。 */
export function providersAt(providers: LLMProvider[], baseUrl: string): LLMProvider[] {
  const url = bare(baseUrl);
  return url ? providers.filter((p) => isAt(url)(p.base_url) || p.base_urls.some(isAt(url))) : [];
}

/**
 * 编辑已存的 LLM 时认出是哪家，`served` 是已经拉到的各家模型。几家共用一个地址时（按量和编程套餐常这样），
 * 先认在这个地址有这个模型的，再认默认地址就是这里的；都没有就是 undefined。
 */
export function providerAt(
  providers: LLMProvider[],
  baseUrl: string,
  model: string,
  served: ReadonlyMap<string, LLMOffer[]>,
): LLMProvider | undefined {
  const url = bare(baseUrl);
  const at = providersAt(providers, baseUrl);
  const named = model.trim();
  return (
    at.find((p) => served.get(p.id)?.some((o) => o.model === named && isAt(url)(o.base_url))) ??
    at.find((p) => isAt(url)(p.base_url)) ??
    at[0]
  );
}

/** 同一个模型名只留第一个：下拉框的值是模型名。 */
export function distinctOffers(offers: LLMOffer[]): LLMOffer[] {
  const seen = new Set<string>();
  return offers.filter((o) => !seen.has(o.model) && seen.add(o.model));
}
