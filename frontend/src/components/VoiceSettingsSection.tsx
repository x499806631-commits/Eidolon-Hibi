"use client";

import { useEffect, useId, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { SettingsSection } from "@/components/app/SettingsSection";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import { toFailure, type Failure } from "@/lib/failure";
import { FISH_AUDIO_MODELS } from "@/lib/localOwnKeys";
import { getVoiceSettings, saveVoiceSettings, type VoiceSettings, type VoiceSettingsShown } from "@/lib/voiceSettings";

const t = copy.settings.voice;
const models = copy.settings.ownKeys.ttsModels;

type Option = { value: string; label: string };

/**
 * 设置「通话」最下面一节「语音合成」，只有部署者看得到（`Account.voice_settings`）：通话用什么说话、通话的 Fish 模型、
 * 念消息用什么、念消息的 Fish 模型、GPT-SoVITS 的地址，一个「保存」。保存前后端试一下，试不过就不存、说原因；存了马上生效，
 * 不用重建。下面一句写 voices.toml 里配了哪几位。
 */
export function VoiceSettingsSection() {
  const { account, characters } = useDirectory();
  const id = useId();
  const [shown, setShown] = useState<VoiceSettingsShown | null>(null);
  const [form, setForm] = useState<VoiceSettings | null>(null);
  const [isSaving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const allowed = account?.voice_settings === true;

  useEffect(() => {
    if (!allowed) return;
    let current = true;
    getVoiceSettings().then(
      (s) => {
        if (!current) return;
        setShown(s);
        setForm(s);
      },
      (e) => current && setFailure(toFailure(e)),
    );
    return () => {
      current = false;
    };
  }, [allowed]);

  if (!allowed) return null;
  const change = (next: Partial<VoiceSettings>) => {
    setSaved(false);
    setForm((f) => f && { ...f, ...next });
  };
  const save = () => {
    if (!form) return;
    setSaving(true);
    setFailure(null);
    saveVoiceSettings(form).then(
      (s) => {
        setShown(s);
        setForm(s);
        setSaved(true);
      },
      (e) => setFailure(toFailure(e)),
    ).finally(() => setSaving(false));
  };

  const engines: Option[] = [
    { value: "fish", label: t.engines.fish },
    { value: "gpt_sovits", label: t.engines.gpt_sovits },
  ];
  const fishModels: Option[] = FISH_AUDIO_MODELS.map((m) => ({ value: m, label: models[m] }));
  const same: Option = { value: "same", label: t.same };
  const reading = form ? (form.read_engine === "same" ? form.engine : form.read_engine) : "fish";
  const usesGpt = form ? form.engine === "gpt_sovits" || reading === "gpt_sovits" : false;
  const names = (shown?.local_voices ?? []).map((c) => characters?.find((x) => x.id === c)?.name ?? c);

  return (
    <SettingsSection title={t.title} description={t.description}>
      {!form ? (
        failure ? <FailureNotice failure={failure} /> : <Spinner className="text-muted-foreground" />
      ) : (
        <FieldGroup>
          <Choice id={`${id}-engine`} label={t.engine} options={engines} value={form.engine} onChange={(v) => change({ engine: v as VoiceSettings["engine"] })} />
          {form.engine === "fish" && (
            <Choice id={`${id}-model`} label={t.model} options={fishModels} value={form.model} onChange={(v) => change({ model: v as VoiceSettings["model"] })} />
          )}
          <Choice
            id={`${id}-read`}
            label={t.readEngine}
            hint={t.readEngineHint}
            options={[same, ...engines]}
            value={form.read_engine}
            onChange={(v) => change({ read_engine: v as VoiceSettings["read_engine"] })}
          />
          {reading === "fish" && (
            <Choice
              id={`${id}-read-model`}
              label={t.readModel}
              options={[same, ...fishModels]}
              value={form.read_model}
              onChange={(v) => change({ read_model: v as VoiceSettings["read_model"] })}
            />
          )}
          {usesGpt && (
            <Field>
              <FieldLabel htmlFor={`${id}-url`}>{t.url}</FieldLabel>
              <Input id={`${id}-url`} value={form.gpt_sovits_url} onChange={(e) => change({ gpt_sovits_url: e.target.value.trim() })} />
              <FieldDescription>
                {t.urlHint} {names.length > 0 ? t.localVoices(names.join("、")) : t.noLocalVoices}
              </FieldDescription>
            </Field>
          )}
          {!shown?.fish_key && <p className="text-subtitle text-muted-foreground">{t.noFishKey}</p>}
          {failure && <FailureNotice failure={failure} />}
          <div className="flex items-center gap-3">
            <Button disabled={isSaving} onClick={save}>
              {isSaving && <Spinner data-icon="inline-start" />}
              {t.save}
            </Button>
            {saved && <span className="text-subtitle text-muted-foreground">{t.saved}</span>}
          </div>
        </FieldGroup>
      )}
    </SettingsSection>
  );
}

function Choice({
  id,
  label,
  hint,
  options,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  options: Option[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Select items={options} value={value} onValueChange={(next) => typeof next === "string" && onChange(next)}>
        <SelectTrigger id={id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {hint && <FieldDescription>{hint}</FieldDescription>}
    </Field>
  );
}
