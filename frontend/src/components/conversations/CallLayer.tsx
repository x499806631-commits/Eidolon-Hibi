"use client";

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ComponentType,
  type ReactNode,
  type RefObject,
  type SVGProps,
} from "react";

import { AmbienceKey } from "@/components/conversations/AmbienceKey";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { copy } from "@/i18n";
import { cueText } from "@/lib/affinity";
import { photoImageUrl, type Character, type Outfit, type Photo, type Segment } from "@/lib/api";
import { endCard, type CallEnd } from "@/lib/callEnd";
import { clock, showTime } from "@/lib/callTime";
import { giftsHref } from "@/lib/gifts";
import { photoFailureNote } from "@/lib/photoFailure";
import type { OwnService } from "@/lib/localOwnKeys";
import { settingsHref } from "@/lib/settingsCategory";
import {
  NOT_OPEN,
  type AffinityCue,
  type LiveConversation,
  type MicNotice,
  type ShownCall,
} from "@/lib/liveConversation";
import type { StandDirection } from "@/lib/standAnimations";
import { cn } from "@/lib/utils";
import {
  CloseIcon,
  CollapseIcon,
  GiftIcon,
  HangUpIcon,
  MicIcon,
  MicOffIcon,
  MicSwitchIcon,
  PhoneIcon,
  PhotoFailedIcon,
  PhotoIcon,
  SendIcon,
  TextOnlyIcon,
  TypingIcon,
} from "@/theme/icons";
import { Ja } from "@/theme/Ja";

import { CueArrowIcon } from "./AffinityBadge";
import { PhotoViewer } from "./PhotoViewer";
import { Stand } from "./stand/Stand";
import { useCallTime } from "./useCallTime";
import { useProducerTimezone } from "./useProducerTimezone";

const t = copy.call;

type Props = {
  character: Character;
  call: ShownCall;
  /** 收起：回到 IM（或者通话层展开前所在的那一页），通话照常继续。收起时整层 inert，焦点由画它的那一页送回页上。 */
  isCollapsed: boolean;
  /** 通话层这一整层；画它的那一页用它看焦点在不在层里。 */
  layerRef: RefObject<HTMLElement | null>;
  /** 手机上占满整屏；电脑上只盖住画它的那一块（她的对话页上是对话栏，别的外壳页面上是当前页）。 */
  isFullScreen: boolean;
  isReducedMotion: boolean;
  micMuted: boolean;
  /** 麦克风出了状况时要说的话。 */
  micNotice: MicNotice | null;
  textOnly: boolean;
  /** 她当前这句；还没开口时为 null。 */
  line: Segment | null;
  /** 这通电话里她的上一句，只看文字时以浅色留在当前这句上面；还没有时为 null。 */
  previousLine: Segment | null;
  /** 用户正在说、还没定稿的话。 */
  hearing: string;
  /** 底栏换成了输入栏。 */
  typing: boolean;
  /** 这通电话里刚打出去的话，她开口前留在台词卡底部；没有时为 null。 */
  typed: string | null;
  /** 输入框里的字，和对话页的输入栏是同一份。 */
  draft: string;
  /** 这通电话里她最近发来的照片，生成中、发出来了或者没发出来；还没有时为 null。 */
  photo: Photo | null;
  /** 刚刚一个回合让她的好感度有了变化，约 2.6 秒；状态行这时写这句话。 */
  affinityCue: AffinityCue | null;
  /** 她的立绘该是什么样子；她没有立绘时用不上。 */
  standDirection: StandDirection;
  /** 立绘依次试的衣装：选中的那套，再是默认衣装；她没有立绘时用不上。 */
  standOutfits: readonly Outfit[];
  onCollapse: () => void;
  onHangUp: () => void;
  onToggleMic: () => void;
  /** 打开麦克风列表。 */
  onSwitchMic: () => void;
  onToggleTextOnly: () => void;
  onOpenTyping: () => void;
  onCloseTyping: () => void;
  onDraft: (draft: string) => void;
  onSend: () => void;
  onRedial: () => void;
  onDismiss: () => void;
  /** 结束页上「去设置自带密钥」。 */
  onOwnKeys: () => void;
  /** 礼物开放着（好感度可用）：她因为冷却挂断时，结束卡的主按钮是「送她一件礼物」。 */
  giftsOpen: boolean;
  /** 结束页上「送她一件礼物」。 */
  onGift: () => void;
  /** 这个部署的免限额密钥：超限的结束卡点名它们；账号还没读到时为 null。 */
  exemptingKeys: readonly OwnService[] | null;
};

