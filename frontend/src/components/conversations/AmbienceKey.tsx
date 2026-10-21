"use client";

import { useSyncExternalStore } from "react";

import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverHeader, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { copy } from "@/i18n";
import { AMBIENCE_SCENES, AMBIENCE_VOLUMES, AUTO, sceneById, type AmbienceVolume } from "@/lib/ambience";
import { AmbienceIcon } from "@/theme/icons";

const t = copy.call.ambience;

const OFF = "off";
const VOLUMES = Object.keys(AMBIENCE_VOLUMES) as AmbienceVolume[];

/**
 * 通话画面底部的环境音按钮（ASMR 模式）：样子同静音、切换麦克风那两个圆键，在放环境音时是按下的样子。
 * 点开向上弹出一个面板：「关闭」和每个场景一格（两列），按下的是正在放的；下面一行三档音量。
 * 选了马上换过去，也记下来，ASMR 模式的通话接通时自动放它。只在接通后能点。
 * 「自动」让判断模型按时间、季节和聊的内容挑，按下的是「自动」，提示里写挑了哪个。
 */
export function AmbienceKey({ isDisabled }: { isDisabled?: boolean }) {
  const { ambience } = useDirectory();
  const state = useSyncExternalStore(ambience.subscribe, ambience.snapshot, ambience.snapshot);
  const playing = sceneById(state.playing);
  const hint = !state.auto
    ? t.hint
    : playing
      ? t.autoPlaying(playing.label)
      : state.picked === "none"
        ? t.autoQuiet
        : t.autoWaiting;
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant="call-round"
            size="icon-call-dock"
            aria-label={t.label}
            aria-pressed={state.playing !== null || state.auto}
            disabled={isDisabled}
          />
        }
      >
        <AmbienceIcon aria-hidden />
      </PopoverTrigger>
      <PopoverContent side="top" className="max-h-[min(30rem,70dvh)] overflow-y-auto">
        <PopoverHeader>
          <PopoverTitle>{t.title}</PopoverTitle>
        </PopoverHeader>
        <ToggleGroup
          aria-label={t.title}
          size="sm"
          spacing={1}
          className="grid w-full grid-cols-2"
          value={[state.auto ? AUTO : (state.playing ?? OFF)]}
          onValueChange={(value: unknown[]) => {
            const next = value[0] as string | undefined;
            if (next) ambience.choose(next === OFF ? null : next);
          }}
        >
          <ToggleGroupItem value={OFF} className="justify-start">
            {t.off}
          </ToggleGroupItem>
          <ToggleGroupItem value={AUTO} className="justify-start">
            <span aria-hidden>🎲</span>
            {t.auto}
          </ToggleGroupItem>
          {AMBIENCE_SCENES.map((scene) => (
            <ToggleGroupItem key={scene.id} value={scene.id} className="justify-start">
              <span aria-hidden>{scene.icon}</span>
              {scene.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <div className="flex items-center gap-2">
          <span className="text-subtitle text-muted-foreground">{t.volume}</span>
          <ToggleGroup
            aria-label={t.volume}
            size="sm"
            spacing={0}
            value={[state.volume]}
            onValueChange={(value: unknown[]) => {
              const next = value[0] as AmbienceVolume | undefined;
              if (next) ambience.setVolume(next);
            }}
          >
            {VOLUMES.map((v) => (
              <ToggleGroupItem key={v} value={v}>
                {t.volumes[v]}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <p className="text-meta text-muted-foreground" aria-live="polite">
          {hint}
        </p>
      </PopoverContent>
    </Popover>
  );
}
