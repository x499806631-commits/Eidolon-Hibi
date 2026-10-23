"use client";

import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxSelectTrigger,
} from "@/components/ui/combobox";
import { Field, FieldDescription, FieldError, FieldLabel, FieldTitle } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import { getLLMProviderModels, getLLMProviders } from "@/lib/api";
import {
  distinctOffers,
  matchesOffer,
  matchesProvider,
  providerAt,
  providersAt,
  type LLMOffer,
  type LLMProvider,
} from "@/lib/llmProviders";
import type { LLMProtocol } from "@/lib/localOwnKeys";

const t = copy.llmPicker;

/** 下拉框里「手动填写」用这个值（下拉框的值不用空字符串）。 */
const MANUAL = "manual";

/** 选了服务商或模型后要填进表单的几项；没有的项不改。 */
export type LLMPick = { protocol?: LLMProtocol; base_url?: string; model?: string };

/** 向服务商查这把 key 能用的模型：`key` 认出问的是哪个地址、哪个协议、哪把 key，问过的不再问。 */
export type ModelLister = { key: string; ask: () => Promise<string[]> };

// 向服务商查到的模型，按 `ModelLister.key`，留到关掉页面：反复打开表单不用再问。
const asked = new Map<string, string[]>();

// 服务商列表整页只拉一次；每家的模型拉过就留着。拉失败的、和服务器还没拉到目录时的空列表都不留，下次打开表单再试。
let providersRequest: Promise<LLMProvider[]> | null = null;
const modelsRequests = new Map<string, Promise<LLMOffer[]>>();

function loadProviders(): Promise<LLMProvider[]> {
  providersRequest ??= getLLMProviders()
    .catch((): LLMProvider[] => [])
    .then((list) => {
      if (list.length === 0) providersRequest = null;
      return list;
    });
  return providersRequest;
}

function loadModels(provider: string): Promise<LLMOffer[]> {
  let request = modelsRequests.get(provider);
  if (!request) {
    request = getLLMProviderModels(provider).catch(() => {
      modelsRequests.delete(provider);
      return [];
    });
    modelsRequests.set(provider, request);
  }
  return request;
}

/**
 * 选服务商和模型（ADR-0027），放在接口协议、地址和模型名几项的上面：选了服务商就填好协议和地址，
 * 再选模型就填好模型名（连同这个模型自己的协议和地址）。几项照样能手改，存的也只是那几项。
 * 正在填的地址是某家的地址时，就显示那一家（几家共用一个地址时按模型名认），所以修改已存的 LLM 时也能接着选模型。
 * 有 `lister` 时还能向服务商查这把 key 能用的模型，查到的换掉 models.dev 的那份。
 * 拉不到服务商列表（服务器还没拉到 models.dev）时不显示服务商，只能向服务商查模型。
 */