const notOpen = () => NOT_OPEN;

/**
 * 画外壳持有的那段对话的通话层：她的对话页上画在对话栏里，别的外壳页面上由外壳画在当前页（ADR-0018）。
 * 收没收起、画哪一通都在 `live` 的快照里，两处画出来是同一个通话层；还没打过电话时什么都不画。
 * 结束卡上去别处的主按钮（自带密钥、送礼）由外壳的 `go` 按去处规则去。
 */
export function LiveCallLayer({
  live,
  character,
  standOutfits,
  layerRef,
  isFullScreen,
  go,
  exemptingKeys,
}: {
  live: LiveConversation;
  character: Character;
  standOutfits: readonly Outfit[];
  layerRef: RefObject<HTMLElement | null>;
  isFullScreen: boolean;
  go: (href: string) => void;
  exemptingKeys: readonly OwnService[] | null;
}) {
  const snapshot = useSyncExternalStore(live.subscribe, live.snapshot, notOpen);
  const isReducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)", false);
  const { layer } = snapshot;
  if (!layer) return null;
  // 先看完结束卡再去：不然通话层在要去的页面上（由外壳画）还展开着结束卡，挡住要去的地方。
  const leave = (href: string) => {
    live.dismissEnd();
    go(href);
  };
  return (
    <CallLayer
      character={character}
      call={layer.call}
      isCollapsed={layer.collapsed}
      layerRef={layerRef}
      isFullScreen={isFullScreen}
      isReducedMotion={isReducedMotion}
      micMuted={snapshot.micMuted}
      micNotice={snapshot.micNotice}
      textOnly={snapshot.textOnly}
      line={snapshot.line}
      previousLine={snapshot.previousLine}
      hearing={snapshot.hearing}
      typing={snapshot.typing}
      typed={snapshot.typed}
      draft={snapshot.draft}
      photo={snapshot.callPhoto}
      affinityCue={snapshot.affinityCue}
      standDirection={snapshot.stand}
      standOutfits={standOutfits}
      onCollapse={() => live.collapse()}
      onHangUp={() => void live.hangUp()}
      onToggleMic={() => live.toggleMic()}
      onSwitchMic={() => live.openMicPicker()}
      onToggleTextOnly={() => live.toggleTextOnly()}
      onOpenTyping={() => live.openTyping()}
      onCloseTyping={() => live.closeTyping()}
      onDraft={(draft) => live.setDraft(draft)}
      onSend={() => void live.submit()}
      // 要在这次点击里同步调用：她的声音趁这一下唤醒 AudioContext。
      onRedial={() => void live.startCall()}
      onDismiss={() => live.dismissEnd()}
      onOwnKeys={() => leave(settingsHref("own-keys"))}
      // 好感度和礼物同一个开关：读得到好感度就是开放着。
      giftsOpen={snapshot.affinity !== null}
      onGift={() => leave(giftsHref(live.characterId))}
      exemptingKeys={exemptingKeys}
    />
  );
}

/**
 * 通话层（DESIGN.md「Call layer」）：从它盖住的那一块（对话栏，或者别的外壳页面）底部整张滑上来，收起是同一个动作反过来（410ms、ease-call），
 * 减少动态效果时没有过渡。强制深色（`.dark`），文字白色、次要字 80% 白。
 *
 * 底是她的头像放大模糊（有背景图时换成清晰的背景图），上面压一层到 call.deep 的渐变。
 * 没有立绘时中部是 112px 头像、名字和状态行；她有立绘时（`character.stand`）立绘做底，名字和状态行缩进头部，
 * 底部压暗。台词放在底部的毛玻璃卡里，再下面是毛玻璃底栏；结束后台词卡换成同样毛玻璃的结束卡。
 * 她在这通电话里发来照片时，台词卡上方出一张小卡（电脑）或一颗胶囊（手机），结束卡上不带。
 * 底栏的打字键把底栏换成输入栏，打出去的话她照常用声音回（GLOSSARY.md「打断」「补充」）。
 */
