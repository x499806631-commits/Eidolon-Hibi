"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type MouseEvent,
  type ReactNode,
} from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useWhileVisible } from "@/hooks/useWhileVisible";
import { copy } from "@/i18n";
import { getAffinity } from "@/lib/affinity";
import { appeal } from "@/lib/appeals";
import { DEFAULT_APPEARANCE, keepAppearance, loadAppearance, withAppearance, type Appearance } from "@/lib/appearance";
import {
  createConversation,
  getAccount,
  getPhoto,
  listCharacters,
  listConversations,
  listLatestConversations,
  listMessages,
  sendTurn,
  type Account,
  type Character,
  type LatestConversation,
} from "@/lib/api";
import { startCall } from "@/lib/call";
import { loadCallPreferences, saveCallPreferences } from "@/lib/callPreferences";
import { isWebOn } from "@/lib/webToggle";
import type { Ambience } from "@/lib/ambience";
import { CharacterVoice } from "@/lib/characterVoice";
import { toFailure, type Failure } from "@/lib/failure";
import { keepListPreferences, listPreferences, NO_PREFERENCES, type ListPreferences } from "@/lib/listPreferences";
import { isLive, LiveConversation, NOT_OPEN, type LiveCall } from "@/lib/liveConversation";
import { departure } from "@/lib/navShell";

import { MicSheet } from "./MicSheet";
import { useCallTones } from "./useCallTones";

/** 标签页在前台时多久重新拉一次摘要：她到点的搭话最多晚这么久出现（切回标签页时马上拉一次）。 */
const LIST_POLL_MS = 60_000;

/** 正在和谁通话，列表那一行和外壳顶上的通话条据此显示「正在呼叫…」「通话中 mm:ss」；从外壳持有的 LiveConversation 的快照算出。 */
export type CallInList = { characterId: string; state: LiveCall } | null;

/** 列表和对话共用的数据：有哪些角色、和每位最新一次对话的摘要。由导航外壳载入一次，设置页也拿得到。 */
export type Directory = {
  /** 登录的用户（浏览器里的自带密钥按他的 ID 分开存）；和角色一起载入，还在载入时为 null。
   * 由导航外壳持有，在对话和设置之间换页时不重新载入。 */
  account: Account | null;
  /** 别处重新取到了登录的用户（例如设置里解绑了一种登录方式之后），换上它；或者按换上那一刻的值改一处（例如搭话的开关）。 */
  accountChanged: (change: Account | ((account: Account) => Account)) => void;
  /** 还在载入时为 null。头像已经按外观换好（打开 SCSP 头像时是她的 SCSP 头像）。 */
  characters: Character[] | null;
  /** 和每位最新一次对话的摘要；标签页在前台时每分钟重新拉一次。她的对话一读到，她那一行就先在本地记成搭话都看过了。 */
  latest: Record<string, LatestConversation>;
  failure: Failure | null;
  /** 重新拉一遍摘要，例如一个回合记下之后、开了新对话之后。拉回来时她的对话页开着，就告诉那一页（`LiveConversation.listed`）。 */
  refresh: () => void;
  /** 从头重新载入，给「重试」用。 */
  reload: () => void;
  /** 这台设备上的隐藏和置顶；和角色一起读出，读不到时什么都没整理。 */
  preferences: ListPreferences;
  /** 整理列表（隐藏、显示、置顶…），立即生效并存下；存不进去时只在这次页面里生效。 */
  tidy: (change: (preferences: ListPreferences) => ListPreferences) => void;
  /** 这台设备上的外观；和角色一起读出。 */
  appearance: Appearance;
  /** 改外观，立即生效（`characters` 的头像跟着换）并存下；存不进去时只在这次页面里生效。 */
  changeAppearance: (appearance: Appearance) => void;
  call: CallInList;
  /** 通话时垫在她声音下面的环境音（ASMR 模式），和她的声音一样由外壳持有。 */
  ambience: Ambience;
  /**
   * 外壳持有的那段对话的实时状态（ADR-0018「通话属于外壳」）：她的对话页开着时就是那一页的；
   * 离开她的对话页时通话还在进行，就留在外壳里，换页时不卸下，通话结束后才丢掉。没有时为 null。
   */
  held: LiveConversation | null;
  /** 她的对话页开着、正用着 `held`：通话层由那一页画；否则由外壳在当前页画。 */
  isHeldShown: boolean;
  /**
   * 她的当前对话正显示在她的对话页上的那位：对话页开着、没在看历史对话。她那一行和导航都不标她没看过的搭话
   * （搭话直接出现在页面上，算看过），拉回来的摘要也只告诉这一页。没有时为 null。
   */
  onScreen: string | null;
  /** 她的对话页换成看一段历史对话（`true`），或者回到当前对话（`false`）。 */
  showPast: (shown: boolean) => void;
  /**
   * 她的对话页打开时取她的实时状态：外壳里那一份正是她的，就用那一份（记录、通话层收没收起都和离开时一样）；
   * 否则新建一份，外壳里原来那一份（另一位角色的）就此结束，通话一并挂断。`userId` 是登录的用户：
   * 发回合、打电话时带上浏览器里存的、他的自带密钥。
   */
  open: (characterId: string, userId: string) => LiveConversation;
  /** 她的对话页卸下：通话还在进行（或者因故结束、结束卡还开着）就留在外壳里，否则就此结束。 */
  close: (live: LiveConversation) => void;
  /**
   * 在本标签页去 `href`，按外壳的去处规则（lib/navShell）：外壳里换页直接去，通话留着；
   * 通话中打开另一位角色的对话或者离开外壳，先问会不会挂断，确认了先挂断再去。
   */
  go: (href: string) => void;
  /**
   * 链接的 onClick，同样按去处规则：要问时拦下这次跳转、先问。新标签页打开的（按着 Ctrl、⌘、Shift、
   * 不是左键，或者 `target="_blank"`）不拦。`isDocument`：链接去的不是应用里的页面（例如后端的绑定登录方式），
   * 确认后整页跳过去。
   */
  guard: (href: string, isDocument?: boolean) => (event: MouseEvent<HTMLAnchorElement>) => void;
  /** 挂断正在进行的通话，算用户自己挂断（退出登录、删除全部数据各自确认之后）；没在通话时什么都不做。 */
  hangUp: () => Promise<void>;
};

