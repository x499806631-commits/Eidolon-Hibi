"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { PageShell } from "@/components/app/PageShell";
import { SectionHeading } from "@/components/app/SubpageHeader";
import { useDirectory, useWide } from "@/components/conversations/ConversationDirectory";
import { useProducerTimezone } from "@/components/conversations/useProducerTimezone";
import { AchievementsSection } from "@/components/gifts/AchievementsSection";
import { BadgesSection } from "@/components/gifts/BadgesSection";
import { DailyTasksSection } from "@/components/gifts/DailyTasksSection";
import { DrawDialog, type Drawing } from "@/components/gifts/DrawDialog";
import { GivenToast, type GivenNotice } from "@/components/gifts/GivenToast";
import { GivePanel, type Giving } from "@/components/gifts/GivePanel";
import { MY_GIFTS_ID, MyGifts } from "@/components/gifts/MyGifts";
import { ResetCards } from "@/components/gifts/ResetCards";
import { FavorDialog } from "@/components/gifts/FavorDialog";
import { TicketDialog } from "@/components/gifts/TicketDialog";
import { Tickets } from "@/components/gifts/Tickets";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { copy } from "@/i18n";
import {
  achievementsSeenUpTo,
  keepAchievementsSeenUpTo,
  latestUnlock,
  OF_ALL,
  unseenRows,
  withUnlocked,
} from "@/lib/achievements";
import { ApiError, type Character } from "@/lib/api";
import { arrange } from "@/lib/conversationList";
import { dailyTasksView, withDone } from "@/lib/dailyTasks";
import { toFailure, type Failure } from "@/lib/failure";
import {
  chosenFirst,
  drawDots,
  drawGifts,
  drawNote,
  emptyNote,
  nextDrawText,
  getGiftCatalog,
  getGiftState,
  giveGift,
  givenNote,
  spendResetCard,
  type DrawResult,
  type GiftInfo,
  type GiftsFor,
  type GiftState,
  type Given,
} from "@/lib/gifts";
import { later } from "@/lib/seenOnDevice";
import { TICKET_KINDS, type TicketKind } from "@/lib/tickets";
import { cn } from "@/lib/utils";
import { CloseIcon, DrawIcon, GiftIcon } from "@/theme/icons";

const t = copy.gifts;

/** 礼物盒揭晓前跳多久，和 `animate-hop` 一样长。 */
const OPENING_MS = 900;

/** 送出后的提示留多久。 */
const NOTICE_MS = 8000;

const NONE: ReadonlySet<string> = new Set();

/**
 * 礼物页（DESIGN.md「礼物页」）：导航栏右边整块一栏，内容最宽 `max-w-3xl`、居中，手机和电脑同一个版式。
 * 上面是抽奖台，下面「每日任务」一节（「去对话」去对话列表，「挑一件」滚到我的礼物）、「成就」一小节（合起来的四个；
 * 解锁晚于进这一页前看过的那几行带点，离开这一页记成看过），有重置卡时再下面一条重置卡条，有体验券时再下面一块体验券（点「使用」弹出用券的框），最后「我的礼物」；点一件（或单抽结果上的「送给谁…」）拉出送礼面板，
 * 送出后面板收起、底部出提示，页面不跳走。好感度和礼物没开放时整页是「礼物暂时没有开放」，「礼物」照样留在导航里；
 * 抽奖、送礼或用重置卡时才知道没开放（收到「没开放」的 409）也切成这样。
 * 从对话页跳来（`pick`）时页首下面一条「给{角色名}挑一件礼物」：点哪件礼物，面板里都已选好她、排在第一个，
 * 送出的礼物和别处送的一样记进她的当前对话；点 ✕ 取消预选，地址里的也去掉。地址里的角色不存在时当作没有预选。
 */