export function CallLayer(props: Props) {
  const { character, call, isCollapsed, isFullScreen, isReducedMotion, layerRef, typing } = props;
  const ended = call.phase === "ended";
  const hasStand = Boolean(character.stand);

  // 收起输入栏回到底栏时，焦点落回打字键；通话结束带走的输入栏不算。
  const typeKey = useRef<HTMLButtonElement>(null);
  const wasTyping = useRef(typing);
  useEffect(() => {
    if (wasTyping.current && !typing && call.phase === "connected") typeKey.current?.focus();
    wasTyping.current = typing;
  }, [typing, call.phase]);

  // Esc 收起（结束页上是回到对话）；按键时取最新的回调。
  const onEscape = useRef(() => {});
  useEffect(() => {
    onEscape.current = ended ? props.onDismiss : props.onCollapse;
  });
  // 展开时把焦点移进来，屏幕阅读器从这里读起。
  useEffect(() => {
    if (isCollapsed) return;
    layerRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) onEscape.current();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [isCollapsed, layerRef]);

  return (
    <section
      ref={layerRef}
      tabIndex={-1}
      aria-label={t.layer(character.name)}
      aria-hidden={isCollapsed}
      inert={isCollapsed}
      className={cn(
        "dark inset-0 z-10 flex flex-col overflow-hidden bg-her-call-deep text-white outline-none",
        isFullScreen ? "fixed" : "absolute",
        // visibility 跟着一起过渡：收起时等滑完才藏起来，展开时立刻可见。第一次出现也从底部滑上来。
        isReducedMotion
          ? "transition-none"
          : "transition-[translate,visibility] duration-410 ease-call starting:translate-y-full",
        isCollapsed ? "invisible translate-y-full" : "translate-y-0",
      )}
    >
      <Backdrop character={character} />
      {hasStand && <StandStage {...props} />}

      <header className="relative grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-4 pt-4">
        <div className="flex justify-start">
          {!ended && (
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-lg"
                    className={cn(isFullScreen && "size-11")}
                    aria-label={t.collapse}
                    onClick={props.onCollapse}
                  />
                }
              >
                <CollapseIcon className="size-6" />
              </TooltipTrigger>
              <TooltipContent>{t.collapseTooltip}</TooltipContent>
            </Tooltip>
          )}
        </div>
        <div className="min-w-0 text-center">
          {hasStand && (
            <>
              <h2 className="truncate text-header font-bold">{character.name}</h2>
              <Status call={call} cue={props.affinityCue} name={character.name} />
            </>
          )}
        </div>
        <div className="flex justify-end">
          {!ended && (
            <Button
              variant="call-glass"
              size={isFullScreen ? "call" : "call-sm"}
              aria-pressed={props.textOnly}
              onClick={props.onToggleTextOnly}
            >
              <TextOnlyIcon aria-hidden />
              {t.textOnly}
            </Button>
          )}
        </div>
      </header>

      {!hasStand && (
        <div className="relative flex flex-col items-center gap-3 pt-10 text-center">
          <Ripple isOn={call.phase === "dialing"}>
            <MarkedAvatar name={character.name} src={character.avatar_url} size="call" />
          </Ripple>
          <h2 className="mt-2 text-call-name">{character.name}</h2>
          <Status call={call} cue={props.affinityCue} name={character.name} />
        </div>
      )}

      <div
        className={cn(
          "relative flex min-h-0 flex-1 flex-col items-center gap-3 overflow-y-auto px-5",
          ended ? "justify-center py-6" : "justify-end pb-5",
        )}
      >
        {call.phase === "ended" ? (
          <Ended {...props} end={call.end} />
        ) : (
          <>
            <p role="status" className="text-center text-sm text-white/80">
              {props.micNotice && t.micNotices[props.micNotice]}
            </p>
            {/* 台词卡在接通前不出现 */}
            {call.phase === "connected" && props.photo && (
              <CallPhoto key={props.photo.id} photo={props.photo} isFullScreen={isFullScreen} />
            )}
            {call.phase === "connected" && <LineCard {...props} />}
          </>
        )}
      </div>

      {typing ? (
        <TypingBar {...props} />
      ) : (
        !ended && <Dock {...props} isConnected={call.phase === "connected"} typeKey={typeKey} />
      )}
    </section>
  );
}