export function LLMProviderPicker({
  id,
  baseUrl,
  model,
  onPick,
  lister,
  between,
}: {
  id: string;
  /** 表单里正在填的地址和模型名。 */
  baseUrl: string;
  model: string;
  onPick: (pick: LLMPick) => void;
  /** 向服务商查模型：还缺地址或 key 时是 null（按钮不可点）；undefined 是这张表单不查。 */
  lister?: ModelLister | null;
  /** 放在服务商和模型之间的字段：设置页的 API key，先填 key 才好查模型。 */
  between?: ReactNode;
}) {
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  // 自己选的那家，连同选的时候的地址：地址被改成别的以后，改按地址认。
  const [chosen, setChosen] = useState<{ id: string; at: string } | null>(null);
  // 拉到的各家模型，按服务商 id。
  const [served, setServed] = useState<ReadonlyMap<string, LLMOffer[]>>(new Map());
  // 正在问的、问不到的原因，都按问的是哪个（`ModelLister.key`）：地址或 key 改了就不算数。
  const [asking, setAsking] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null);
  // 问到的放在 `asked` 里，这里只让表单重画。
  const [, setAnswers] = useState(0);

  useEffect(() => {
    let current = true;
    loadProviders().then((list) => current && setProviders(list));
    return () => {
      current = false;
    };
  }, []);

  const guessed = providerAt(providers, baseUrl, model, served)?.id ?? MANUAL;
  const provider = chosen && chosen.at === baseUrl ? chosen.id : guessed;

  // 选着的那家，和在这个地址的几家（要按模型名认是哪家）：模型都拉下来。
  const wanted = [provider, ...providersAt(providers, baseUrl).map((p) => p.id)]
    .filter((p) => p !== MANUAL && !served.has(p))
    .join(" ");
  useEffect(() => {
    if (!wanted) return;
    let current = true;
    for (const id of new Set(wanted.split(" "))) {
      loadModels(id).then((list) => current && setServed((s) => new Map(s).set(id, list)));
    }
    return () => {
      current = false;
    };
  }, [wanted]);

  if (providers.length === 0 && lister === undefined) return null;

  const byId = new Map(providers.map((p) => [p.id, p]));
  const offers = distinctOffers(served.get(provider) ?? []);
  const byModel = new Map(offers.map((o) => [o.model, o]));
  const live = lister ? asked.get(lister.key) : undefined;
  const failure = lister && failed?.key === lister.key ? failed.message : null;
  const busy = !!lister && asking === lister.key;
  const label = (value: string) => byModel.get(value)?.name ?? value;
  // 问到了就是问到的那些（按名字排），否则是 models.dev 里这家的。
  const choices = live
    ? [...live].sort((a, b) => label(a).localeCompare(label(b), undefined, { sensitivity: "base" }))
    : offers.map((o) => o.model);
  const named = model.trim();

  function chooseProvider(next: string) {
    const picked = byId.get(next);
    if (!picked) {
      setChosen({ id: MANUAL, at: baseUrl });
      return;
    }
    // 换了一家，原来的模型名多半不对了，清掉；这家没写默认地址时，等选模型再填。
    const at = picked.base_url ?? baseUrl;
    setChosen({ id: picked.id, at });
    onPick({ ...(picked.base_url && picked.protocol && { base_url: at, protocol: picked.protocol }), model: "" });
  }

  function chooseModel(next: string) {
    const offer = byModel.get(next);
    if (!offer) {
      onPick({ model: next }); // 只在问到的里面：就在这个地址、按这个协议
      return;
    }
    setChosen({ id: provider, at: offer.base_url });
    onPick({ protocol: offer.protocol, base_url: offer.base_url, model: offer.model });
  }

  async function ask() {
    if (!lister) return;
    const { key } = lister;
    setAsking(key);
    setFailed(null);
    try {
      asked.set(key, await lister.ask());
      setAnswers((n) => n + 1);
    } catch (e) {
      setFailed({ key, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setAsking((now) => (now === key ? null : now));
    }
  }

  const modelHint = live ? t.fetched(live.length) : lister === null ? t.fetchNeedsKey : t.modelHint;

  return (
    <>
      {providers.length > 0 && (
        <Field>
          <FieldLabel htmlFor={`${id}-provider`}>{t.provider}</FieldLabel>
          <Combobox
            items={[MANUAL, ...providers.map((p) => p.id)]}
            itemToStringLabel={(value: string) => byId.get(value)?.name ?? t.manual}
            filter={(value: string, query: string) => {
              const p = byId.get(value);
              return p ? matchesProvider(p, query) : true;
            }}
            value={provider}
            onValueChange={(value) => value && chooseProvider(value)}
          >
            <ComboboxSelectTrigger id={`${id}-provider`} className="w-full" />
            <ComboboxContent>
              <ComboboxInput showTrigger={false} placeholder={t.search} />
              <ComboboxEmpty>{t.noProvider}</ComboboxEmpty>
              <ComboboxList>
                {(value: string) => (
                  <ComboboxItem key={value} value={value}>
                    {byId.get(value)?.name ?? t.manual}
                  </ComboboxItem>
                )}
              </ComboboxList>
            </ComboboxContent>
          </Combobox>
          <FieldDescription>{t.providerHint}</FieldDescription>
        </Field>
      )}
      {between}
      {(choices.length > 0 || lister !== undefined) && (
        <Field>
          {/* 只有按钮时不拿它当标签的对象：按钮念它自己的字 */}
          {choices.length > 0 ? (
            <FieldLabel htmlFor={`${id}-offer`}>{t.model}</FieldLabel>
          ) : (
            <FieldTitle>{t.model}</FieldTitle>
          )}
          <div className="flex gap-2">
            {choices.length > 0 && (
              <Combobox
                items={choices}
                itemToStringLabel={label}
                filter={(value: string, query: string) => matchesOffer({ model: value, name: label(value) }, query)}
                value={choices.includes(named) ? named : null}
                onValueChange={(value) => value && chooseModel(value)}
              >
                <ComboboxSelectTrigger id={`${id}-offer`} className="min-w-0 flex-1" placeholder={t.modelPlaceholder} />
                <ComboboxContent>
                  <ComboboxInput showTrigger={false} placeholder={t.modelSearch} />
                  <ComboboxEmpty>{t.noModel}</ComboboxEmpty>
                  <ComboboxList>
                    {(value: string) => (
                      <ComboboxItem key={value} value={value}>
                        <span className="flex min-w-0 flex-col">
                          <span className="truncate">{label(value)}</span>
                          {label(value) !== value && (
                            <span className="truncate text-meta text-muted-foreground">{value}</span>
                          )}
                        </span>
                      </ComboboxItem>
                    )}
                  </ComboboxList>
                </ComboboxContent>
              </Combobox>
            )}
            {lister !== undefined && (
              <Button
                type="button"
                variant="outline"
                disabled={!lister || busy}
                focusableWhenDisabled
                onClick={ask}
              >
                {busy && <Spinner data-icon="inline-start" />}
                {busy ? t.fetching : t.fetch}
              </Button>
            )}
          </div>
          <div aria-live="polite">
            {failure ? <FieldError>{failure}</FieldError> : <FieldDescription>{modelHint}</FieldDescription>}
          </div>
        </Field>
      )}
    </>
  );
}
