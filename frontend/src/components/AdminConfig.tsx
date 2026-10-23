"use client";

import { FormEvent, Fragment, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { AdminPage } from "@/components/app/admin/AdminPage";
import { MetadataList, MetadataListItem } from "@/components/app/admin/MetadataList";
import { Notice } from "@/components/app/admin/Notice";
import { NumberInput } from "@/components/app/admin/NumberInput";
import { StatusDot } from "@/components/app/StatusDot";
import { FailureNotice } from "@/components/app/FailureNotice";
import { LLMProviderPicker, type ModelLister } from "@/components/app/LLMProviderPicker";
import { ModelChoiceField } from "@/components/app/ModelChoiceField";
import { EffortField, ExtraBodyField, useEffortHint } from "@/components/app/ReasoningEffortFields";
import { OpenFormProvider, useOpenForm } from "@/components/admin/OpenForm";
import { AdminSponsor } from "@/components/admin/AdminSponsor";
import { ResetCardGrants } from "@/components/admin/ResetCardGrants";
import { useSubmitCheck } from "@/components/admin/useSubmitCheck";
import { AdminPrices } from "@/components/AdminPrices";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { copy } from "@/i18n";
import {
  changeOf,
  CONFIG_TABS,
  CUSTOM_MODEL,
  formOf,
  getAdminConfig,
  isAvailable,
  isConfigTab,
  ITEM_SPECS,
  lockedOn,
  MAX_COUNT,
  readOnlyValue,
  restoreItem,
  rows,
  SAME_AS_CALLS,
  saveItem,
  statusDisplay,
  TAB_ITEMS,
  tabHasFailure,
  tabHref,
  type AdminConfig as Config,
  type ConfigTab,
  type ConfigItem,
  type FieldRow,
  type FieldSpec,
  type Form,
  type ItemName,
  type ReadOnlyItem,
  UNLIMITED,
  listItemModels,
} from "@/lib/adminConfig";
import type { FishAudioModel, LLMProtocol } from "@/lib/localOwnKeys";
import { EFFORT_UNSET, effortLabel } from "@/lib/reasoningEffort";
import { toFailure, type Failure } from "@/lib/failure";
import { FailureIcon } from "@/theme/icons";

const t = copy.admin.config;

/**
 * 后台的「配置」页：页首下面一排分类标签（模型 / 语音 / 生图 / 网络 / 服务状态 / 限额与玩法 / 赞赏 / 单价 / 只读），一次只看一类；
 * 内容列窄的时候标签换成下拉（`TabSelect`）。
 * 每一项一张卡，标题旁用 Badge 标出覆盖的状态，列出当前生效的值（有后台覆盖时对照 .env 的值），
 * 可以在卡里展开表单修改、恢复成 .env 的值（DESIGN.md「Layout · 后台 · 配置」）。
 *
 * 当前标签记在地址里（`?tab=voice`），可以收藏、刷新不丢。一个标签里有覆盖没生效的项时，标签字后面加
 * 琥珀色点，不会因为没切过去就漏看。切走时各标签的内容留着不卸，没保存的表单切回来还在（除非在别的
 * 标签开了另一张表单，全页同一时间只开一张）。
 */
export function AdminConfig({ initialTab }: { initialTab: ConfigTab }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [tab, setTab] = useState<ConfigTab>(initialTab);

  useEffect(() => {
    getAdminConfig().then(setConfig, (e) => setFailure(toFailure(e)));
  }, []);

  function chooseTab(value: string) {
    if (!isConfigTab(value)) return;
    setTab(value);
    window.history.replaceState(null, "", tabHref(window.location.pathname, value));
  }

  return (
    <AdminPage width="narrow" title={copy.admin.pages.config} intro={t.intro}>
      <OpenFormProvider>
        {failure && !config && <FailureNotice failure={failure} />}
        {!failure && !config && (
          <p className="text-sm text-muted-foreground" role="status">
            {t.loading}
          </p>
        )}
        {config && (
          <Tabs value={tab} onValueChange={(value) => chooseTab(String(value))} className="@container gap-6">
            <TabSelect tab={tab} config={config} onChoose={chooseTab} />
            <TabsList aria-label={t.nav} className="hidden @lg:inline-flex">
              {CONFIG_TABS.map((name) => (
                <TabsTrigger key={name} value={name}>
                  {t.tabs[name]}
                  {tabHasFailure(config, name) && <StatusDot variant="warning" label={t.tabFailed} />}
                </TabsTrigger>
              ))}
            </TabsList>
            {CONFIG_TABS.map((name) => (
              <TabsContent key={name} value={name} keepMounted className="flex flex-col gap-6">
                {TAB_ITEMS[name].map((item) => (
                  <ItemCard key={item} name={item} config={config} onChange={setConfig}>
                    {item === "affinity" && <ResetCardGrants isOn={isOn(config.affinity)} />}
                  </ItemCard>
                ))}
                {name === "sponsor" && <AdminSponsor />}
                {name === "prices" && <AdminPrices />}
                {name === "read_only" && <ReadOnlyCard items={config.read_only} />}
              </TabsContent>
            ))}
          </Tabs>
        )}
      </OpenFormProvider>
    </AdminPage>
  );
}

/**
 * 内容列窄（< 32rem，手机上）时代替那排标签的下拉：一排放不下九个标签，滚动又会把琥珀色点藏到屏外。
 * 选项后面照样跟琥珀色点；收起时只要有一类有覆盖没生效，框里也跟一个点，提醒点开看。
 */
function TabSelect({ tab, config, onChoose }: { tab: ConfigTab; config: Config; onChoose: (value: string) => void }) {
  const items = CONFIG_TABS.map((name) => ({ value: name, label: t.tabs[name] }));
  const someFailed = CONFIG_TABS.some((name) => tabHasFailure(config, name));
  return (
    <Select items={items} value={tab} onValueChange={(value) => value && onChoose(String(value))}>
      <SelectTrigger aria-label={t.nav} className="w-full @lg:hidden">
        <SelectValue>
          {(value: ConfigTab) => (
            <>
              {t.tabs[value]}
              {someFailed && <StatusDot variant="warning" label={t.someTabFailed} />}
            </>
          )}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {CONFIG_TABS.map((name) => (
          <SelectItem key={name} value={name}>
            <span className="flex items-center gap-1.5">
              {t.tabs[name]}
              {tabHasFailure(config, name) && <StatusDot variant="warning" label={t.tabFailed} />}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** 好感度和礼物现在是否开着：配了判断模型，开关生效的值是打开。 */
function isOn(item: Config["affinity"]): boolean {
  return isAvailable(item) && item.effective.enabled === true;
}

/** 卡片的标题：标题本身是 h2（读屏可以按标题在卡之间跳），旁边可以跟一个状态 Badge。 */
function ItemTitle({ title, badge }: { title: string; badge?: ReactNode }) {
  return (
    <CardTitle className="flex flex-wrap items-center gap-2">
      <h2>{title}</h2>
      {badge}
    </CardTitle>
  );
}

/**
 * 一项的各个值。没有后台覆盖时 .env 的值就是当前生效的值，对照只会重复一遍，所以是定义列表「项：当前值」；
 * 有覆盖时是「项 / .env 的值 / 当前生效」的表，被盖掉的 .env 值划掉；覆盖没有生效的那一项，
 * 在当前生效的值下面注明后台覆盖的值和「已失效」。
 */
function Values({ name, rows: fieldRows }: { name: ItemName; rows: FieldRow[] }) {
  if (fieldRows.some((row) => row.overridden)) {
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t.columns.field}</TableHead>
            <TableHead>{t.columns.env}</TableHead>
            <TableHead>{t.columns.effective}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {fieldRows.map((row) => (
            <TableRow key={row.field}>
              <TableCell className="whitespace-normal text-muted-foreground">{fieldLabel(name, row.field)}</TableCell>
              <TableCell className="whitespace-normal break-all">
                {row.inEffect ? (
                  <del className="text-muted-foreground">{shown(name, row.field, row.env)}</del>
                ) : (
                  shown(name, row.field, row.env)
                )}
              </TableCell>
              <TableCell className="whitespace-normal break-all">
                {shown(name, row.field, row.effective)}
                {row.overridden && !row.inEffect && (
                  <span className="block text-xs text-muted-foreground">
                    {t.columns.override}：{shown(name, row.field, row.override)}（{t.notInEffect}）
                  </span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }
  return (
    <MetadataList>
      {fieldRows.map((row) => (
        <MetadataListItem key={row.field} label={fieldLabel(name, row.field)}>
          {shown(name, row.field, row.effective)}
        </MetadataListItem>
      ))}
    </MetadataList>
  );
}

/**
 * 一个值在表里的样子：key 只有末 4 位，max_tokens 没设时是接口类型的默认值，思考强度是「中文名（原值）」或「不设置」，
 * 额外请求参数是一行 JSON，服务、地域和语音合成的模型用界面上的叫法，限额的 0 是不限，开关是打开或关闭。
 */
function shown(name: ItemName, field: string, value: FieldRow["env"]): string {
  if (ITEM_SPECS[name].form.some((spec) => spec.name === field && spec.kind === "effort")) {
    return effortLabel(value === null ? EFFORT_UNSET : String(value));
  }
  if (field === "extra_body" && value !== null) return JSON.stringify(value);
  if (value === null || value === "") return field === "max_tokens" ? t.defaultMaxTokens : t.none;
  if (typeof value === "boolean") return value ? t.switchOn : t.switchOff;
  if (ITEM_SPECS[name].secrets.includes(field)) return t.last4(String(value));
  if (field === "protocol") return t.protocols[value as LLMProtocol] ?? String(value);
  if (field === "provider") return t.providers[String(value)] ?? String(value);
  if (field === "region") return t.regions[String(value)] ?? String(value);
  if (field === "api_format") return t.imageFormats[String(value)] ?? String(value);
  if (name === "tts" && field === "model") return optionLabel(name, field, String(value));
  if (name === "limits" && value === 0) return t.unlimited;
  if (field === "window_minutes") return t.minutes(String(value));
  return String(value);
}

/** 一项的名字：对话模型分文字和通话两项思考强度，文字那一项要写明。 */
function fieldLabel(name: ItemName, field: string): string {
  return t.itemFields[name]?.[field] ?? t.fields[field] ?? field;
}

/** 表单里一个字段的说明：key 留空不改，其余按字段给出取值范围；代理的开关里，这个部署没配置的服务注明不起作用。 */
function fieldHint(name: ItemName, spec: FieldSpec, keysEditable: boolean, item?: ConfigItem): string | undefined {
  if (spec.kind === "secret") {
    if (!keysEditable) return t.keysReadOnly;
    if (name === "proxy") return t.formHints.proxy_password;
    return name === "stt" ? t.formHints.stt_api_key : name === "judge" ? t.formHints.judge_api_key : t.apiKeyHint;
  }
  if (name === "proxy" || name === "service_status") {
    if (item?.unconfigured?.some((service) => service === spec.name)) return t.formHints[`${name}_unconfigured`];
    if (name === "service_status") return t.formHints[`service_status_${spec.name}`];
    return t.formHints[`proxy_${spec.name}`];
  }
  if (spec.kind === "maxTokens") return t.maxTokensHint;
  if (spec.kind === "probability") return t.formHints.probability;
  if (name === "judge" && spec.name === "model") return t.formHints.judge_model;
  if (name === "tts" && spec.name === "model") return t.formHints.tts_model;
  if (name === "image" && spec.name === "base_url") return t.formHints.image_base_url;
  return t.formHints[spec.name];
}

function optionLabel(name: ItemName, field: string, option: string): string {
  if (field === "protocol") return t.protocols[option as LLMProtocol] ?? option;
  if (field === "api_format") return t.imageFormats[option] ?? option;
  if (field === "region") return t.regions[option] ?? option;
  if (field === "window_minutes") return t.minutes(option);
  // 生图的模型：下拉里是官方模型的短名，最后一项是手填。
  if (name === "image" && field === "model") return option === CUSTOM_MODEL ? t.customModel : (t.imageModels[option] ?? option);
  // 语音合成的模型，和设置页一个叫法；念消息的那几项多一个「和通话一样」。
  if (option === SAME_AS_CALLS && (field === "read_engine" || field === "read_model")) return t.sameAsCalls;
  if (field === "engine" || field === "read_engine") return t.voiceEngines[option] ?? option;
  if (field === "model" || field === "read_model") return copy.settings.ownKeys.ttsModels[option as FishAudioModel] ?? option;
  return t.providers[option] ?? option;
}

/**
 * 一项配置的卡。.env 没有配置的项只说明为什么不能改；其余见 AvailableItem。`children` 跟在卡里各个值的下面
 * （「好感度和礼物」的发重置卡）。
 */
function ItemCard({
  name,
  config,
  onChange,
  children,
}: {
  name: ItemName;
  config: Config;
  onChange: (config: Config) => void;
  children?: ReactNode;
}) {
  const item = config[name];
  if (!isAvailable(item)) {
    return (
      <Card>
        <CardHeader>
          <ItemTitle title={t.items[name].title} />
          <CardDescription>{t.items[name].hint}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">{t.unavailable(item.unavailable)}</p>
          {children}
        </CardContent>
      </Card>
    );
  }
  return (
    <AvailableItem name={name} item={item} keysEditable={config.keys_editable} onChange={onChange}>
      {children}
    </AvailableItem>
  );
}

/**
 * 向服务商查模型槽能用的模型（ADR-0027）：没填 key 时用正在生效的那把，只在它生效的地址上用。
 * 还没填地址时是 null；不是模型槽是 undefined。
 */
function modelLister(name: ItemName, form: Form): ModelLister | null | undefined {
  if (name !== "chat_llm" && name !== "memory_llm") return undefined;
  const base_url = form.base_url?.trim() ?? "";
  const api_key = form.api_key?.trim() ?? "";
  if (!base_url) return null;
  return {
    key: `${name} ${form.protocol} ${base_url} ${api_key}`,
    ask: () => listItemModels(name, { protocol: form.protocol, base_url, ...(api_key && { api_key }) }),
  };
}

/**
 * 一项能改的配置：状态 Badge、各个值，卡里展开的修改表单（全页同一时间只开一张），CardFooter 里的
 * 「恢复成 .env 的值」。服务类的项保存时后端先试调，试不通照原话显示原因、不保存；阈值和限额不试调，
 * 保存键只写「保存」。
 */
function AvailableItem({
  name,
  item,
  keysEditable,
  onChange,
  children,
}: {
  name: ItemName;
  item: ConfigItem;
  keysEditable: boolean;
  onChange: (config: Config) => void;
  children?: ReactNode;
}) {
  const spec = ITEM_SPECS[name];
  const id = useId();
  // 同一时间只开一个表单：点了别的「修改」，这一项的表单就收起（见 OpenForm）。
  const [editing, setEditing] = useOpenForm(name);
  /** 是自己关的（取消、保存成功），不是因为别的表单打开而收起：只有这时才把焦点还给「修改」。 */
  const closedHere = useRef(false);
  const [form, setForm] = useState<Form>(() => formOf(spec, item.effective));
  const [busy, setBusy] = useState<"save" | "restore" | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const result = changeOf(spec, form, keysEditable);
  // 不先报错：点过一次保存才标出不对的项（见 useSubmitCheck）。
  const check = useSubmitCheck<HTMLFormElement>();
  const problemOf = (field: string) =>
    check.shown && "problems" in result ? (result.problems.find((p) => p.field === field) ?? null) : null;
  const fieldRows = rows(item, spec);
  const overridden = fieldRows.some((row) => row.overridden);
  const saveKind = spec.tried ? "tried" : "plain";
  const display = statusDisplay(item);
  const canRestore = display.canRestore;
  const formRef = check.ref;
  // 档位提示只在改模型时查，按表单里正在填的地址和模型名；不是模型的项没有地址，不查。
  const catalogHint = useEffortHint(form.protocol ?? "", editing ? (form.base_url ?? "") : "", form.model ?? "");

  // 打开表单时焦点移到第一个能填的框（「修改」随表单打开收起，焦点不移就落回页首）。
  const editRef = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);
  useEffect(() => {
    if (editing) formRef.current?.querySelector<HTMLElement>("input:not([disabled]), button:not([disabled])")?.focus();
    // 关掉表单（取消或保存）后焦点回到「修改」：它随表单关闭重新挂上，原来的那个元素已经不在了。
    else if (wasEditing.current && closedHere.current) editRef.current?.focus();
    wasEditing.current = editing;
    closedHere.current = false;
  }, [editing]);

  function edit() {
    setForm(formOf(spec, item.effective));
    setFailure(null);
    setNotice(null);
    check.reset();
    setEditing(true);
  }

  async function run(action: "save" | "restore", request: () => Promise<Config>, done: string) {
    setBusy(action);
    setFailure(null);
    setNotice(null);
    try {
      onChange(await request());
      closedHere.current = true;
      setEditing(false);
      setNotice(done);
    } catch (e) {
      setFailure(toFailure(e));
    } finally {
      setBusy(null);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if ("change" in result) void run("save", () => saveItem(name, result.change), t.saved);
    else check.refuse();
  }

  function cancel() {
    closedHere.current = true;
    setEditing(false);
  }

  const set = (field: string) => (value: string) => setForm((f) => ({ ...f, [field]: value }));

  return (
    <Card>
      <CardHeader>
        <ItemTitle
          title={t.items[name].title}
          badge={
            display.variant === "accent" ? (
              <Badge variant="secondary">{t.badges.overridden}</Badge>
            ) : display.variant === "warning" ? (
              <Badge variant="outline">{t.badges.failed}</Badge>
            ) : null
          }
        />
        <CardDescription>{t.items[name].hint}</CardDescription>
        {!editing && (
          <CardAction>
            <Button
              ref={editRef}
              variant="outline"
              size="sm"
              aria-label={copy.admin.actionOn(t.edit, t.items[name].title)}
              onClick={edit}
              disabled={busy !== null}
            >
              {t.edit}
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {item.follows_chat && <p className="text-muted-foreground">{t.followsChat}</p>}
        {item.model && <p className="text-muted-foreground">{t.forModel(item.model)}</p>}
        {display.failure && (
          <Alert>
            <FailureIcon />
            <AlertTitle>{t.status.failed}</AlertTitle>
            <AlertDescription>{t.failed(display.failure)}</AlertDescription>
          </Alert>
        )}
        {notice && <Notice text={notice} onDismiss={() => setNotice(null)} />}
        {failure && <FailureNotice failure={failure} />}
        {/* 修改时表单里已经填着当前的值：没有覆盖时上面那份「项：当前值」只是重复，收起；有覆盖时对照还有用，留着。 */}
        {(!editing || overridden) && <Values name={name} rows={fieldRows} />}
        {editing && (
          <form ref={formRef} onSubmit={submit} noValidate className="flex max-w-md flex-col gap-6">
            <FieldGroup>
              {spec.form.map((field) => {
                const label = fieldLabel(name, field.name);
                const problem = problemOf(field.name);
                const fieldId = `${id}-${field.name}`;
                if (field.kind === "limit") {
                  return (
                    <LimitInput
                      key={field.name}
                      id={fieldId}
                      label={label}
                      value={form[field.name]}
                      onChange={set(field.name)}
                      error={problem ? t.problems.limit(label) : null}
                    />
                  );
                }
                if (field.kind === "switch") {
                  const hint = fieldHint(name, field, keysEditable, item);
                  const locked = lockedOn(form, field.name);
                  return (
                    <Field key={field.name} orientation="horizontal" data-disabled={locked ? true : undefined}>
                      <FieldContent>
                        <FieldLabel htmlFor={fieldId}>{label}</FieldLabel>
                        {hint && <FieldDescription>{hint}</FieldDescription>}
                        {locked && <FieldDescription>{t.lastExemptingKey}</FieldDescription>}
                      </FieldContent>
                      <Switch
                        id={fieldId}
                        checked={form[field.name] === "true"}
                        onCheckedChange={(on) => set(field.name)(String(on))}
                        disabled={locked}
                      />
                    </Field>
                  );
                }
                if (field.kind === "effort") {
                  return (
                    <EffortField
                      key={field.name}
                      id={fieldId}
                      label={label}
                      hint={fieldHint(name, field, keysEditable)}
                      protocol={form.protocol}
                      choice={form[field.name]}
                      onChoose={set(field.name)}
                      problem={problem ? t.problems[problem.reason](label) : undefined}
                      catalogHint={catalogHint}
                      showCatalogNote={field.name === "reasoning_effort"}
                    />
                  );
                }
                if (field.kind === "extraBody") {
                  return (
                    <ExtraBodyField
                      key={field.name}
                      id={fieldId}
                      label={label}
                      hint={fieldHint(name, field, keysEditable)}
                      text={form[field.name]}
                      onChange={set(field.name)}
                      problem={problem ? t.problems[problem.reason](label) : undefined}
                    />
                  );
                }
                if (field.kind === "modelChoice") {
                  return (
                    <ModelChoiceField
                      key={field.name}
                      id={fieldId}
                      label={label}
                      options={(field.options ?? []).map((o) => ({ value: o, label: optionLabel(name, field.name, o) }))}
                      customLabel={optionLabel(name, field.name, CUSTOM_MODEL)}
                      value={form[field.name]}
                      onChange={set(field.name)}
                      error={problem ? t.problems[problem.reason](label) : null}
                    />
                  );
                }
                if (field.kind === "count" || field.kind === "positive") {
                  const hint = fieldHint(name, field, keysEditable);
                  return (
                    <Field key={field.name} data-invalid={problem ? true : undefined}>
                      {/* NumberInput 自己带读屏名，没有能指的 id：标题用 FieldTitle，和限额一样。 */}
                      <FieldTitle>{label}</FieldTitle>
                      <NumberInput
                        label={label}
                        value={form[field.name] === "" ? null : Number(form[field.name])}
                        onChange={(count) => set(field.name)(count === null ? "" : String(count))}
                        min={1}
                        max={field.kind === "count" ? MAX_COUNT : undefined}
                        isIntegerOnly
                        isInvalid={problem !== null}
                      />
                      {hint && <FieldDescription>{hint}</FieldDescription>}
                      {problem && <FieldError>{t.problems[problem.reason](label)}</FieldError>}
                    </Field>
                  );
                }
                /** 一项下拉选择。 */
                const selectField = () => {
                  const options = (field.options ?? []).map((o) => ({ value: o, label: optionLabel(name, field.name, o) }));
                  const hint = fieldHint(name, field, keysEditable);
                  return (
                    <Field key={field.name}>
                      <FieldLabel htmlFor={fieldId}>{label}</FieldLabel>
                      <Select
                        items={options}
                        value={form[field.name]}
                        onValueChange={(value) => value && set(field.name)(String(value))}
                      >
                        <SelectTrigger id={fieldId} className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {options.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              {option.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {hint && <FieldDescription>{hint}</FieldDescription>}
                    </Field>
                  );
                };
                // 模型的几项上面先放服务商和模型的下拉，选了填进协议、地址和模型名（ADR-0027）。
                if (field.name === "protocol" && "base_url" in form && "model" in form) {
                  return (
                    <Fragment key={field.name}>
                      <LLMProviderPicker
                        id={fieldId}
                        baseUrl={form.base_url}
                        model={form.model}
                        onPick={(pick) => setForm((f) => ({ ...f, ...pick }))}
                        lister={modelLister(name, form)}
                      />
                      {selectField()}
                    </Fragment>
                  );
                }
                if (field.kind === "select" || field.kind === "minutes") return selectField();
                const secret = field.kind === "secret";
                const hint = fieldHint(name, field, keysEditable);
                return (
                  <Field key={field.name} data-invalid={problem ? true : undefined} data-disabled={secret && !keysEditable ? true : undefined}>
                    <FieldLabel htmlFor={fieldId}>
                      {label}
                      {(field.kind === "optionalText" || field.kind === "clearableText") && (
                        <span className="font-normal text-muted-foreground">{copy.common.optional}</span>
                      )}
                    </FieldLabel>
                    <Input
                      id={fieldId}
                      type={secret ? "password" : "text"}
                      value={form[field.name]}
                      onChange={(event) => set(field.name)(event.target.value)}
                      disabled={secret && !keysEditable}
                      aria-invalid={problem ? true : undefined}
                      autoComplete="off"
                    />
                    {hint && <FieldDescription>{hint}</FieldDescription>}
                    {problem && <FieldError>{t.problems[problem.reason](label)}</FieldError>}
                  </Field>
                );
              })}
            </FieldGroup>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={busy !== null}>
                {busy === "save" && <Spinner data-icon="inline-start" />}
                {busy === "save" ? t.saving[saveKind] : t.save[saveKind]}
              </Button>
              <Button type="button" variant="ghost" onClick={cancel} disabled={busy !== null}>
                {copy.common.cancel}
              </Button>
            </div>
          </form>
        )}
        {children}
      </CardContent>
      {canRestore && !editing && (
        <CardFooter>
          <Button
            variant="ghost"
            size="sm"
            disabled={busy !== null}
            onClick={() => void run("restore", () => restoreItem(name), t.restored)}
          >
            {busy === "restore" && <Spinner data-icon="inline-start" />}
            {t.restore}
          </Button>
        </CardFooter>
      )}
    </Card>
  );
}

/**
 * 一项限额：先选「设定上限」还是「不限」，选了上限再填 1 以上的数。不让 0 当「不限」：
 * 用户的单独限额里 0 是「只能用自带密钥」，同一个数字两处意思相反容易配错。
 * 从「不限」切回「设定上限」时填回刚才的数。
 */
function LimitInput({
  id,
  label,
  value,
  onChange,
  error,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error: string | null;
}) {
  const unlimited = value === UNLIMITED;
  const lastCount = useRef(unlimited ? "" : value);
  const titleId = `${id}-title`;
  return (
    // 不标 data-invalid：它会把整个 Field 的字染红，连「设定上限 / 不限」两个键也变红；输入框的红框和下面的说明就够了。
    <Field>
      <FieldTitle id={titleId}>{label}</FieldTitle>
      <div className="flex flex-wrap items-start gap-3">
        <ToggleGroup
          aria-labelledby={titleId}
          variant="outline"
          spacing={0}
          value={[unlimited ? "unlimited" : "number"]}
          onValueChange={(choice) => {
            // 两个里总要选一个：再点一下已选的那个不取消。
            const next = choice[0];
            if (next) onChange(next === "unlimited" ? UNLIMITED : lastCount.current);
          }}
        >
          <ToggleGroupItem value="number">{t.limitChoices.number}</ToggleGroupItem>
          <ToggleGroupItem value="unlimited">{t.limitChoices.unlimited}</ToggleGroupItem>
        </ToggleGroup>
        {!unlimited && (
          <NumberInput
            label={label}
            value={value === "" ? null : Number(value)}
            onChange={(count) => {
              lastCount.current = count === null ? "" : String(count);
              onChange(lastCount.current);
            }}
            min={1}
            isIntegerOnly
            isInvalid={error !== null}
          />
        )}
      </div>
      {error && <FieldError>{error}</FieldError>}
    </Field>
  );
}

/** 「EIDOLON_SECRET_KEY」这类名字窄列里放不下时只在下划线后折行，不从词中间断开。 */
function BreakAtUnderscores({ text }: { text: string }) {
  const parts = text.split("_");
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {part}
          {i < parts.length - 1 && (
            <>
              _<wbr />
            </>
          )}
        </Fragment>
      ))}
    </>
  );
}

/** 只能查看的项：当前的值（密钥只说设没设）和为什么不能在后台改。宽屏（≥ 1024px）是表，更窄时换成行。 */
function ReadOnlyCard({ items }: { items: ReadOnlyItem[] }) {
  const wide = useMediaQuery("(min-width: 1024px)", true);
  const value = (row: ReadOnlyItem) => readOnlyValue(row.value, { ...t.readOnly, none: t.none });
  return (
    <Card>
      <CardHeader>
        <ItemTitle title={t.readOnly.title} />
        <CardDescription>{t.readOnly.hint}</CardDescription>
      </CardHeader>
      <CardContent>
        {wide ? (
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-1/6">{t.readOnly.columns.name}</TableHead>
                <TableHead className="w-1/3">{t.readOnly.columns.value}</TableHead>
                <TableHead>{t.readOnly.columns.why}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((row) => (
                <TableRow key={row.name}>
                  <TableCell className="align-top whitespace-normal">
                    <BreakAtUnderscores text={t.readOnly.names[row.name] ?? row.name} />
                  </TableCell>
                  <TableCell className="align-top whitespace-normal break-all">{value(row)}</TableCell>
                  <TableCell className="align-top whitespace-normal text-muted-foreground">{row.why}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <ul className="flex flex-col divide-y">
            {items.map((row) => (
              <li key={row.name} className="flex flex-col gap-0.5 py-2.5 first:pt-0 last:pb-0">
                <span className="font-medium">{t.readOnly.names[row.name] ?? row.name}</span>
                <span className="break-all">{value(row)}</span>
                <span className="text-xs text-muted-foreground">{row.why}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