export function GiftsPage({ pick }: { pick: GiftsFor | null }) {
  const [state, setState] = useState<GiftState | null>(null);
  const [catalog, setCatalog] = useState<GiftInfo[] | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [drawing, setDrawing] = useState<Drawing | null>(null);
  // 收起面板时留着上一次送的，收起的动画里内容不消失。
  const [giving, setGiving] = useState<Giving | null>(null);
  const [isGiveOpen, setGiveOpen] = useState(false);
  const [isSending, setSending] = useState(false);
  const [notice, setNotice] = useState<GivenNotice | null>(null);
  const [using, setUsing] = useState<TicketKind | null>(null);
  const isReducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)", false);
  const isWide = useWide();
  const timeZone = useProducerTimezone();
  const { account, characters, latest, preferences, go, refresh, giftsRead, rewardedAt } = useDirectory();
  const userId = account?.id ?? null;
  // 进这一页前这台设备上看过的合起来的成就：那一小节里解锁晚于它的带点，留到离开这一页。
  const [seen, setSeen] = useState<{ userId: string; at: string | null } | null>(null);
  if (userId && seen?.userId !== userId) setSeen({ userId, at: achievementsSeenUpTo(userId, OF_ALL) });
  // 在这一页上送礼刚做完的每日任务：那一行打勾时弹一下。
  const [justDone, setJustDone] = useState<string[]>([]);
  const router = useRouter();
  // 点过 ✕ 的预选：地址换掉以前（重新渲染要等一下）就不再显示。地址换回 /gifts 以后忘掉，
  // 这一页没卸下又从结束卡带着她跳来时（通话层由外壳画在礼物页上）照样预选。
  const [dropped, setDropped] = useState<string | null>(null);
  if (!pick && dropped !== null) setDropped(null);
  const chosen = pick && pick.to !== dropped ? characters?.find((c) => c.id === pick.to) : undefined;
  const giftsById = useMemo(() => new Map((catalog ?? []).map((g) => [g.id, g])), [catalog]);
  // 所有角色（隐藏了的也在），顺序和对话列表一样：重置卡确认框照这个顺序列，送给谁时预选的她排第一。
  const arranged = useMemo(() => {
    const all = characters ?? [];
    return arrange(all, latest, { preferences, keep: all.map((c) => c.id) }).rows.map((row) => row.character);
  }, [characters, latest, preferences]);
  const recipients = useMemo(() => chosenFirst(arranged, chosen?.id ?? null), [arranged, chosen]);
  const statuses = useMemo(() => new Map((state?.characters ?? []).map((c) => [c.id, c])), [state]);

  // 离开这一页（卸下）时把看过记成合起来的成就里最近一次解锁。
  const latestOfAll = useRef<string | null>(null);
  useEffect(() => {
    latestOfAll.current = state?.achievements ? latestUnlock({ achievements: state.achievements }) : null;
  }, [state]);
  useEffect(() => {
    if (!userId) return;
    return () => {
      if (latestOfAll.current) keepAchievementsSeenUpTo(userId, OF_ALL, latestOfAll.current);
    };
  }, [userId]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), NOTICE_MS);
    return () => clearTimeout(timer);
  }, [notice]);

  /** 读到的礼物状态：这一页照它显示，开关和最近一次到账也告诉外壳（在这一页上就记成看过，「礼物」上不出点）。 */
  const applyGiftState = useCallback(
    (s: GiftState) => {
      setState(s);
      giftsRead(s);
    },
    [giftsRead],
  );

  // 别处（通话里、别的设备）到账了，这一页读到的落后了：重读一遍，那一节跟着更新。每个时刻只重读一次。
  const rereadFor = useRef<string | null>(null);
  useEffect(() => {
    if (!state || !rewardedAt || later(state.rewarded_at, rewardedAt) === state.rewarded_at) return;
    if (rereadFor.current === rewardedAt) return;
    rereadFor.current = rewardedAt;
    getGiftState().then(applyGiftState, () => {});
  }, [state, rewardedAt, applyGiftState]);

  useEffect(() => {
    Promise.all([getGiftState(), getGiftCatalog()]).then(
      ([s, c]) => {
        applyGiftState(s);
        setCatalog(c);
      },
      (e) => setFailure(toFailure(e)),
    );
  }, [applyGiftState]);

  async function draw(all: boolean) {
    setFailure(null);
    setDrawing({ all, result: null });
    try {
      const [result] = await Promise.all([
        drawGifts(all),
        new Promise((resolve) => setTimeout(resolve, isReducedMotion ? 0 : OPENING_MS)),
      ]);
      setDrawing({ all, result });
      // 先按抽到的把背包补上，「送给谁…」马上打开时件数就是对的；随后再整个读一遍。
      setState((s) => s && withDrawn(s, result, catalog ?? []));
      getGiftState().then(applyGiftState, (e) => setFailure(toFailure(e)));
    } catch (e) {
      setDrawing(null);
      failed(e);
    }
  }

  /** 收到「没开放」的 409 时整页切成未开放，别的失败在页上方说。 */
  function failed(e: unknown) {
    if (e instanceof ApiError && e.giftsClosed) {
      setState((s) => s && { ...s, open: false });
      giftsRead({ open: false, rewarded_at: null });
    }
    else setFailure(toFailure(e));
  }

  /** 拉出这件的送礼面板；还有几件按背包里的算。 */
  function startGiving(gift: GiftInfo) {
    const left = state?.backpack.find((b) => b.gift_id === gift.id)?.count ?? 0;
    setDrawing(null);
    setGiving({ gift, left, to: chosen?.id ?? null });
    setGiveOpen(true);
  }

  async function send() {
    const to = recipients.find((c) => c.id === giving?.to);
    if (!giving || !to) return;
    setFailure(null);
    setSending(true);
    try {
      const given = await giveGift(to.id, giving.gift.id);
      setGiveOpen(false);
      setState((s) => s && afterGiving(s, giving.gift.id, to.id, given));
      // 做完了任务：那一行当场打勾、弹一下（到账就在这一页上，重读回来记成看过，不出点）。
      if (given.rewarded) setJustDone(given.rewarded.daily_tasks);
      setNotice({
        key: Date.now(),
        character: to,
        note: givenNote(given, to.name, new Date(), timeZone),
      });
      refresh();
      getGiftState().then(applyGiftState, () => {});
    } catch (e) {
      setGiveOpen(false);
      failed(e);
      // 另一个标签页可能刚送掉了最后一件：重新读一遍背包。
      getGiftState().then(applyGiftState, () => {});
    } finally {
      setSending(false);
    }
  }

  /** 用一张重置卡；拉回了谁、还剩几张都从重新读的状态里来，抽奖台、重置卡条和送礼面板里的档位一起刷新。 */
  async function spendCard() {
    setFailure(null);
    try {
      await spendResetCard();
    } catch (e) {
      // 没开放切成未开放；没有卡了、确认框打开期间她们自己回到了信任，在页上方说，卡没有扣。
      failed(e);
    }
    getGiftState().then(applyGiftState, (e) => setFailure(toFailure(e)));
  }

  // 没开放时没有下次，抽奖台和「还没有礼物」也不显示。
  const nextDraw = state?.next_draw_at ? nextDrawText(state.next_draw_at, new Date(), timeZone) : "";

  return (
    <PageShell width="full" isInShell header={<h1 className="text-list-title">{t.title}</h1>}>
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6">
        {failure && <FailureNotice failure={failure} />}
        {state === null || catalog === null ? (
          !failure && <p className="text-body text-muted-foreground">{t.loading}</p>
        ) : !state.open ? (
          <GiftsEmpty title={t.closed.title} description={t.closed.description} />
        ) : (
          <>
            {chosen && (
              <PickedFor
                character={chosen}
                onCancel={() => {
                  setDropped(chosen.id);
                  router.replace("/gifts", { scroll: false });
                }}
              />
            )}
            <DrawDesk
              draws={state.draws}
              max={state.max_draws}
              perDay={state.draws_per_day ?? 1}
              next={nextDraw}
              toRare={state.to_rare}
              isDrawing={drawing !== null}
              onDraw={draw}
            />
            {state.daily_tasks && (
              <DailyTasksSection
                view={dailyTasksView(
                  state.daily_tasks,
                  nextDrawText(state.daily_tasks.renews_at, new Date(), timeZone),
                  state.backpack.length > 0,
                )}
                justDone={justDone}
                onAction={(action) => {
                  if (action === "converse") go("/conversations");
                  else
                    document
                      .getElementById(MY_GIFTS_ID)
                      ?.scrollIntoView({ behavior: isReducedMotion ? "auto" : "smooth", block: "start" });
                }}
              />
            )}
            {state.achievements && (
              <AchievementsSection
                achievements={state.achievements}
                unseen={seen ? unseenRows({ achievements: state.achievements }, seen.at) : NONE}
              />
            )}
            <BadgesSection />
            {state.reset_cards > 0 && (
              <ResetCards
                count={state.reset_cards}
                resettable={state.resettable}
                characters={arranged}
                onUse={spendCard}
              />
            )}
            {state.tickets.length > 0 && (
              <Tickets tickets={state.tickets} onUse={setUsing} onDiary={() => go("/diary")} />
            )}
            {state.backpack.length > 0 ? (
              <MyGifts backpack={state.backpack} catalog={catalog} onGive={(held) => startGiving(held.gift)} />
            ) : (
              <>
                <SectionHeading>{t.mine}</SectionHeading>
                <GiftsEmpty title={t.empty.title} description={emptyNote(state.draws, nextDraw)} />
              </>
            )}
          </>
        )}
      </div>
      <DrawDialog
        drawing={drawing}
        catalog={giftsById}
        onGive={startGiving}
        onAgain={() => draw(false)}
        onClose={() => setDrawing(null)}
      />
      <TicketDialog
        kind={using === "favor" ? null : using}
        characters={arranged}
        catalog={giftsById}
        isWide={isWide}
        timeZone={timeZone}
        onClose={() => setUsing(null)}
        onDone={() => getGiftState().then(setState, (e) => setFailure(toFailure(e)))}
        onGo={go}
      />
      <FavorDialog
        isOpen={using === "favor"}
        characters={arranged}
        statuses={statuses}
        backpack={state?.backpack ?? []}
        catalog={giftsById}
        isWide={isWide}
        timeZone={timeZone}
        onClose={() => setUsing(null)}
        onDone={() => getGiftState().then(setState, (e) => setFailure(toFailure(e)))}
        onGo={go}
      />
      <GivePanel
        giving={giving}
        isOpen={isGiveOpen}
        isWide={isWide}
        isSending={isSending}
        characters={recipients}
        statuses={statuses}
        onChoose={(to) => setGiving((g) => g && { ...g, to })}
        onSend={send}
        onClose={() => setGiveOpen(false)}
      />
      {/* 「去对话」去她的对话页，那里打开的就是记录放进的那段；通话中去别的角色时先问会不会挂断。 */}
      <GivenToast notice={notice} onGo={(n) => go(`/conversation/${encodeURIComponent(n.character.id)}`)} />
    </PageShell>
  );
}