/**
 * 底：没有背景图时她的头像放大、`blur-3xl`；有背景图时背景图清晰铺满。上面压一层从 25% 黑到
 * call.deep 混黑（`her-call-shade`）的渐变，白字的可读性全靠它（DESIGN.md「Call layer」）。
 */
function Backdrop({ character }: { character: Character }) {
  const src = character.background_url ?? character.avatar_url;
  return (
    <>
      {src && (
        // 纯装饰的底图，不经 next/image：头像要整张放大模糊，背景图按 cover 铺满。
        <img
          src={src}
          alt=""
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-0 size-full object-cover",
            !character.background_url && "scale-125 blur-3xl",
          )}
        />
      )}
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-linear-to-b from-black/25 to-her-call-shade" />
    </>
  );
}

/**
 * 立绘（DESIGN.md「Call layer」的「立绘」）：叠在底图和渐变之上、文字和按钮之下，不接收点击。
 * 只要她有立绘，一打电话就用这套布局，立绘晚到或者加载失败都不切回头像版，台词的位置不跳。
 * - 手机：占下方 80% 的高度，取景到膝，到了从下方升起。
 * - 电脑：铺满头部以下的整层，取景到腰，原地淡入。
 * 底部 50% 压一层到 call.deep 的渐变，台词卡和底栏落在衣服上也看得清。结束后立绘降到 40%。
 */
function StandStage(props: Props) {
  const { call, isCollapsed, isFullScreen, isReducedMotion, standOutfits, standDirection } = props;
  return (
    <>
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-x-0 bottom-0 transition-opacity duration-500",
          isFullScreen ? "h-[80%]" : "top-16",
          call.phase === "ended" && "opacity-40",
        )}
      >
        <Stand
          // 每拨一次重新载入：上一通失败了，这一通再试一次（通话状态带着这是第几次拨号）。
          key={call.dial}
          outfits={standOutfits}
          framing={isFullScreen ? "knees" : "waist"}
          isPaused={isCollapsed}
          entrance={isFullScreen && !isReducedMotion ? "rise" : "fade"}
          direction={standDirection}
          isReducedMotion={isReducedMotion}
        />
      </div>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-1/2 bg-linear-to-b from-transparent via-her-call-deep/88 via-55% to-her-call-deep"
      />
    </>
  );
}

/**
 * 状态行（名字下面）：呼叫中是「正在呼叫」加三个依次闪的小点，通话中是计时，结束后是挂断图标加
 * 「通话已结束 · mm:ss」（没接通过就不带时长）。只用文字和图标，不再用线形。
 * 通话中一个回合刚改变了好感度（`cue`）时，约 2.6 秒换成变化胶囊的那句话，前面一个升降小箭头。
 */
