"use client";

import { useId, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { SettingsSection } from "@/components/app/SettingsSection";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { copy } from "@/i18n";
import { FEATURES, switchFeature, WITHIN, type Feature } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";

const t = copy.settings.features;

/**
 * 设置「玩法」（GLOSSARY.md「玩法开关」）：这个部署有的每个新玩法一个开关，默认都开着，关了只影响自己、所有偶像一起。
 * 同「搭话」：存在服务器上，一拨马上存，存不上就拨回去，照原话说明；值在导航外壳持有的账号里，一级的摘要和导航跟着变。
 * 作息和洗澡在行程里：行程关着时它们的开关按不动，下面写「跟着「行程」一起关着」，自己的值留着。
 */
export function FeaturesSettings() {
  const { account, accountChanged } = useDirectory();
  const id = useId();
  const [saving, setSaving] = useState<Feature | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  if (!account) return null;
  const switches = account.features;
  const shown = FEATURES.filter((feature) => switches[feature] !== undefined);

  const change = (feature: Feature, next: boolean) => {
    const before = switches[feature];
    setFailure(null);
    setSaving(feature);
    accountChanged((current) => ({ ...current, features: { ...current.features, [feature]: next } }));
    switchFeature(feature, next).then(
      () => setSaving(null),
      (e) => {
        accountChanged((current) => ({ ...current, features: { ...current.features, [feature]: before } }));
        setFailure(toFailure(e));
        setSaving(null);
      },
    );
  };

  return (
    <SettingsSection>
      <FieldGroup>
        <p className="text-subtitle text-muted-foreground">{t.intro}</p>
        {shown.map((feature) => {
          const within = WITHIN[feature];
          const heldOff = within !== undefined && switches[within] === false;
          const item = t.items[feature];
          return (
            <Field key={feature} orientation="horizontal" data-disabled={heldOff || undefined}>
              <FieldContent>
                <FieldLabel htmlFor={`${id}-${feature}`}>{item.label}</FieldLabel>
                <FieldDescription>{heldOff ? t.within : item.hint}</FieldDescription>
              </FieldContent>
              <Switch
                id={`${id}-${feature}`}
                checked={!heldOff && switches[feature] === true}
                disabled={heldOff || saving !== null}
                onCheckedChange={(next) => change(feature, next)}
              />
            </Field>
          );
        })}
        {failure && <FailureNotice failure={failure} />}
      </FieldGroup>
    </SettingsSection>
  );
}