/** 抽完以后的礼物状态：剩下的次数，抽到的礼物叠进背包（按目录的顺序），重置卡另计，这次解锁的合起来的阶段标上。 */
function withDrawn(state: GiftState, result: DrawResult, catalog: GiftInfo[]): GiftState {
  const counts = new Map(state.backpack.map((b) => [b.gift_id, b.count]));
  let cards = state.reset_cards;
  const tickets = new Map(state.tickets.map((h) => [h.kind, h.count]));
  for (const d of result.drawn) {
    if (d.kind === "gift") counts.set(d.gift_id, (counts.get(d.gift_id) ?? 0) + 1);
    else if (d.kind === "ticket") tickets.set(d.ticket, (tickets.get(d.ticket) ?? 0) + 1);
    else cards += 1;
  }
  const backpack = catalog.flatMap((g) => {
    const count = counts.get(g.id);
    return count ? [{ gift_id: g.id, count }] : [];
  });
  return {
    ...state,
    draws: result.draws,
    backpack,
    reset_cards: cards,
    tickets: TICKET_KINDS.flatMap((kind) => {
      const count = tickets.get(kind);
      return count ? [{ kind, count }] : [];
    }),
    achievements: state.achievements && withUnlocked(state.achievements, result.rewarded),
  };
}

