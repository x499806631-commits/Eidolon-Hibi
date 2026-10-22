// 设置「通话」里的「语音合成」（GLOSSARY.md「朗读」「本地语音」）：这个部署通话和念消息用什么说话、Fish 用哪个模型、
// 自己的 GPT-SoVITS 在哪。改的是整个部署（和后台「语音合成」是同一份），只有部署者能看到（`Account.voice_settings`）。

import { api, json } from "@/lib/api";
import type { FishAudioModel } from "@/lib/localOwnKeys";

export type VoiceEngine = "fish" | "gpt_sovits";

export type VoiceSettings = {
  engine: VoiceEngine;
  model: FishAudioModel;
  read_engine: VoiceEngine | "same";
  read_model: FishAudioModel | "same";
  gpt_sovits_url: string;
};

/** 读到的：设置本身，有没有 Fish Audio 的 key，哪几位在 voices.toml 里配了本地声音。 */
export type VoiceSettingsShown = VoiceSettings & { fish_key: boolean; local_voices: string[] };

export async function getVoiceSettings(): Promise<VoiceSettingsShown> {
  return json(await api("/api/voice-settings"));
}

/** 保存前后端会先试一下（Fish 的余额、GPT-SoVITS 连不连得上），试不过就不存，说原因。 */
export async function saveVoiceSettings(settings: VoiceSettings): Promise<VoiceSettingsShown> {
  return json(
    await api("/api/voice-settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(settings),
    }),
  );
}
