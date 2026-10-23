"use client";

import { useEffect, useRef, useState } from "react";

import type { Outfit } from "@/lib/api";
import type { StandDirection } from "@/lib/standAnimations";
import { isPestering, type TouchPart } from "@/lib/standTouch";
import { cn } from "@/lib/utils";

import { loadSpineRuntime } from "./spineRuntime";
import { StandEngine, type Framing } from "./StandEngine";

type Props = {
  /** 依次试的衣装：选中的那套，再是默认衣装（`outfitsToTry`）。变了就在原地换上。 */
  outfits: readonly Outfit[];
  framing: Framing;
  /** 通话层收起时暂停渲染，展开时从当前姿势接着画。 */
  isPaused: boolean;
  /** 到了之后怎么出现：手机「大立绘」从底部升起；电脑和减少动态效果时原地淡入。 */
  entrance: "rise" | "fade";
  /** 她现在该是什么样子（spec「立绘指令」）。 */
  direction: StandDirection;
  /** 减少动态效果：身体动作一律用待机，从下一个动作开始生效。 */
  isReducedMotion: boolean;
  /** 每试完一套衣装：载入了没有（换衣装的预览据此判断画面上是不是这一套）。 */
  onTried?: (outfit: Outfit, isLoaded: boolean) => void;
  /** 画布的像素比上限，默认同通话层（`MAX_PIXEL_RATIO`）。挂载时定下，之后不变。 */
  maxPixelRatio?: number;
  /** 静帧：只画一帧就停（落地页的「减少动态效果」档）。挂载时定下，之后不变。 */
  isStill?: boolean;
  /**
   * 能摸她（FORK.md「立绘触摸」）：点在头、脸颊、手、肩膀上她马上有反应，再告诉用的地方点了哪儿、
   * 是不是一直在戳她。不给就点不到，同原来一样。
   */
  onTouch?: (part: TouchPart, pestered: boolean) => void;
};

/**
 * 立绘（spec「通话层」「衣装与换衣装」）：铺满外框的一块画布，外框的位置和大小由用的地方决定
 * （通话层、换衣装的大预览）。加载中什么都不画（只露出背景图和纱），到了用 0.3 秒淡入。
 * 衣装依次试，一套加载不到就换下一套；都不行时画布一直是空的，同样只剩背景图，不提示用户；
 * 换一通电话（重新挂载）或者换一套衣装再试。已经画出来之后换衣装失败的，身上那套照旧。
 * runtime 加载不到、浏览器不支持 WebGL 时整块去掉，这次挂载不再重试。
 */
export function Stand({
  outfits,
  framing,
  isPaused,
  entrance,
  direction,
  isReducedMotion,
  onTried,
  maxPixelRatio,
  isStill = false,
  onTouch,
}: Props) {
  const taps = useRef<number[]>([]);
  // 按衣装的值而不是数组来载入：角色列表或衣装清单重新拉取时，同样的衣装不会让立绘重新载入。
  const wanted = JSON.stringify(outfits.map(({ id, url }) => ({ id, url })));
  const canvas = useRef<HTMLCanvasElement>(null);
  const engine = useRef<StandEngine | null>(null);
  const [ready, setReady] = useState<StandEngine | null>(null);
  /** 加载中（包括几套衣装都加载不到）、画出来了，或者这台设备上画不了。 */
  const [phase, setPhase] = useState<"loading" | "shown" | "failed">("loading");

  // 引擎要等 runtime 和素材，晚于这几个 effect 创建：创建时按这里记下的最新取景、暂停状态和立绘指令来。
  const current = useRef({ framing, isPaused, direction, isReducedMotion, maxPixelRatio, isStill });
  const tried = useRef(onTried);
  useEffect(() => {
    tried.current = onTried;
  });

  useEffect(() => {
    current.current.framing = framing;
    if (engine.current) engine.current.framing = framing;
  }, [framing]);

  useEffect(() => {
    current.current.isPaused = isPaused;
    engine.current?.setPaused(isPaused);
  }, [isPaused]);

  useEffect(() => {
    current.current.direction = direction;
    current.current.isReducedMotion = isReducedMotion;
    engine.current?.direct(direction, isReducedMotion);
  }, [direction, isReducedMotion]);

  // 引擎每次挂载只建一个：runtime 到了就建，之后换衣装都在它上面换。
  useEffect(() => {
    const target = canvas.current;
    if (!target) return;
    let cancelled = false;
    let created: StandEngine | null = null;
    loadSpineRuntime()
      .then((spine) => {
        if (cancelled) return;
        created = new StandEngine(target, spine, current.current.framing, current.current.maxPixelRatio);
        created.setStill(current.current.isStill);
        created.setPaused(current.current.isPaused);
        created.direct(current.current.direction, current.current.isReducedMotion);
        engine.current = created;
        setReady(created);
      })
      .catch(() => {
        if (!cancelled) setPhase("failed");
      });
    return () => {
      cancelled = true;
      created?.dispose();
      engine.current = null;
    };
  }, []);

  // 依次试这几套衣装，换上第一套载入成功的。
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    const tryFrom = async (queue: Outfit[]): Promise<void> => {
      const [outfit, ...rest] = queue;
      if (!outfit) return;
      try {
        await ready.load(outfit);
        if (cancelled) return;
        setPhase("shown");
        tried.current?.(outfit, true);
      } catch {
        if (cancelled) return;
        tried.current?.(outfit, false);
        return tryFrom(rest);
      }
    };
    void tryFrom(JSON.parse(wanted) as Outfit[]);
    return () => {
      cancelled = true;
    };
  }, [ready, wanted]);

  if (phase === "failed") return null;
  const shown = phase === "shown";
  // 画布铺满外框。到了之后 0.3 秒淡入（spec「通话层」）；手机上同时从下方 12% 处升起，
  // 曲线和时长与通话层的滑动一样（410ms、ease-call）。
  const partAt = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return engine.current?.touchedAt(e.clientX - rect.left, e.clientY - rect.top) ?? null;
  };
  return (
    <canvas
      ref={canvas}
      aria-hidden
      onPointerDown={
        onTouch &&
        ((e) => {
          const part = partAt(e);
          if (!part) return;
          const now = Date.now();
          taps.current = [...taps.current.filter((t) => now - t < 10_000), now];
          const pestered = isPestering(taps.current, now);
          if (pestered) taps.current = [];
          engine.current?.react(part, pestered);
          onTouch(part, pestered);
        })
      }
      onPointerMove={
        onTouch &&
        ((e) => {
          e.currentTarget.style.cursor = partAt(e) ? "pointer" : "";
        })
      }
      className={cn(
        onTouch && shown && "pointer-events-auto touch-manipulation",
        "block size-full [transition:opacity_300ms_linear,translate_410ms_cubic-bezier(0.24,1,0.4,1)]",
        shown ? "opacity-100" : "opacity-0",
        entrance === "rise" && !shown ? "translate-y-[12%]" : "translate-y-0",
      )}
    />
  );
}