/** 送出一件以后的礼物状态：这件按接口给的件数（送完的不再列），她的好感度换成现在的，这次解锁的合起来的阶段标上。 */
function afterGiving(state: GiftState, giftId: string, characterId: string, given: Given): GiftState {
  const { rewarded } = given;
  return {
    ...state,
    draws: state.draws + (rewarded?.draws ?? 0),
    daily_tasks: state.daily_tasks && withDone(state.daily_tasks, rewarded?.daily_tasks ?? []),
    achievements: state.achievements && withUnlocked(state.achievements, rewarded),
    rewarded_at: later(state.rewarded_at, rewarded?.rewarded_at ?? null),
    backpack: state.backpack
      .map((b) => (b.gift_id === giftId ? { ...b, count: given.left } : b))
      .filter((b) => b.count > 0),
    characters: state.characters.map((c) => (c.id === characterId ? { id: c.id, ...given.affinity } : c)),
  };
}

/** 从对话页跳来时页首下面那一条：她的小头像、「给{角色名}挑一件礼物」和取消预选的 ✕。 */
function PickedFor({ character, onCancel }: { character: Character; onCancel: () => void }) {
  return (
    <div className="flex items-center gap-2 rounded-2xl border py-1 pr-1 pl-3">
      <MarkedAvatar name={character.name} src={character.avatar_url} size="sm" />
      <p className="min-w-0 flex-1 truncate text-sm">{t.pickFor(character.name)}</p>
      {/* 手机上触控目标不小于 44px */}
      <Button
        variant="ghost"
        size="icon-lg"
        className="max-md:size-11"
        aria-label={t.cancelPick(character.name)}
        onClick={onCancel}
      >
        <CloseIcon />
      </Button>
    </div>
  );
}

