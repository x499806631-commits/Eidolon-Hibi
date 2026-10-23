"use client";

import { InfoIcon } from "lucide-react";
import { FormEvent, useEffect, useId, useState, type ReactNode } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { LLMProviderPicker, type LLMPick, type ModelLister } from "@/components/app/LLMProviderPicker";
import { ModelChoiceField } from "@/components/app/ModelChoiceField";
import { EffortField, ExtraBodyField, useEffortHint } from "@/components/app/ReasoningEffortFields";
import { SettingsSection } from "@/components/app/SettingsSection";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import {
  checkOwnImage,
  checkOwnLLM,
  checkOwnMemoryLLM,
  checkOwnSpeechKey,
  getAccount,
  getLimitExemption,
  listOwnLLMModels,
  type LimitExemption,
} from "@/lib/api";
import { exemptionNote, sayServices } from "@/lib/exemptingKeys";
import { toFailure, type Failure } from "@/lib/failure";
import {
  canKeepOwnKeys,
  DEFAULT_FISH_AUDIO_MODEL,
  DEFAULT_IMAGE_BASE_URL,
  FISH_AUDIO_MODELS,
  forgetOwnKey,
  IMAGE_FORMATS,
  IMAGE_MODELS,
  keepOwnKey,
  last4,
  localOwnKeys,
  LLM_PROTOCOLS,
  ownServices,
  STT_PROVIDERS,
  type FishAudioModel,
  type ImageFormat,
  type LLMProtocol,
  type LocalImage,
  type LocalLLM,
  type LocalOwnKeys,
  type LocalTtsKey,
  type OwnService,
  type SttProvider,
} from "@/lib/localOwnKeys";
import { EFFORT_UNSET, effortLabel, effortOf, extraBodyText, parseExtraBody } from "@/lib/reasoningEffort";
import { cn } from "@/lib/utils";

const t = copy.settings.ownKeys;

/**
 * 用户自带、自己付费的密钥。每项都只存在这个浏览器里，按登录的用户分开存；填写时服务器只试调、
 * 不保存。浏览器存不了时如实说，表单停用，不退而存在内存里。
 */