function Status({ call, cue, name }: { call: ShownCall; cue: AffinityCue | null; name: string }) {
  const time = useCallTime(call);
  if (call.phase === "connected" && cue) {
    return (
      <p className="flex items-center justify-center gap-1 text-sm text-white">
        <CueArrowIcon up={cue.change.up} className="size-3.5" />
        <span className="truncate">{cueText(cue.change, name)}</span>
      </p>
    );
  }
  return (
    <p className="flex items-center justify-center gap-1.5 text-sm text-white/80 tabular-nums">
      {call.phase === "dialing" && (
        <>
          {t.calling}
          <span aria-hidden className="inline-flex gap-0.5">
            {/* 延迟写在 style 里：animate-pulse 是 animation 简写，会盖掉类里的 animation-delay */}
            {[0, 200, 400].map((delay) => (
              <span
                key={delay}
                className="size-1 rounded-full bg-white/80 motion-safe:animate-pulse"
                style={{ animationDelay: `${delay}ms` }}
              />
            ))}
          </span>
        </>
      )}
      {call.phase === "connected" && time && showTime(time)}
      {call.phase === "ended" && (
        <>
          <HangUpIcon aria-hidden className="size-3.5" />
          {call.seconds === null ? t.ended : t.endedAfter(clock(call.seconds))}
        </>
      )}
    </p>
  );
}

/** 呼叫中、没有立绘时头像外两圈 20% 白的波纹，2 秒一圈；减少动态效果时不放。 */
function Ripple({ isOn, children }: { isOn: boolean; children: ReactNode }) {
  return (
    <div className="relative grid place-items-center">
      {/* 时长和延迟写在 style 里：animate-ping 是 animation 简写，会盖掉类里的 animation-duration */}
      {isOn &&
        [0, 1].map((delay) => (
          <span
            key={delay}
            aria-hidden
            className="absolute inset-0 rounded-full bg-white/20 motion-safe:animate-ping motion-reduce:hidden"
            style={{ animationDuration: "2s", animationDelay: `${delay}s` }}
          />
        ))}
      {children}
    </div>
  );
}

/**
 * 台词卡：她当前这句的日语原文在上、字幕在下；「只看文字」开着时上一句以 45% 不透明度留在上面。
 * 她还没开口时是一句提示。用户正在说、还没定稿的话放在最下面。
 */
function LineCard({ line, previousLine, hearing, typed, textOnly }: Props) {
  // 卡底一行：用户正在说的话，不在说时是刚打出去的那句。
  const foot = hearing ? t.hearing(hearing) : typed && t.typed(typed);
  return (
    <div className="w-full max-w-[34em] rounded-3xl bg-white/12 px-5 py-4 text-center text-balance backdrop-blur-md">
      <div aria-live="polite">
        {line ? (
          <>
            {textOnly && previousLine && (
              <div className="mb-3 opacity-45">
                <p className="text-body">
                  <Ja>{previousLine.ja}</Ja>
                </p>
                {previousLine.zh && <p className="text-subtitle text-white/80">{previousLine.zh}</p>}
              </div>
            )}
            <p className="text-call-line leading-relaxed">
              <Ja>{line.ja}</Ja>
            </p>
            {line.zh && <p className="mt-1 text-sm text-white/80">{line.zh}</p>}
          </>
        ) : (
          !typed && <p className="text-sm text-white/80">{textOnly ? t.repliesInText : t.speakFreely}</p>
        )}
      </div>
      {foot && <p className="mt-3 text-sm text-white/80">{foot}</p>}
    </div>
  );
}

/**
 * 她在通话里发来的照片，放在台词卡上方，不挡她的脸（DESIGN.md「Call layer」的「照片」）。有了下一张就换成下一张。
 * - 电脑：一张毛玻璃小卡，里面是 128px 宽的照片，点开放大；生成中是同样大小的占位加转圈和「正在拍照…」，
 *   没发出来时写「照片没发出来 · {原因}」。
 * - 手机：一颗毛玻璃胶囊。生成中转圈加「正在拍照…」，到了是相机图标加「发来一张照片 · 点开看」、点了放大，
 *   没发出来同电脑。
 */