/** 宽屏（≥ 768px）是左右双栏，窄屏是两级页面（DESIGN.md「Layout」）。服务端先按宽屏渲染。 */
export function useWide(): boolean {
  return useMediaQuery("(min-width: 768px)", true);
}

/** 这次点击链接是在新标签页（或新窗口）打开，不在这一页换页。 */
export function opensNewTab(event: MouseEvent<HTMLAnchorElement>): boolean {
  return (
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey ||
    event.button !== 0 ||
    event.currentTarget.target === "_blank"
  );
}

const DirectoryContext = createContext<Directory | null>(null);

/** 外壳还没持有对话时，列表看到的那份空快照。 */
const notHeld = () => NOT_OPEN;
const noSubscription = () => () => {};

export function useDirectory(): Directory {
  const directory = useContext(DirectoryContext);
  if (!directory) throw new Error("useDirectory 只能在导航外壳里用");
  return directory;
}

export function DirectoryProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  const userId = account?.id ?? null;
  const [configured, setConfigured] = useState<Character[] | null>(null);
  const [latest, setLatest] = useState<Record<string, LatestConversation>>({});
  const [preferences, setPreferences] = useState<ListPreferences>(NO_PREFERENCES);
  const [appearance, setAppearance] = useState<Appearance>(DEFAULT_APPEARANCE);
  const characters = useMemo(() => configured && withAppearance(configured, appearance), [configured, appearance]);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [attempt, setAttempt] = useState(0);
  // 外壳持有的那段对话：状态给渲染，ref 给回调在点击、卸下这一刻读。
  const [held, setHeld] = useState<LiveConversation | null>(null);
  const heldRef = useRef<LiveConversation | null>(null);
  // 已经结束（dispose）的还留着画最后一刻（通话层收起、滑下去），下一次打开时换新的。
  const ended = useRef(new WeakSet<LiveConversation>());
  const [isHeldShown, setHeldShown] = useState(false);
  const [isPastShown, setPastShown] = useState(false);
  // 给回调在拉回摘要这一刻读：她的当前对话在不在屏幕上。
  const onScreenRef = useRef({ held: false, past: false });
  const showHeld = useCallback((shown: boolean) => {
    onScreenRef.current = { held: shown, past: false };
    setHeldShown(shown);
    setPastShown(false);
  }, []);
  // 在本地看过的：第几次看过的（`seen`），每位记最后一次。拉摘要时以服务器为准，但这一次拉出去之后才看过的那几位，
  // 拉回来的还是没看过，就留着看过。
  const seenAt = useRef(new Map<string, number>());
  const seenCount = useRef(0);
  const [leaving, setLeaving] = useState<(() => void) | null>(null);
  // 她的声音和电话本身的声音由外壳持有：换页时放声音的元素不卸下，她说到一半的话照样听得完。
  const [voice] = useState(() => new CharacterVoice());
  const tones = useCallTones();
  // 订阅外壳持有的那一份快照：列表那一行、隐藏时留下的角色、外壳顶上的通话条都从它算出来。
  const snapshot = useSyncExternalStore(held?.subscribe ?? noSubscription, held?.snapshot ?? notHeld, notHeld);
  const liveCall = held && isLive(snapshot.call) ? snapshot.call : null;
  const call: CallInList = held && liveCall ? { characterId: held.characterId, state: liveCall } : null;

  /** 拉回来的摘要，第 `since` 次看过之后又在本地看过的那几位记成看过。 */
  const received = useCallback((all: LatestConversation[], since: number): Record<string, LatestConversation> => {
    const seenSince = (c: LatestConversation) => (seenAt.current.get(c.character_id) ?? 0) > since;
    return Object.fromEntries(
      all.map((c) => [c.character_id, c.unseen_reach_out && seenSince(c) ? { ...c, unseen_reach_out: false } : c]),
    );
  }, []);

  /** 她的对话读到了（打开、或者重读）：里面的搭话服务器已经记成看过，她那一行先在本地清掉。 */
  const seen = useCallback((characterId: string) => {
    seenAt.current.set(characterId, ++seenCount.current);
    setLatest((all) => {
      const hers = all[characterId];
      return hers?.unseen_reach_out ? { ...all, [characterId]: { ...hers, unseen_reach_out: false } } : all;
    });
  }, []);

  const refresh = useCallback(() => {
    const since = seenCount.current;
    listLatestConversations().then(
      (all) => {
        const next = received(all, since);
        setLatest(next);
        const live = heldRef.current;
        const hers = live && next[live.characterId];
        const { held: shown, past } = onScreenRef.current;
        if (live && hers && shown && !past && !ended.current.has(live)) live.listed(hers);
      },
      () => {}, // 摘要只是预览，拉不到时留着上一次的
    );
  }, [received]);

  useEffect(() => {
    let cancelled = false;
    const since = seenCount.current;
    // 先问账户：没登录或还没同意条款的浏览器会被送去该去的页面。
    getAccount()
      .then((account) => Promise.all([account, listCharacters(), listLatestConversations()]))
      .then(
        ([account, all, summaries]) => {
          if (cancelled) return;
          setAccount(account);
          setConfigured(all);
          setPreferences(listPreferences(account.id, all.map((c) => c.id)));
          setAppearance(loadAppearance(account.id));
          setLatest(received(summaries, since));
        },
        (e) => !cancelled && setFailure(toFailure(e)),
      );
    return () => {
      cancelled = true;
    };
  }, [attempt, received]);

  // 开着网站时她到点的搭话不用刷新就出现：载入了才拉，后台标签页不拉。
  useWhileVisible(
    () => {
      if (configured && !failure) refresh();
    },
    LIST_POLL_MS,
    { atOnceWhenShown: true },
  );

  const showPast = useCallback(
    (shown: boolean) => {
      const was = onScreenRef.current.past;
      if (shown === was) return;
      onScreenRef.current = { ...onScreenRef.current, past: shown };
      setPastShown(shown);
      // 看历史对话时不在她的当前对话上，推迟着的重读不再做；回来时它可能落后了，马上拉一次摘要。
      if (shown) heldRef.current?.offScreen();
      else refresh();
    },
    [refresh],
  );

  const reload = useCallback(() => {
    setFailure(null);
    setConfigured(null);
    setAttempt((n) => n + 1);
  }, []);

  const tidy = useCallback(
    (change: (preferences: ListPreferences) => ListPreferences) => {
      setPreferences((current) => {
        const next = change(current);
        if (userId) keepListPreferences(userId, next); // 写同样的值，重复执行也无妨
        return next;
      });
    },
    [userId],
  );

  const accountChanged = useCallback((change: Account | ((account: Account) => Account)) => {
    setAccount((current) => (typeof change === "function" ? current && change(current) : change));
  }, []);

  const changeAppearance = useCallback(
    (next: Appearance) => {
      setAppearance(next);
      if (userId) keepAppearance(userId, next);
    },
    [userId],
  );

  /** 结束这一份：挂断通话（还在拨号的也算），停下她的声音；留着它画最后一刻。 */
  const end = useCallback((live: LiveConversation) => {
    if (ended.current.has(live)) return;
    ended.current.add(live);
    live.dispose();
  }, []);

  const open = useCallback(
    (characterId: string, userId: string) => {
      const current = heldRef.current;
      showHeld(true);
      if (current && current.characterId === characterId && !ended.current.has(current)) {
        // 外壳里那一份可能落后了（例如离开期间她搭了话）：马上拉一次摘要，有新的就重读。
        refresh();
        return current;
      }
      // 同一时间只和一位角色说话：外壳里原来那一份就此结束（要问的，在来这里之前已经问过了）。
      if (current) end(current);
      const live = new LiveConversation(characterId, {
        listConversations,
        createConversation,
        listMessages: (conversationId) =>
          listMessages(conversationId).then((record) => {
            seen(characterId);
            return record;
          }),
        onListChanged: refresh,
        startCall: (conversationId, preferences, handlers, asmr) =>
          startCall(conversationId, preferences, handlers, userId, asmr),
        sendTurn: (conversationId, text) =>
          sendTurn(conversationId, text, userId, isWebOn(characterId)),
        getPhoto,
        getAffinity,
        appeal,
        preferences: { load: loadCallPreferences, save: saveCallPreferences },
        voice,
        tones,
      });
      heldRef.current = live;
      setHeld(live);
      return live;
    },
    [end, refresh, seen, showHeld, voice, tones],
  );

  const close = useCallback(
    (live: LiveConversation) => {
      if (heldRef.current !== live) return;
      showHeld(false);
      const { call } = live.snapshot();
      if (!isLive(call) && call.phase !== "ended") end(live);
      else live.offScreen();
    },
    [end, showHeld],
  );

  // 不在她的对话页上时，留在外壳里的通话结束了（挂断，或者看完了结束卡）：这一份就此结束，通话条随之消失。
  useEffect(() => {
    if (held && !isHeldShown && snapshot.call.phase === "idle") end(held);
  }, [held, isHeldShown, snapshot, end]);

  // 外壳卸下（离开外壳，或者退出登录、删除全部数据之后整页跳走）：通话挂断。
  useEffect(
    () => () => {
      if (heldRef.current) end(heldRef.current);
    },
    [end],
  );

  const hangUp = useCallback(async () => {
    const live = heldRef.current;
    if (live && isLive(live.snapshot().call)) await live.hangUp();
  }, []);

  /** 在点击这一刻读快照：要问就打开确认框（确认了挂断再 `leave`）并返回 true；不用问返回 false。 */
  const asks = useCallback(
    (href: string, leave: () => void): boolean => {
      const live = heldRef.current;
      const callee = live && isLive(live.snapshot().call) ? live.characterId : null;
      if (departure(href, callee) !== "ask") return false;
      // 挂断都算用户自己挂断（GLOSSARY.md「通话」）。
      setLeaving(() => () => {
        void hangUp();
        leave();
      });
      return true;
    },
    [hangUp],
  );

  const go = useCallback(
    (href: string) => {
      const leave = () => router.push(href);
      if (!asks(href, leave)) leave();
    },
    [asks, router],
  );

  const guard = useCallback(
    (href: string, isDocument = false) =>
      (event: MouseEvent<HTMLAnchorElement>) => {
        if (opensNewTab(event) || event.defaultPrevented) return;
        const leave = () => (isDocument ? window.location.assign(href) : router.push(href));
        if (asks(href, leave)) event.preventDefault();
      },
    [asks, router],
  );

  const callee = characters?.find((c) => c.id === call?.characterId);
  return (
    <DirectoryContext.Provider
      value={{
        account,
        accountChanged,
        characters,
        latest,
        preferences,
        tidy,
        appearance,
        changeAppearance,
        failure,
        refresh,
        reload,
        call,
        ambience: voice.ambience,
        held,
        isHeldShown,
        onScreen: held && isHeldShown && !isPastShown ? held.characterId : null,
        showPast,
        open,
        close,
        go,
        guard,
        hangUp,
      }}
    >
      {children}
      {/* 她的声音：交给 voice，静音也由它管（只看文字，或者她的声音改走电话质感时，这里只是持有音轨）。换页时不卸下。 */}
      <audio ref={voice.attach} autoPlay />
      <MicSheet
        isOpen={snapshot.pickingMic}
        onOpenChange={(isOpen) => (isOpen ? held?.openMicPicker() : held?.closeMicPicker())}
        micId={snapshot.micId}
        onSelect={(micId) => void held?.switchMic(micId)}
      />
      {/* 确认框不在她的子树里：只有中性色和挂断红（DESIGN.md「Hang-up Red」：「挂断并离开」用它）。 */}
      <AlertDialog open={leaving !== null} onOpenChange={(open) => !open && setLeaving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{copy.call.leaveTitle(callee?.name ?? "")}</AlertDialogTitle>
            <AlertDialogDescription>{copy.call.leaveDescription}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{copy.call.stay}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                const leave = leaving;
                setLeaving(null);
                leave?.();
              }}
            >
              {copy.call.leaveConfirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </DirectoryContext.Provider>
  );
}