export function OwnKeysSettings() {
  const [userId, setUserId] = useState<string | null>(null);
  /** 这个部署的免限额密钥，和 `userId` 一起从账号读到。 */
  const [exemptingKeys, setExemptingKeys] = useState<OwnService[]>([]);
  const [kept, setKept] = useState<LocalOwnKeys>({});
  const [canKeep, setCanKeep] = useState(true);
  const [failure, setFailure] = useState<Failure | null>(null);
  // 服务器对哪几项服务答的豁免；和现在存的对不上（还没问到）或问失败时，不显示那句说明。
  const [answered, setAnswered] = useState<{ services: string; exemption: LimitExemption } | null>(null);
  const services = ownServices(kept).join(",");

  useEffect(() => {
    if (userId === null) return;
    const own = ownServices(kept);
    let current = true;
    getLimitExemption(own).then(
      (exemption) => {
        if (current) setAnswered({ services: own.join(","), exemption });
      },
      () => {
        if (current) setAnswered(null);
      },
    );
    return () => {
      current = false;
    };
  }, [userId, kept]);

  useEffect(() => {
    getAccount().then(
      (account) => {
        setUserId(account.id);
        setExemptingKeys(account.exempting_keys);
        setKept(localOwnKeys(account.id));
        setCanKeep(canKeepOwnKeys());
      },
      (e) => setFailure(toFailure(e)),
    );
  }, []);

  if (failure && !userId) {
    return (
      <SettingsSection>
        <FailureNotice failure={failure} />
      </SettingsSection>
    );
  }
  if (!userId) return null;

  /** 改了浏览器里存的 key，再按存下的显示：存不下的就是没存。 */
  const change = (keep: () => void) => {
    keep();
    setKept(localOwnKeys(userId));
  };

  return (
    <SettingsSection
      description={
        <>
          {t.summary}
          {/* ⓘ：悬停或点开都弹出其余说明；用 Popover 不用 Tooltip，Tooltip 不响应触摸，也不该装长段文字。
              只此一处，按 shadcn 纪律直接写在这里，不抽成项目组件。 */}
          <Popover>
            <PopoverTrigger
              openOnHover
              render={<Button variant="ghost" size="icon-xs" className="ml-0.5 align-middle" aria-label={t.detailsLabel} />}
            >
              <InfoIcon />
            </PopoverTrigger>
            <PopoverContent align="start">
              <ul className="flex list-disc flex-col gap-1 pl-4">
                {t.details.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </PopoverContent>
          </Popover>
        </>
      }
    >
      {!canKeep && <FailureNotice failure={{ message: t.cannotKeep, signedOut: false }} />}
      <p className="text-body">{sayServices(t.exempting, exemptingKeys)}</p>
      <p role="status" className="text-body">
        {answered?.services === services && exemptionNote(answered.exemption, exemptingKeys)}
        {t.perCallLimits}
      </p>
      <div className="flex flex-col gap-4">
        <OwnLLMForm
          kind="chat"
          current={kept.llm}
          disabled={!canKeep}
          onChecked={(llm) => change(() => keepOwnKey(userId, "llm", llm))}
          onDelete={() => change(() => forgetOwnKey(userId, "llm"))}
        />
        {/* 后台模型只附加在自带 LLM 上：没填 LLM 时不显示，删掉 LLM 时一起删掉（`forgetOwnKey`）。 */}
        {kept.llm && (
          <>
            <Separator />
            <OwnLLMForm
              kind="memory"
              current={kept.memory_llm}
              disabled={!canKeep}
              onChecked={(llm) => change(() => keepOwnKey(userId, "memory_llm", llm))}
              onDelete={() => change(() => forgetOwnKey(userId, "memory_llm"))}
            />
          </>
        )}
        <Separator />
        <OwnSpeechKeyForm
          title={t.ttsTitle}
          current={kept.tts}
          model={kept.tts?.model ?? DEFAULT_FISH_AUDIO_MODEL}
          disabled={!canKeep}
          onSubmit={async (apiKey, _, model = DEFAULT_FISH_AUDIO_MODEL) => {
            await checkOwnSpeechKey("tts", apiKey, { model });
            change(() => keepOwnKey(userId, "tts", { api_key: apiKey, model }));
          }}
          onDelete={() => change(() => forgetOwnKey(userId, "tts"))}
        />
        <Separator />
        <OwnSpeechKeyForm
          title={t.sttTitle}
          current={kept.stt}
          provider={kept.stt?.provider ?? "soniox"}
          disabled={!canKeep}
          onSubmit={async (apiKey, provider = "soniox") => {
            // 百炼的 key 连同试调判断出的地域一起存，通话时随 key 带上。
            const { region } = await checkOwnSpeechKey("stt", apiKey, { provider });
            change(() => keepOwnKey(userId, "stt", { api_key: apiKey, provider, ...(region ? { region } : {}) }));
          }}
          onDelete={() => change(() => forgetOwnKey(userId, "stt"))}
        />
        <Separator />
        {/* 生图一直显示：部署者没配生图时，自带的也能让她发照片。 */}
        <OwnImageForm
          current={kept.image}
          disabled={!canKeep}
          onChecked={(image) => change(() => keepOwnKey(userId, "image", image))}
          onDelete={() => change(() => forgetOwnKey(userId, "image"))}
        />
      </div>
    </SettingsSection>
  );
}

/**
 * 一项服务：标题；填过的只显示末 4 位和修改、删除；没填的一行说明加「填写」，点开才是表单，
 * 页面保持安静（PRODUCT.md「角色在前，系统在后」）。
 */
function ServiceKey({
  title,
  hint,
  summary,
  isSet,
  editing,
  disabled,
  onEdit,
  onDelete,
  children,
}: {
  title: string;
  hint?: string;
  /** 一行一句：填过的是末 4 位等，没填的是一句说明。 */
  summary: string[];
  isSet: boolean;
  editing: boolean;
  /** 这个浏览器存不了：不能填写、修改。 */
  disabled: boolean;
  onEdit: () => void;
  onDelete: () => void;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h3 className="text-row-name">{title}</h3>
        {hint && <p className="text-body text-muted-foreground">{hint}</p>}
      </div>
      {!editing && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            {summary.map((line) => (
              <p key={line} className={cn("text-body break-words", !isSet && "text-muted-foreground")}>
                {line}
              </p>
            ))}
          </div>
          <div className="flex gap-1">
            <Button variant="ghost" size="sm" disabled={disabled} onClick={onEdit}>
              {isSet ? t.edit : t.fill}
            </Button>
            {isSet && (
              <Button variant="ghost" size="sm" aria-label={t.removeLabel(title)} onClick={onDelete}>
                {t.remove}
              </Button>
            )}
          </div>
        </div>
      )}
      {editing && !disabled && children}
    </div>
  );
}