function CallPhoto({ photo, isFullScreen }: { photo: Photo; isFullScreen: boolean }) {
  const [viewing, setViewing] = useState(false);
  const failedNote = photo.status === "failed" && t.photo.failed(photoFailureNote(photo.reason).reason);
  if (isFullScreen || failedNote) {
    const content =
      photo.status === "pending" ? (
        <>
          <Spinner className="size-4" />
          {t.photo.taking}
        </>
      ) : photo.status === "ready" ? (
        <>
          <PhotoIcon aria-hidden className="size-4" />
          {t.photo.sent}
        </>
      ) : (
        <>
          <PhotoFailedIcon aria-hidden className="size-4" />
          {failedNote}
        </>
      );
    const pill = "flex items-center gap-2 rounded-full bg-white/12 px-4 py-2 text-sm backdrop-blur-md";
    return (
      <div role="status" className="flex justify-center">
        {photo.status === "ready" ? (
          <>
            <button
              type="button"
              onClick={() => setViewing(true)}
              className={cn(pill, "outline-none focus-visible:ring-3 focus-visible:ring-white/60")}
            >
              {content}
            </button>
            <PhotoViewer id={photo.id} open={viewing} onOpenChange={setViewing} />
          </>
        ) : (
          <p className={pill}>{content}</p>
        )}
      </div>
    );
  }
  return (
    <div role="status" className="rounded-2xl bg-white/12 p-1.5 backdrop-blur-md">
      {photo.status === "pending" ? (
        <div className="flex aspect-[832/1216] w-32 flex-col items-center justify-center gap-2 rounded-xl bg-white/8 text-meta text-white/80">
          <Spinner className="size-4" />
          {t.photo.taking}
        </div>
      ) : (
        <>
          <button
            type="button"
            aria-label={t.photo.open}
            onClick={() => setViewing(true)}
            className="block w-32 overflow-hidden rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-white/60"
          >
            <img
              src={photoImageUrl(photo.id)}
              alt={copy.conversation.photo.sent}
              width={832}
              height={1216}
              className="aspect-[832/1216] w-full object-cover"
            />
          </button>
          <PhotoViewer id={photo.id} open={viewing} onOpenChange={setViewing} />
        </>
      )}
    </div>
  );
}

/** 结束卡：原因、后端多说的话、说明、是否计入限额，以及「回到对话」和主操作（重拨、「去设置自带密钥」，
 * 或者她因为冷却挂断时的「送她一件礼物」；礼物没开放时冷却版没有主操作）。 */
function Ended({
  character,
  end,
  giftsOpen,
  exemptingKeys,
  onRedial,
  onDismiss,
  onOwnKeys,
  onGift,
}: Props & { end: CallEnd }) {
  const timeZone = useProducerTimezone();
  const card = endCard(end, { name: character.name, now: new Date(), timeZone, giftsOpen, exemptingKeys });
  const main = card.action && {
    redial: { onClick: onRedial, label: t.redial, Icon: PhoneIcon },
    own_keys: { onClick: onOwnKeys, label: t.toOwnKeys, Icon: null },
    gift: { onClick: onGift, label: copy.gifts.giveHer, Icon: GiftIcon },
  }[card.action];
  return (
    <div
      role="alert"
      className="flex w-full max-w-[34em] flex-col items-center gap-2 rounded-3xl bg-white/12 px-5 py-6 text-center text-balance backdrop-blur-md"
    >
      <h3 className="text-lg font-semibold">{card.title}</h3>
      {card.detail && <p className="text-sm">{card.detail}</p>}
      {card.body && <p className="text-sm text-white/80">{card.body}</p>}
      <p className="text-meta text-white/60">{card.counts ? t.counted : t.notCounted}</p>
      <div className="mt-3 flex flex-wrap justify-center gap-2">
        <Button variant="call-glass" size="call" onClick={onDismiss}>
          {t.backToConversation}
        </Button>
        {main && (
          <Button variant="call-solid" size="call" onClick={main.onClick}>
            {main.Icon && <main.Icon aria-hidden />}
            {main.label}
          </Button>
        )}
      </div>
    </div>
  );
}

/**
 * 底栏：一条毛玻璃胶囊。静音、切换麦克风、打字是 56px 圆形图标键（静音按下时白底黑图标），
 * 挂断是带「挂断」二字的挂断红胶囊。呼叫中静音、切换麦克风和打字置灰，只能挂断。
 */