/**
 * 抽奖台：`bg-muted` 圆角块，「抽奖 还有 n 次」、7 个小点（攒了几次亮几个，每日任务给的超过 7 次时全亮、后面写「+n」）、一句说明和保底还有几次；右边「抽一次」，
 * 攒了 2 次以上时旁边多一个「全部抽完」，次数为 0 时「抽一次」不可用。
 */
function DrawDesk({
  draws,
  max,
  perDay,
  next,
  toRare,
  isDrawing,
  onDraw,
}: {
  draws: number;
  max: number;
  /** 每天发几次。 */
  perDay: number;
  /** 下次到账的「今天 / 明天 HH:MM」。 */
  next: string;
  /** 最多再抽几次一定出稀有（保底）。 */
  toRare: number;
  isDrawing: boolean;
  onDraw: (all: boolean) => void;
}) {
  const dots = drawDots(draws, max);
  return (
    <section aria-label={t.draw.title} className="flex flex-wrap items-center gap-4 rounded-2xl bg-muted p-5">
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <p className="flex items-baseline gap-2">
          <span className="text-header">{t.draw.title}</span>
          <span className="text-sm text-muted-foreground tabular-nums">{t.draw.left(draws)}</span>
        </p>
        <span role="img" aria-label={dots.label} className="flex items-center gap-1">
          {Array.from({ length: max }, (_, i) => (
            <span key={i} className={cn("size-2 rounded-full", i < dots.lit ? "bg-foreground" : "bg-foreground/15")} />
          ))}
          {dots.more && (
            <span className="ml-0.5 text-meta leading-none text-muted-foreground tabular-nums">{dots.more}</span>
          )}
        </span>
        <p className="text-subtitle text-muted-foreground">{drawNote(draws, max, next, perDay)}</p>
        <p className="text-meta text-muted-foreground">{t.draw.toRare(toRare)}</p>
      </div>
      <div className="flex gap-2">
        {draws >= 2 && (
          <Button variant="outline" disabled={isDrawing} onClick={() => onDraw(true)}>
            {t.draw.all}
          </Button>
        )}
        <Button disabled={draws === 0 || isDrawing} onClick={() => onDraw(false)}>
          <DrawIcon data-icon="inline-start" />
          {t.draw.once}
        </Button>
      </div>
    </section>
  );
}

/** 礼物图标加一句标题和说明的 `Empty`：「还没有礼物」，以及没开放时的整页。 */
function GiftsEmpty({ title, description }: { title: string; description: string }) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <GiftIcon />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  );
}
