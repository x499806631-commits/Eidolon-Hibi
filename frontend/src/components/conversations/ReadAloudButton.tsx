"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { copy } from "@/i18n";
import { onReading, toggleReading } from "@/lib/readAloud";
import { ReadAloudIcon, StopReadingIcon } from "@/theme/icons";

const t = copy.conversation.readAloud;

/**
 * 她一条消息旁的小喇叭（DESIGN.md「对话页」）：点了用她的声音念这一条，正在念时换成「停」；
 * 没念成时提示框说一句。muted 的 ghost 图标键，不抢气泡的位置。
 */
export function ReadAloudButton({ messageKey, name }: { messageKey: string; name: string }) {
  const [state, setState] = useState<"idle" | "playing" | "failed">("idle");
  useEffect(
    () =>
      onReading((playing, failed) =>
        setState(playing === messageKey ? "playing" : failed === messageKey ? "failed" : "idle"),
      ),
    [messageKey],
  );
  const label = state === "playing" ? t.stop : state === "failed" ? t.failed : t.read(name);
  const Icon = state === "playing" ? StopReadingIcon : ReadAloudIcon;
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={label}
            aria-pressed={state === "playing"}
            className="shrink-0 text-muted-foreground"
            onClick={() => toggleReading(messageKey)}
          >
            <Icon />
          </Button>
        }
      />
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