function FormButtons({ busy, canSubmit, onCancel }: { busy: boolean; canSubmit: boolean; onCancel: () => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button type="submit" disabled={!canSubmit || busy}>
        {busy && <Spinner data-icon="inline-start" />}
        {busy ? t.saving : t.save}
      </Button>
      <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
        {copy.common.cancel}
      </Button>
    </div>
  );
}

/**
 * 语音合成或语音识别的 key：先让服务器试调一次（它不保存），通过了才存进这个浏览器。语音识别先选服务商
 * 再填 key；改服务商就是换一个 key，同样要填 key、试调。语音合成另选模型；只换模型时 key 可以留空，
 * 沿用存着的那把，照样按新模型试调。
 */
function OwnSpeechKeyForm({
  title,
  current,
  provider: currentProvider,
  model: currentModel,
  disabled,
  onSubmit,
  onDelete,
}: {
  title: string;
  current: LocalTtsKey | undefined;
  /** 语音识别：已填的 key 所属的服务商，没填时是默认选中的那家。 */
  provider?: SttProvider;
  /** 语音合成：已填的 key 选的模型，没填时是默认选中的那个。 */
  model?: FishAudioModel;
  disabled: boolean;
  /** 试调这把 key（语音识别的连同选的服务商，语音合成的连同选的模型），通过了就存下；不通过时带着服务商的原因失败。 */
  onSubmit: (apiKey: string, provider: SttProvider | undefined, model: FishAudioModel | undefined) => Promise<void>;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [provider, setProvider] = useState(currentProvider);
  const [model, setModel] = useState(currentModel);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const id = useId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!keyToTry) return;
    setBusy(true);
    setFailure(null);
    try {
      await onSubmit(keyToTry, provider, model);
      setApiKey("");
      setEditing(false);
    } catch (e) {
      setFailure(toFailure(e));
    } finally {
      setBusy(false);
    }
  }

  function edit() {
    setProvider(currentProvider);
    setModel(currentModel);
    setEditing(true);
  }

  // 语音合成只换模型时沿用存着的 key；语音识别换服务商就得填新的。
  const keepsKey = model !== undefined && !!current;
  const keyToTry = apiKey.trim() || (keepsKey ? current.api_key : "");
  const summary = current ? [t.last4(last4(current.api_key))] : [t.notSet];
  const named = !current
    ? null
    : currentProvider
      ? t.sttProviders[currentProvider]
      : currentModel
        ? t.ttsModels[currentModel]
        : null;
  const models = FISH_AUDIO_MODELS.map((m) => ({ value: m, label: t.ttsModels[m] }));
  return (
    <ServiceKey
      title={title}
      summary={named ? [named, ...summary] : summary}
      isSet={!!current}
      editing={editing}
      disabled={disabled}
      onEdit={edit}
      onDelete={onDelete}
    >
      <form onSubmit={submit}>
        <FieldGroup>
          {provider && (
            <FieldSet>
              <FieldLegend variant="label">{t.sttProvider}</FieldLegend>
              <RadioGroup
                value={provider}
                onValueChange={(value) => setProvider(value as SttProvider)}
                className="flex flex-wrap gap-x-6 gap-y-2"
              >
                {STT_PROVIDERS.map((p) => (
                  <Field key={p} orientation="horizontal" className="w-auto">
                    <RadioGroupItem value={p} id={`${id}-${p}`} />
                    <FieldLabel htmlFor={`${id}-${p}`}>{t.sttProviders[p]}</FieldLabel>
                  </Field>
                ))}
              </RadioGroup>
            </FieldSet>
          )}
          {model && (
            <Field>
              <FieldLabel htmlFor={`${id}-model`}>{t.ttsModel}</FieldLabel>
              <Select items={models} value={model} onValueChange={(value) => value && setModel(value as FishAudioModel)}>
                <SelectTrigger id={`${id}-model`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {models.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          )}
          <Field>
            <FieldLabel htmlFor={`${id}-key`}>{t.apiKey}</FieldLabel>
            <Input
              id={`${id}-key`}
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              autoComplete="off"
            />
            {keepsKey && <FieldDescription>{t.keepApiKey}</FieldDescription>}
          </Field>
          {failure && <FailureNotice failure={failure} />}
          <FormButtons busy={busy} canSubmit={!!keyToTry} onCancel={() => setEditing(false)} />
        </FieldGroup>
      </form>
    </ServiceKey>
  );
}

/**
 * 自带的生图：接口形式、地址、key、模型四项，默认值和后台的一样。先让服务器真生成一张试调（花用户自己的额度，
 * 服务器不保存），通过了才存进这个浏览器。修改时 key 留空就沿用存着的那把。
 */
function OwnImageForm({
  current,
  disabled,
  onChecked,
  onDelete,
}: {
  current: LocalImage | undefined;
  disabled: boolean;
  /** 试调通过的生图，地址和模型名按服务器规范化过的。 */
  onChecked: (image: LocalImage) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [apiFormat, setApiFormat] = useState<ImageFormat>("novelai");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const id = useId();
  const admin = copy.admin.config;

  const keyToTry = apiKey.trim() || current?.api_key;
  const toTry: LocalImage | null =
    baseUrl.trim() && model.trim() && keyToTry
      ? { api_format: apiFormat, base_url: baseUrl.trim(), api_key: keyToTry, model: model.trim() }
      : null;

  /** 打开表单，从存着的那份填起；没存过的从默认值填起。 */
  function edit() {
    setApiFormat(current?.api_format ?? "novelai");
    setBaseUrl(current?.base_url ?? DEFAULT_IMAGE_BASE_URL);
    setModel(current?.model ?? IMAGE_MODELS[0]);
    setFailure(null);
    setEditing(true);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!toTry) return;
    setBusy(true);
    setFailure(null);
    try {
      onChecked({ ...toTry, ...(await checkOwnImage(toTry)) });
      setApiKey("");
      setEditing(false);
    } catch (e) {
      setFailure(toFailure(e));
    } finally {
      setBusy(false);
    }
  }

  const formats = IMAGE_FORMATS.map((f) => ({ value: f, label: admin.imageFormats[f] ?? f }));
  const models = IMAGE_MODELS.map((m) => ({ value: m, label: admin.imageModels[m] ?? m }));
  const summary = current
    ? [
        admin.imageFormats[current.api_format] ?? current.api_format,
        t.llmCurrent(current.model, current.base_url),
        t.last4(last4(current.api_key)),
      ]
    : [t.imageNotSet];

  return (
    <ServiceKey
      title={t.imageTitle}
      hint={t.imageHint}
      summary={summary}
      isSet={!!current}
      editing={editing}
      disabled={disabled}
      onEdit={edit}
      onDelete={onDelete}
    >
      <form onSubmit={submit}>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor={`${id}-format`}>{t.imageFormat}</FieldLabel>
            <Select
              items={formats}
              value={apiFormat}
              onValueChange={(value) => value && setApiFormat(value as ImageFormat)}
            >
              <SelectTrigger id={`${id}-format`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {formats.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-base-url`}>{t.baseUrl}</FieldLabel>
            <Input
              id={`${id}-base-url`}
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              autoComplete="off"
            />
            <FieldDescription>{admin.formHints.image_base_url}</FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-key`}>{t.apiKey}</FieldLabel>
            <Input
              id={`${id}-key`}
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              autoComplete="off"
            />
            {current && <FieldDescription>{t.keepApiKey}</FieldDescription>}
          </Field>
          <ModelChoiceField
            id={`${id}-model`}
            label={t.model}
            options={models}
            customLabel={admin.customModel}
            value={model}
            onChange={setModel}
            error={null}
          />
          {failure && <FailureNotice failure={failure} />}
          <FormButtons busy={busy} canSubmit={!!toTry} onCancel={() => setEditing(false)} />
        </FieldGroup>
      </form>
    </ServiceKey>
  );
}

/** 两种自带 LLM 的表单各自的文案和试调：`callEffort` 是有没有通话那一档。 */
const LLM_FORMS = {
  chat: {
    title: t.llmTitle,
    hint: t.llmHint,
    notSet: t.notSet,
    effort: t.textEffort,
    effortHint: t.textEffortHint,
    callEffort: true,
    check: checkOwnLLM,
  },
  memory: {
    title: t.memoryLlmTitle,
    hint: t.memoryLlmHint,
    notSet: t.memoryLlmNotSet,
    effort: t.memoryEffort,
    effortHint: t.memoryEffortHint,
    callEffort: false,
    check: checkOwnMemoryLLM,
  },
};

/**
 * 自带的 LLM（`chat`，文字、通话各一档思考强度），或另配的后台模型（`memory`，只有一档）：
 * 先让服务器试调一次（它不保存），通过了才存进这个浏览器。
 */
function OwnLLMForm({
  kind,
  current,
  disabled,
  onChecked,
  onDelete,
}: {
  kind: keyof typeof LLM_FORMS;
  current: LocalLLM | undefined;
  disabled: boolean;
  /** 试调通过的 LLM，地址和模型名按服务器规范化过的。 */
  onChecked: (llm: LocalLLM) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [protocol, setProtocol] = useState<LLMProtocol>("chat-completions");
  const [apiKey, setApiKey] = useState("");
  const [textEffort, setTextEffort] = useState(EFFORT_UNSET);
  const [callEffort, setCallEffort] = useState(EFFORT_UNSET);
  const [extraBody, setExtraBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const id = useId();

  // 档位换成接口写不出的（选着「极低」换成 Anthropic）是 undefined，额外请求参数不是 JSON 对象时带着 problem：都不能保存。
  // 后台模型只有 `textEffort` 那一档，`callEffort` 一直是不设置。
  const effortInText = effortOf(textEffort, protocol);
  const form = LLM_FORMS[kind];
  const effortInCalls = form.callEffort ? effortOf(callEffort, protocol) : null;
  const parsedExtraBody = parseExtraBody(extraBody);
  // 档位提示按表单里正在填的协议、地址和模型名查，不带密钥。
  const catalogHint = useEffortHint(protocol, editing ? baseUrl : "", model);
  const keyToTry = apiKey.trim() || current?.api_key;
  // 要试调的那份，还没填全或有哪项填不了时没有。不设置的几项不写，和存下它们以前的一样。
  const toTry: LocalLLM | null =
    baseUrl.trim() &&
    model.trim() &&
    keyToTry &&
    effortInText !== undefined &&
    effortInCalls !== undefined &&
    !("problem" in parsedExtraBody)
      ? {
          base_url: baseUrl.trim(),
          api_key: keyToTry,
          model: model.trim(),
          protocol,
          ...(effortInText && { reasoning_effort: effortInText }),
          ...(effortInCalls && { call_reasoning_effort: effortInCalls }),
          ...(parsedExtraBody.value && { extra_body: parsedExtraBody.value }),
        }
      : null;

  /** 打开表单，从存着的那份填起。 */
  function edit() {
    setBaseUrl(current?.base_url ?? "");
    setModel(current?.model ?? "");
    setProtocol(current?.protocol ?? "chat-completions");
    setTextEffort(current?.reasoning_effort ?? EFFORT_UNSET);
    setCallEffort(current?.call_reasoning_effort ?? EFFORT_UNSET);
    setExtraBody(extraBodyText(current?.extra_body));
    setFailure(null);
    setEditing(true);
  }

  // 向服务商查模型，用要试调的那把 key：填了新的用新的，没填用存着的。
  const listAt = baseUrl.trim();
  const lister: ModelLister | null =
    listAt && keyToTry
      ? { key: `${protocol} ${listAt} ${keyToTry}`, ask: () => listOwnLLMModels(protocol, listAt, keyToTry) }
      : null;

  /** 选了服务商或模型：填进协议、地址和模型名。 */
  function pick({ protocol, base_url, model }: LLMPick) {
    if (protocol !== undefined) setProtocol(protocol);
    if (base_url !== undefined) setBaseUrl(base_url);
    if (model !== undefined) setModel(model);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!toTry) return;
    setBusy(true);
    setFailure(null);
    try {
      onChecked({ ...toTry, ...(await form.check(toTry)) });
      setApiKey("");
      setEditing(false);
    } catch (e) {
      setFailure(toFailure(e));
    } finally {
      setBusy(false);
    }
  }

  const protocolOptions = LLM_PROTOCOLS.map((p) => ({ value: p, label: t.protocols[p] }));
  const effortLines = (llm: LocalLLM) => {
    if (!form.callEffort) return llm.reasoning_effort ? [t.memoryLlmThinking(effortLabel(llm.reasoning_effort))] : [];
    if (!llm.reasoning_effort && !llm.call_reasoning_effort) return [];
    return [
      t.llmThinking(
        effortLabel(llm.reasoning_effort ?? EFFORT_UNSET),
        effortLabel(llm.call_reasoning_effort ?? EFFORT_UNSET),
      ),
    ];
  };
  const summary = current
    ? [
        t.llmCurrent(current.model, current.base_url),
        t.last4(last4(current.api_key)),
        ...effortLines(current),
        ...(current.extra_body ? [t.llmExtraBody] : []),
      ]
    : [form.notSet];

  return (
    <ServiceKey
      title={form.title}
      hint={form.hint}
      summary={summary}
      isSet={!!current}
      editing={editing}
      disabled={disabled}
      onEdit={edit}
      onDelete={onDelete}
    >
      <form onSubmit={submit}>
        <FieldGroup>
          {/* 先选服务商、填 key，再选模型（能向服务商查）；下面三项是选了自动填的，也能手改 */}
          <LLMProviderPicker
            id={id}
            baseUrl={baseUrl}
            model={model}
            onPick={pick}
            lister={lister}
            between={
              <Field>
                <FieldLabel htmlFor={`${id}-key`}>{t.apiKey}</FieldLabel>
                <Input
                  id={`${id}-key`}
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  autoComplete="off"
                />
                {current && <FieldDescription>{t.keepApiKey}</FieldDescription>}
              </Field>
            }
          />
          <Field>
            <FieldLabel htmlFor={`${id}-protocol`}>{t.protocol}</FieldLabel>
            <Select
              items={protocolOptions}
              value={protocol}
              onValueChange={(value) => value && setProtocol(value as LLMProtocol)}
            >
              <SelectTrigger id={`${id}-protocol`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {protocolOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-base-url`}>{t.baseUrl}</FieldLabel>
            <Input
              id={`${id}-base-url`}
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://api.openai.com/v1"
              autoComplete="off"
            />
          </Field>
          <Field>
            <FieldLabel htmlFor={`${id}-model`}>{t.model}</FieldLabel>
            <Input
              id={`${id}-model`}
              value={model}
              onChange={(event) => setModel(event.target.value)}
              autoComplete="off"
            />
          </Field>
          <EffortField
            id={`${id}-effort`}
            label={form.effort}
            hint={`${form.effortHint}${copy.reasoningEffort.hints.effort}`}
            protocol={protocol}
            choice={textEffort}
            onChoose={setTextEffort}
            problem={effortInText === undefined ? copy.reasoningEffort.problems.effort(form.effort) : undefined}
            catalogHint={catalogHint}
            showCatalogNote
          />
          {form.callEffort && (
            <EffortField
              id={`${id}-call-effort`}
              label={t.callEffort}
              hint={copy.reasoningEffort.hints.callEffort}
              protocol={protocol}
              choice={callEffort}
              onChoose={setCallEffort}
              problem={effortInCalls === undefined ? copy.reasoningEffort.problems.effort(t.callEffort) : undefined}
              catalogHint={catalogHint}
            />
          )}
          <ExtraBodyField
            id={`${id}-extra-body`}
            label={t.extraBody}
            hint={copy.reasoningEffort.hints.extraBody}
            text={extraBody}
            onChange={setExtraBody}
            problem={
              "problem" in parsedExtraBody
                ? copy.reasoningEffort.problems[parsedExtraBody.problem](t.extraBody)
                : undefined
            }
          />
          {failure && <FailureNotice failure={failure} />}
          <FormButtons
            busy={busy}
            canSubmit={!!toTry}
            onCancel={() => setEditing(false)}
          />
        </FieldGroup>
      </form>
    </ServiceKey>
  );
}
