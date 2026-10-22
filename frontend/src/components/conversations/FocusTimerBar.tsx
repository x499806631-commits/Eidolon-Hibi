"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { copy } from "@/i18n";
import { formatLeft, pause, resume, start, tick, type Timer } from "@/lib/focusTimer";
import { CloseIcon, FocusIcon, PauseFocusIcon, ResumeFocusIcon } from "@/theme/icons";

const t = copy.conversation.focus;

/** 提醒休息的那一声：复用环境音里的翻页声，不另做音效。 */
const CHIME = "/ambience/pages.mp3";

/**
 * 番茄钟「一起专心」（对话页首下方一条计时条）：专心她选的那段时间、休息 5 分钟，如此循环。
 * 一条上依次是图标、剩余时间（休息时换成她说的话）、暂停/继续、结束。
 *
 * 时间按绝对时刻走（`lib/focusTimer`），所以标签页被挂起、定时器被节流，回来时也对得上。
 * 到点换段时放一声提示音；第一版只在前端提示，不往对话里写任何东西。
 */
export function FocusTimerBar({
  name,
  minutes,
  onEnd,
  inert,
}: {
  /** 她的名字：休息那句「{名字}说：休息一下吧～」用。 */
  name: string;
  /** 专心一段多长，分钟：在 `FocusSetup` 里选的，休息完回到专心还按它；休息固定 5 分钟。 */
  minutes: number;
  /** 结束计时：这条从页面上拿掉。 */
  onEnd: () => void;
  /** 通话层铺开盖住这一栏时，别让 Tab 摸到这条。 */
  inert?: boolean;
}) {
  const [timer, setTimer] = useState<Timer>(() => start(Date.now(), minutes));
  // 上一段是哪一段：换段时只放一次提示音。
  const phase = useRef(timer.phase);

  useEffect(() => {
    const walking = setInterval(() => setTimer((now) => tick(now, Date.now())), 1000);
    return () => clearInterval(walking);
  }, []);

  useEffect(() => {
    if (timer.phase === phase.current) return;
    phase.current = timer.phase;
    if (timer.phase !== "break") return;
    try {
      // 浏览器不让自动播放时静默失败：计时照旧，只是没响
      void new Audio(CHIME).play().catch(() => {});
    } catch {
      // 连 Audio 都建不出来也一样：不影响计时
    }
  }, [timer.phase]);

  const paused = timer.until === null;
  return (
    <div inert={inert} className="flex h-10 shrink-0 items-center gap-1 px-3">
      <FocusIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
      <p className="min-w-0 flex-1 truncate px-1 text-meta text-muted-foreground">
        {timer.phase === "break" ? t.resting(name) : t.left(formatLeft(timer.left))}
      </p>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={paused ? t.resume : t.pause}
        onClick={() => setTimer((now) => (now.until === null ? resume(now, Date.now()) : pause(now, Date.now())))}
      >
        {paused ? <ResumeFocusIcon className="size-4" /> : <PauseFocusIcon className="size-4" />}
      </Button>
      <Button variant="ghost" size="icon-sm" aria-label={t.end} onClick={onEnd}>
        <CloseIcon className="size-4" />
      </Button>
    </div>
  );
}