function Dock({
  isConnected,
  micMuted,
  onToggleMic,
  onHangUp,
  onSwitchMic,
  onOpenTyping,
  typeKey,
}: Props & { isConnected: boolean; typeKey: RefObject<HTMLButtonElement | null> }) {
  return (
    <footer className="relative flex justify-center px-4 pt-2 pb-8">
      <div className="flex items-center gap-2 rounded-full bg-white/12 p-2 backdrop-blur-md">
        <RoundKey
          label={micMuted ? t.micUnmute : t.micMute}
          icon={micMuted ? MicOffIcon : MicIcon}
          isPressed={micMuted}
          isDisabled={!isConnected}
          onClick={onToggleMic}
        />
        <RoundKey label={t.switchMic} icon={MicSwitchIcon} isDisabled={!isConnected} onClick={onSwitchMic} />
        <AmbienceKey isDisabled={!isConnected} />
        <RoundKey label={t.type} icon={TypingIcon} isDisabled={!isConnected} onClick={onOpenTyping} keyRef={typeKey} />
        <Button variant="hangup" size="call-dock" onClick={onHangUp} className="ml-2">
          <HangUpIcon aria-hidden />
          {t.hangUp}
        </Button>
      </div>
    </footer>
  );
}

/**
 * 输入栏：底栏换成的同一条毛玻璃胶囊，里面是收起输入栏（✕）、输入框和白底发送键，都是 44px 高。
 * 打开时输入框拿到焦点；回车发送、Shift+回车换行，输入法选字时的回车不算；Esc 回到底栏，不收起通话层。
 * 发送键按下时不抢焦点，发完键盘不收。
 *
 * 开着时 viewport 带 `interactive-widget=resizes-content`：手机键盘弹起时整层缩到键盘上方，
 * 页首和她的脸留在屏幕上（浏览器默认会把整页往上推）；收起时去掉。iOS Safari 不认这一条。
 */
function TypingBar({ draft, onDraft, onSend, onCloseTyping }: Props) {
  const field = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]');
    const before = meta?.content;
    if (meta && !before?.includes("interactive-widget")) meta.content = `${before}, interactive-widget=resizes-content`;
    // 先改 viewport 再拿焦点，键盘弹起时已经按缩整层的办法排。
    field.current?.focus();
    return () => {
      if (meta && before !== undefined) meta.content = before;
    };
  }, []);
  const canSend = draft.trim() !== "";
  return (
    <footer className="relative flex justify-center px-4 pt-2 pb-3">
      <form
        className="flex w-full max-w-[34em] items-end gap-1.5 rounded-3xl bg-white/12 p-1.5 backdrop-blur-md"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSend) onSend();
        }}
      >
        <Button type="button" variant="call-round" size="icon-call" aria-label={t.closeTyping} onClick={onCloseTyping}>
          <CloseIcon aria-hidden />
        </Button>
        <Textarea
          ref={field}
          variant="call"
          rows={1}
          value={draft}
          onChange={(e) => onDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (canSend) onSend();
            } else if (e.key === "Escape" && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onCloseTyping();
            }
          }}
          placeholder={copy.conversation.placeholder}
          aria-label={copy.conversation.placeholder}
          className="flex-1"
        />
        {/* 按下时不抢输入框的焦点：手机上一失焦键盘就收，这一下点击落空 */}
        <Button
          type="submit"
          variant="call-solid"
          size="icon-call"
          aria-label={copy.conversation.send}
          disabled={!canSend}
          onMouseDown={(e) => e.preventDefault()}
        >
          <SendIcon aria-hidden />
        </Button>
      </form>
    </footer>
  );
}

function RoundKey({
  label,
  icon: Icon,
  isPressed,
  isDisabled,
  onClick,
  keyRef,
}: {
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  isPressed?: boolean;
  isDisabled?: boolean;
  onClick: () => void;
  keyRef?: RefObject<HTMLButtonElement | null>;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            ref={keyRef}
            variant="call-round"
            size="icon-call-dock"
            aria-label={label}
            aria-pressed={isPressed}
            disabled={isDisabled}
            onClick={onClick}
          />
        }
      >
        <Icon aria-hidden />
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
