import { afterEach, describe, expect, it, vi } from "vitest";
import type { AffinityStatus } from "@/lib/affinity";
import type { AmbienceKind } from "@/lib/ambience";
import {
  ApiError,
  type CallEvent,
  type Conversation,
  type ConversationEvent,
  type LatestConversation,
  type Message,
  type PhotoStatus,
  type ReachOut,
  type Recorded,
  type TurnEvent,
} from "@/lib/api";
import type { Call, CallHandlers, MicInUse } from "@/lib/call";
import type { CallPreferences } from "@/lib/callPreferences";
import { LiveConversation, type Ports } from "@/lib/liveConversation";
import { CALM } from "@/lib/standAnimations";

/** A call that has connected: records what the conversation asks of it. */
class FakeCall implements Call {
  sent: string[] = [];
  textOnly: boolean[] = [];
  micEnabled: boolean[] = [];
  hangUps = 0;
  /** What each switch asked for; the next one resolves with `switchTo`, or fails if it is null. */
  switches: string[] = [];
  switchTo: MicInUse | null = { id: "", fellBack: false };
  constructor(
    readonly maxSeconds: number | null = 1800,
    readonly exempt = false,
    readonly mic: MicInUse = { id: "", fellBack: false },
  ) {}
  switchMic = async (micId: string) => {
    this.switches.push(micId);
    if (!this.switchTo) throw new Error("could not open it");
    return this.switchTo;
  };
  sendText = (text: string) => void this.sent.push(text);
  setTextOnly = (textOnly: boolean) => void this.textOnly.push(textOnly);
  setMicEnabled = (enabled: boolean) => void this.micEnabled.push(enabled);
  hangUp = async () => void this.hangUps++;
  ambienceAuto: boolean[] = [];
  setAmbienceAuto = (on: boolean) => void this.ambienceAuto.push(on);
  leaves = 0;
  leave = async () => void this.leaves++;
}

/** Her voice: records what the conversation tells it. */
class FakeVoice {
  /** The phone texture each dialing asked for. */
  dials: boolean[] = [];
  /** Whether each dialing was of an ASMR call. */
  asmrDials: boolean[] = [];
  played: MediaStreamTrack[] = [];
  textOnly: boolean[] = [];
  disposed = 0;
  dial = (phoneTexture: boolean, asmr = false) => {
    this.dials.push(phoneTexture);
    this.asmrDials.push(asmr);
  };
  play = (track: MediaStreamTrack) => void this.played.push(track);
  setTextOnly = (textOnly: boolean) => void this.textOnly.push(textOnly);
  dispose = () => void this.disposed++;
  /** How the call under way asks the backend to pick the ambience, once it has connected. */
  setAmbienceAuto: ((on: boolean) => void) | null = null;
  callConnected = (setAmbienceAuto: (on: boolean) => void) => void (this.setAmbienceAuto = setAmbienceAuto);
  picked: AmbienceKind[] = [];
  ambiencePicked = (kind: AmbienceKind) => void this.picked.push(kind);
}

/** The phone's own sounds: records what the conversation tells it, in order. */
class FakeTones {
  calls: string[] = [];
  /** What `ringLeft` says: rung once in full already, unless a test says otherwise. */
  left = 0;
  ring = () => void this.calls.push("ring");
  ringLeft = () => this.left;
  connect = () => void this.calls.push("connect");
  hangUp = () => void this.calls.push("hangUp");
  silence = () => void this.calls.push("silence");
}

/** Her voice as a call hands it over. */
const herTrack = { kind: "audio" } as MediaStreamTrack;

/** One `startCall`: it stays dialing until the test says how it ends. */
type Attempt = {
  preferences: CallPreferences;
  handlers: CallHandlers;
  asmr?: boolean;
  connect: (call: Call) => void;
  fail: (error: Error) => void;
};

const NOW = 1_000_000;

function record(id: string): Conversation {
  return { id, character_id: "hikari", created_at: "2026-09-27T00:00:00Z" };
}

type Options = {
  /** Her conversations, newest first; one, "c1", unless a test says otherwise. */
  conversations?: Conversation[];
  /** The messages recorded in "c1". */
  messages?: Recorded[];
  turns?: (text: string) => AsyncIterable<TurnEvent>;
  /** The preferences kept on this device, where they are not the defaults. */
  preferences?: Partial<CallPreferences>;
  /** Ports a test replaces, to make opening fail or wait. */
  ports?: Partial<Ports>;
  /** Her affinity with the user as the page opens; affinity is off without it. */
  affinity?: AffinityStatus;
};

/** A live conversation over fake ports, not yet open: her conversations and their
 * messages, calls the test connects, scripted text turns, preferences kept in memory,
 * a clock that stands still. */
function opening(options: Options = {}) {
  const attempts: Attempt[] = [];
  const created: string[] = [];
  /** Which conversation each call and text turn went to. */
  const sentTo: string[] = [];
  let listChanged = 0;
  /** What each reading of the preferences gave. */
  const loaded: CallPreferences[] = [];
  let stored: CallPreferences = {
    micId: "",
    quality: "standard",
    muted: false,
    phoneTexture: false,
    ...options.preferences,
  };
  const voice = new FakeVoice();
  const tones = new FakeTones();
  const ports: Ports = {
    listConversations: async () => options.conversations ?? [record("c1")],
    createConversation: async () => {
      const conversation = record(`new${created.length + 1}`);
      created.push(conversation.id);
      return conversation;
    },
    listMessages: async (conversationId) => (conversationId === "c1" ? (options.messages ?? []) : []),
    getAffinity: async () => options.affinity ?? null,
    getPhoto: async () => ({ status: "pending", reason: null }),
    onListChanged: () => listChanged++,
    startCall: (conversationId, preferences, handlers, asmr) => {
      sentTo.push(conversationId);
      // 麦克风一拨就打开；要看它打开之前的，测试自己换掉这个 port。
      handlers.onMicOpen();
      return new Promise((connect, fail) => attempts.push({ preferences, handlers, asmr, connect, fail }));
    },
    sendTurn: (conversationId, text) => {
      sentTo.push(conversationId);
      return (options.turns ?? noTurns)(text);
    },
    preferences: {
      load: () => {
        const preferences = { ...stored };
        loaded.push(preferences);
        return preferences;
      },
      save: (change) => (stored = { ...stored, ...change }),
    },
    voice,
    tones,
    now: () => NOW,
    ...options.ports,
  };
  const live = new LiveConversation("hikari", ports);
  let published = 0;
  live.subscribe(() => published++);
  return {
    live,
    attempts,
    created,
    sentTo,
    voice,
    tones,
    loaded,
    preferences: () => stored,
    published: () => published,
    listChanged: () => listChanged,
  };
}

/** A live conversation over fake ports, once it has opened (or failed to). */
async function conversation(options: Options = {}) {
  const opened = opening(options);
  await settled();
  return opened;
}

/** Lets every promise already settled run on. */
function settled(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function* noTurns(): AsyncIterable<TurnEvent> {
  throw new Error("no text turns in this test");
}

/** A text turn whose reply streams these events, then ends. */
function scripted(events: TurnEvent[]): () => AsyncIterable<TurnEvent> {
  return async function* () {
    yield* events;
  };
}

/** A promise the test settles when it is ready. */
function gate(): { gate: Promise<void>; open: () => void } {
  let open = () => {};
  const promise = new Promise<void>((resolve) => (open = resolve));
  return { gate: promise, open };
}

/** Starts a call and lets it connect. */
async function connected(
  live: LiveConversation,
  attempts: Attempt[],
  call = new FakeCall(),
): Promise<FakeCall> {
  const calling = live.startCall();
  attempts[attempts.length - 1].connect(call);
  await calling;
  return call;
}

/** Starts a call that fails to connect with `error`. */
async function failing(live: LiveConversation, attempts: Attempt[], error: Error): Promise<void> {
  const calling = live.startCall();
  attempts[attempts.length - 1].fail(error);
  await calling;
}

function message(
  id: number,
  role: Message["role"],
  text: string,
  createdAt = "2026-09-27T00:00:00Z",
): Message {
  const segments = role === "user" ? [] : [{ ja: text, zh: `（${text}）` }];
  return {
    id,
    role,
    text: role === "user" ? text : null,
    ja: role === "user" ? null : text,
    zh: role === "user" ? null : `（${text}）`,
    segments,
    created_at: createdAt,
    affinity: null,
  };
}

const history = [message(1, "user", "早上好"), message(2, "character", "おはよう")];

describe("LiveConversation: a call", () => {
  it("dials, then counts from when it connects, with the backend's time limit", async () => {
    const { live, attempts } = await conversation();
    const calling = live.startCall();
    expect(live.snapshot().call).toEqual({ phase: "dialing", dial: 1 });

    attempts[0].connect(new FakeCall(1800));
    await calling;

    expect(live.snapshot().call).toEqual({ phase: "connected", dial: 1, since: NOW, maxSeconds: 1800 });
  });

  it("goes straight back to idle when the user hangs up, taking back the reply cut off", async () => {
    const { live, attempts } = await conversation({ messages: history });
    const call = await connected(live, attempts);
    const { handlers } = attempts[0];
    handlers.onEvent({ type: "user", text: "你好", reply: 1 });
    handlers.onEvent({ type: "delta", segment: 0, lang: "ja", text: "こん" });
    handlers.onHearing("还有");

    await live.hangUp();

    expect(call.hangUps).toBe(1);
    expect(live.snapshot()).toMatchObject({ call: { phase: "idle" }, hearing: "", failure: null });
    expect(live.snapshot().entries.map((e) => e.key)).toEqual(["m1", "m2"]);
  });

  it("can be hung up while dialing: back to idle at once, and hung up once it connects", async () => {
    const { live, attempts } = await conversation();
    const calling = live.startCall();

    await live.hangUp();
    expect(live.snapshot().call).toEqual({ phase: "idle" });
    const call = new FakeCall();
    attempts[0].connect(call);
    await calling;

    expect(call.hangUps).toBe(1);
    expect(live.snapshot().call).toEqual({ phase: "idle" });
  });

  it("lets a new call be dialed after one hung up while dialing, which does not disturb it", async () => {
    const { live, attempts } = await conversation();
    const first = live.startCall();
    await live.hangUp();
    const second = live.startCall();

    attempts[0].fail(new Error("无法接通，请稍后再试"));
    await first;
    expect(live.snapshot().call).toEqual({ phase: "dialing", dial: 2 });
    attempts[1].connect(new FakeCall());
    await second;
    expect(live.snapshot().call.phase).toBe("connected");
  });

  // Which reason each failure gives is ruled in `callEnd.test.ts`; this shows the call ending with it.
  it("ends saying why, and that it did not count, when it cannot connect", async () => {
    const { live, attempts } = await conversation();

    await failing(live, attempts, new Error("无法接通，请稍后再试"));

    expect(live.snapshot().call).toMatchObject({ phase: "ended", end: { reason: "not_connected", counts: false } });
  });

  it("ends with the backend's reason and word on the limit, taking back the reply cut off", async () => {
    const { live, attempts } = await conversation({ messages: history });
    await connected(live, attempts);
    const { handlers } = attempts[0];
    handlers.onEvent({ type: "ended", reason: "duration_limit", message: "满 30 分钟了", counts: true });
    handlers.onEvent({ type: "user", text: "你好", reply: 1 });
    handlers.onHearing("还有");

    handlers.onDropped();

    expect(live.snapshot()).toMatchObject({
      call: { phase: "ended", end: { reason: "duration_limit", counts: true } },
      hearing: "",
    });
    expect(live.snapshot().entries.map((e) => e.key)).toEqual(["m1", "m2"]);
  });

  it("shows her hanging up in the log as the backend says she did, and tells the list", async () => {
    const { live, attempts, listChanged } = await conversation({ messages: history });
    await connected(live, attempts);
    const told = listChanged();
    const hungUp = { type: "event", id: 7, kind: "hung_up", created_at: "2026-10-13T03:00:00Z", affinity: null } as const;

    attempts[0].handlers.onEvent({ type: "ended", reason: "she_hung_up", message: "她挂断了电话。", counts: true, event: hungUp });
    attempts[0].handlers.onDropped();

    expect(live.snapshot().call).toMatchObject({ phase: "ended", end: { reason: "she_hung_up", counts: true } });
    expect(live.snapshot().entries.map((e) => e.kind)).toEqual(["user", "character", "event"]);
    expect(listChanged()).toBe(told + 1);
  });

  it("says a call that drops with no word from the backend counted, unless it was exempt", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts, new FakeCall(1800, false));
    attempts[0].handlers.onDropped();
    expect(live.snapshot().call).toMatchObject({ phase: "ended", end: { reason: "dropped", counts: true } });

    await connected(live, attempts, new FakeCall(1800, true));
    attempts[1].handlers.onDropped();
    expect(live.snapshot().call).toMatchObject({ phase: "ended", end: { reason: "dropped", counts: false } });
  });

  it("does not give a new call's drop the reason an earlier call was ended for", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    attempts[0].handlers.onEvent({ type: "ended", reason: "turn_limit", message: "说了 20 轮了", counts: true });
    attempts[0].handlers.onDropped();

    await connected(live, attempts);
    attempts[1].handlers.onDropped();

    expect(live.snapshot().call).toMatchObject({ phase: "ended", end: { reason: "dropped" } });
  });

  it("does not give a new call's drop the reason the last call sends too late", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    await live.hangUp();
    await connected(live, attempts);

    attempts[0].handlers.onEvent({ type: "ended", reason: "turn_limit", message: "说了 20 轮了", counts: true });
    attempts[1].handlers.onDropped();

    expect(live.snapshot().call).toMatchObject({ phase: "ended", end: { reason: "dropped" } });
  });

  it("leaves a call that drops while dialing to fail to connect", async () => {
    const { live, attempts } = await conversation();
    const calling = live.startCall();

    attempts[0].handlers.onDropped();

    expect(live.snapshot().call).toEqual({ phase: "dialing", dial: 1 });
    attempts[0].fail(new Error("无法接通，请稍后再试"));
    await calling;
    expect(live.snapshot().call).toMatchObject({ phase: "ended", end: { reason: "not_connected" } });
  });

  it("goes back to idle from the end, or dials again", async () => {
    const { live, attempts } = await conversation();
    await failing(live, attempts, new Error("无法接通，请稍后再试"));

    live.dismissEnd();
    expect(live.snapshot().call).toEqual({ phase: "idle" });

    await failing(live, attempts, new Error("无法接通，请稍后再试"));
    void live.startCall();
    expect(live.snapshot().call).toEqual({ phase: "dialing", dial: 3 });
    expect(attempts).toHaveLength(3);
  });

  it("hangs up a call that finishes connecting after the page is left", async () => {
    const { live, attempts, published } = await conversation();
    const calling = live.startCall();

    live.dispose();
    const before = published();
    const call = new FakeCall();
    attempts[0].connect(call);
    await calling;

    expect([call.hangUps, call.leaves]).toEqual([0, 1]); // left, not hung up
    expect(published()).toBe(before);
  });

  it("shows no end for a call that fails to connect after the page is left", async () => {
    const { live, attempts } = await conversation();
    const calling = live.startCall();
    live.dispose();
    attempts[0].fail(new Error("无法接通，请稍后再试"));
    await calling;

    expect(live.snapshot().call).toEqual({ phase: "dialing", dial: 1 }); // as it was when the page was left
  });

  it("hangs up the call under way and stops her voice when the page is left", async () => {
    const { live, attempts, voice } = await conversation();
    const call = await connected(live, attempts);

    live.dispose();

    expect([call.hangUps, call.leaves]).toEqual([0, 1]); // left, not hung up
    expect(voice.disposed).toBe(1);
  });

  it("stops showing what it is hearing once the backend takes the words up", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    const { handlers } = attempts[0];
    handlers.onHearing("你好");
    expect(live.snapshot().hearing).toBe("你好");

    handlers.onEvent({ type: "user", text: "你好", reply: 1 });

    expect(live.snapshot().hearing).toBe("");
    expect(live.snapshot().entries.map((e) => e.kind)).toEqual(["user", "character"]);
  });

  it("shows a turn failing in a call while the call goes on", async () => {
    const { live, attempts } = await conversation({ messages: history });
    await connected(live, attempts);
    const { handlers } = attempts[0];
    handlers.onEvent({ type: "user", text: "你好", reply: 1 });

    handlers.onEvent({ type: "error", message: "角色暂时无法回复" });

    expect(live.snapshot()).toMatchObject({
      call: { phase: "connected" },
      failure: { message: "角色暂时无法回复", signedOut: false },
    });
    expect(live.snapshot().entries.map((e) => e.key)).toEqual(["m1", "m2"]);
  });

});

describe("LiveConversation: which call it is", () => {
  it("keeps the same number when the call connects and when it ends", async () => {
    const { live, attempts } = await conversation();
    const calling = live.startCall();
    attempts[0].connect(new FakeCall());
    await calling;

    attempts[0].handlers.onDropped();

    expect(live.snapshot().call).toMatchObject({ phase: "ended", dial: 1 });
  });
});

describe("LiveConversation: how long an ended call was", () => {
  it("counts the whole seconds from when it connected, rounding down, and none when it never did", async () => {
    let clock = NOW;
    const { live, attempts } = await conversation({ ports: { now: () => clock } });
    await connected(live, attempts);

    clock += 83_999;
    attempts[0].handlers.onDropped();

    expect(live.snapshot().call).toMatchObject({ phase: "ended", seconds: 83 });

    await failing(live, attempts, new Error("无法接通，请稍后再试"));

    expect(live.snapshot().call).toMatchObject({ phase: "ended", seconds: null });
  });
});

// spec「通话层的台词卡按她说话的节奏换段」: the line card shows one segment at a time, moving
// on as her voice does.
describe("LiveConversation: the line card", () => {
  afterEach(() => void vi.useRealTimers());

  /** A call in "c1", whose history is the two recorded messages, on a clock the test may stop
   * with `vi.useFakeTimers`; `emit` sends the call under way events. */
  async function inCall(options: { textOnly?: boolean } = {}) {
    const opened = await conversation({ messages: history, ports: { now: () => Date.now() } });
    if (options.textOnly) opened.live.toggleTextOnly();
    await connected(opened.live, opened.attempts);
    const emit = (...events: CallEvent[]) =>
      events.forEach((event) => opened.attempts.at(-1)!.handlers.onEvent(event));
    /** The Japanese of the line the card shows and the one before it. */
    const card = () => {
      const { line, previousLine } = opened.live.snapshot();
      return { line: line?.ja ?? null, previous: previousLine?.ja ?? null };
    };
    return { ...opened, emit, card };
  }

  /** The user's words taken up, as reply `number` starts. */
  function reply(number: number, text = "你好"): CallEvent {
    return { type: "user", text, reply: number };
  }

  /** Some of segment `segment`'s Japanese arrives. */
  function ja(segment: number, text: string): CallEvent {
    return { type: "delta", segment, lang: "ja", text };
  }

  function playing(reply: number, segment: number): CallEvent {
    return { type: "segment-playing", reply, segment };
  }

  /** Her reply under way is recorded, with segments of this Japanese. */
  function recorded(...segments: string[]): CallEvent {
    const reply = message(4, "character", segments.join(""));
    return { type: "done", message: { ...reply, segments: segments.map((ja) => ({ ja, zh: "" })) }, affinity: null };
  }

  it("shows the segment her voice starts playing, filling it in as its text arrives", async () => {
    const { live, emit, card } = await inCall();
    // Nothing from before the call.
    expect(card()).toEqual({ line: null, previous: null });

    emit(reply(1), ja(0, "こんにちは"));
    // Arrived, but not yet said: the text runs ahead of her voice.
    expect(card()).toEqual({ line: null, previous: null });

    emit(playing(1, 0));
    expect(card()).toEqual({ line: "こんにちは", previous: null });
    emit({ type: "delta", segment: 0, lang: "zh", text: "你好" }, ja(1, "元気？"));
    expect(live.snapshot().line).toEqual({ ja: "こんにちは", zh: "你好" });

    emit(playing(1, 1));
    expect(live.snapshot()).toMatchObject({
      line: { ja: "元気？", zh: "" },
      previousLine: { ja: "こんにちは", zh: "你好" },
    });
    // The same segment growing longer is not a new line: the previous one stays put.
    emit(ja(1, "そう"));
    expect(card()).toEqual({ line: "元気？そう", previous: "こんにちは" });
  });

  it("skips a segment her voice never plays, and never goes back to it", async () => {
    const { emit, card } = await inCall();
    emit(reply(1), ja(0, "一"), ja(1, "二"), ja(2, "三"), playing(1, 0));

    emit(playing(1, 2));
    expect(card()).toEqual({ line: "三", previous: "一" });

    emit(playing(1, 1));
    expect(card()).toEqual({ line: "三", previous: "一" });
  });

  it("follows a reply to the end the way it started: by her voice even if text only is switched on midway, at her pace even if it is switched off", async () => {
    const { live, emit, card } = await inCall();
    vi.useFakeTimers();
    emit(reply(1), ja(0, "一"), playing(1, 0));

    live.toggleTextOnly();
    emit(ja(1, "二"));
    expect(card()).toEqual({ line: "一", previous: null });
    emit(playing(1, 1));
    expect(card()).toEqual({ line: "二", previous: "一" });

    emit(recorded("一", "二"), reply(2), ja(0, "三"));
    expect(card()).toEqual({ line: "三", previous: "二" });
    live.toggleTextOnly();
    emit(ja(1, "四"));
    expect(card()).toEqual({ line: "三", previous: "二" });
    vi.advanceTimersByTime(1500);
    expect(card()).toEqual({ line: "四", previous: "三" });
  });

  it("plays a reply started in text only at her pace, the first segment as it arrives, her stand following the card", async () => {
    const { live, emit, card } = await inCall({ textOnly: true });
    vi.useFakeTimers();
    const second = "ねえ…… 今日はとても楽しかったね？"; // 17 characters: 3.4s
    emit(reply(1), ja(0, "うん！"));
    expect(card()).toEqual({ line: "うん！", previous: null });

    emit(ja(1, second), { type: "emotion", reply: 1, segment: 1, emotion: "sad", nod: null }, ja(2, "三"));
    // Even a short line stays 1.5s, and her mood waits for the card.
    vi.advanceTimersByTime(1499);
    expect(card()).toEqual({ line: "うん！", previous: null });
    expect(live.snapshot().stand.mood).toBe("calm");
    vi.advanceTimersByTime(1);
    expect(card()).toEqual({ line: second, previous: "うん！" });
    expect(live.snapshot().stand.mood).toBe("sad");

    vi.advanceTimersByTime(3399);
    expect(card()).toEqual({ line: second, previous: "うん！" });
    vi.advanceTimersByTime(1);
    expect(card()).toEqual({ line: "三", previous: second });

    // The next segment has not arrived: the card stays, and moves on to it as soon as it does.
    vi.advanceTimersByTime(5000);
    expect(card()).toEqual({ line: "三", previous: second });
    emit(ja(3, "四"));
    expect(card()).toEqual({ line: "四", previous: "三" });
  });

  it("waits out a pause in her voice, playing out the rest at her pace once it has not come back for a while, her stand following", async () => {
    const { live, emit, card } = await inCall();
    vi.useFakeTimers();
    // Her pace: 5 characters a second, punctuation too but not spaces, at least 1.5s a line.
    const third = "ねえ…… 今日はとても楽しかったね？"; // 17 characters: 3.4s
    emit(reply(1), ja(0, "一"), ja(1, "二"), ja(2, third), ja(3, "四"));
    emit({ type: "emotion", reply: 1, segment: 2, emotion: "sad", nod: null });
    emit({ type: "bot-started-speaking" }, playing(1, 0));
    vi.advanceTimersByTime(2000);

    // Between two segments her voice pauses, long enough to count as stopping, after the first
    // has had its time at her pace: the card stays with her voice.
    emit({ type: "bot-stopped-speaking" }, recorded("一", "二", third, "四"));
    vi.advanceTimersByTime(500);
    expect(card()).toEqual({ line: "一", previous: null });
    emit({ type: "bot-started-speaking" }, playing(1, 1));
    expect(card()).toEqual({ line: "二", previous: "一" });

    // Her voice stops short and does not come back: 3s on, the card plays out the rest.
    emit({ type: "bot-stopped-speaking" });
    vi.advanceTimersByTime(2999);
    expect(card()).toEqual({ line: "二", previous: "一" });
    vi.advanceTimersByTime(1);
    expect(card()).toEqual({ line: third, previous: "二" });
    expect(live.snapshot().stand.mood).toBe("sad");

    vi.advanceTimersByTime(3399);
    expect(card()).toEqual({ line: third, previous: "二" });
    vi.advanceTimersByTime(1);
    expect(card()).toEqual({ line: "四", previous: third });

    vi.advanceTimersByTime(60_000);
    expect(card()).toEqual({ line: "四", previous: third });
  });

  it("waits a while for her voice to start on a recorded reply, playing out one it never starts on", async () => {
    const { emit, card } = await inCall();
    vi.useFakeTimers();
    // Her text runs ahead of her voice: the reply is often recorded before she starts it.
    emit(reply(1), ja(0, "一"), ja(1, "二"), recorded("一", "二"));
    vi.advanceTimersByTime(2000);
    expect(card()).toEqual({ line: null, previous: null });
    emit({ type: "bot-started-speaking" }, playing(1, 0));
    vi.advanceTimersByTime(60_000);
    expect(card()).toEqual({ line: "一", previous: null });
    emit(playing(1, 1), { type: "bot-stopped-speaking" });

    emit(reply(2), ja(0, "三"), ja(1, "四"), recorded("三", "四"));
    vi.advanceTimersByTime(2999);
    expect(card()).toEqual({ line: "二", previous: "一" });
    vi.advanceTimersByTime(1);
    expect(card()).toEqual({ line: "三", previous: "二" });
    vi.advanceTimersByTime(1500);
    expect(card()).toEqual({ line: "四", previous: "三" });
  });

  it("plays out nothing more once the call is over", async () => {
    const { live, emit, card } = await inCall();
    vi.useFakeTimers();
    emit(reply(1), ja(0, "一"), ja(1, "二"), recorded("一", "二"));
    emit({ type: "emotion", reply: 1, segment: 1, emotion: "happy", nod: null });
    emit({ type: "bot-started-speaking" }, playing(1, 0), { type: "bot-stopped-speaking" });

    await live.hangUp();
    vi.advanceTimersByTime(60_000);

    expect(card()).toEqual({ line: "一", previous: null });
    expect(live.snapshot().stand).toEqual(CALM);
  });

  it.each([
    [
      "speaks",
      async (_live: LiveConversation, emit: (event: CallEvent) => void) => emit({ type: "user-started-speaking" }),
    ],
    [
      "types",
      async (live: LiveConversation) => {
        live.setDraft("等一下");
        await live.submit();
      },
    ],
  ])("stays where her voice was when the user %s over her, until her next reply is said", async (_, interrupt) => {
    const { live, emit, card } = await inCall();
    vi.useFakeTimers();
    emit(reply(1), ja(0, "一"), ja(1, "二"), ja(2, "三"));
    emit({ type: "bot-started-speaking" }, playing(1, 0), playing(1, 1));

    await interrupt(live, emit);
    // Her voice stops, and the reply is recorded before the user's words are taken up.
    emit({ type: "bot-stopped-speaking" }, recorded("一", "二", "三"));
    vi.advanceTimersByTime(60_000);
    expect(card()).toEqual({ line: "二", previous: "一" });

    emit(reply(2, "等一下"), ja(0, "四"));
    expect(card()).toEqual({ line: "二", previous: "一" });
    emit(playing(2, 0));
    expect(card()).toEqual({ line: "四", previous: "二" });
  });

  it("stays where it was in text only once the user's words start a new turn, going straight to her next reply's first segment", async () => {
    const { emit, card } = await inCall({ textOnly: true });
    vi.useFakeTimers();
    emit(reply(1), ja(0, "一"), ja(1, "二"), ja(2, "三"), ja(3, "四"), recorded("一", "二", "三", "四"));
    vi.advanceTimersByTime(1500);
    expect(card()).toEqual({ line: "二", previous: "一" });

    // Speaking alone starts no turn (it may come to nothing): the card goes on at her pace.
    emit({ type: "user-started-speaking" }, { type: "user-stopped-speaking" });
    vi.advanceTimersByTime(1500);
    expect(card()).toEqual({ line: "三", previous: "二" });

    emit(reply(2, "等一下"));
    vi.advanceTimersByTime(60_000);
    expect(card()).toEqual({ line: "三", previous: "二" });
    // The segment she never got to is skipped: the line before is the one the card showed.
    emit(ja(0, "五"));
    expect(card()).toEqual({ line: "五", previous: "三" });
  });

  it("leaves no trace of a reply a follow-on dropped", async () => {
    // GLOSSARY.md「补充」：放下的那次回复不留任何痕迹。
    const { emit, card } = await inCall();
    emit(reply(1, "一"), ja(0, "い"), playing(1, 0));
    expect(card()).toEqual({ line: "い", previous: null });

    // A follow-on joins the turn: the reply she had started saying is dropped.
    emit(reply(2, "一\n二"));
    expect(card()).toEqual({ line: null, previous: null });

    emit(ja(0, "に"), playing(2, 0));
    expect(card()).toEqual({ line: "に", previous: null });
  });

  it("starts each call with nothing on it, not taking a line from the last call", async () => {
    const { live, attempts, emit, card } = await inCall();
    emit(reply(1), ja(0, "こんにちは"), playing(1, 0), recorded("こんにちは"));
    await live.hangUp();

    await connected(live, attempts);
    expect(card()).toEqual({ line: null, previous: null });

    emit(reply(1), ja(0, "やあ"), playing(1, 0));
    expect(card()).toEqual({ line: "やあ", previous: null });
  });
});

describe("LiveConversation: a call that is over", () => {
  /** How the first call ends, its turn under way; after that its messages come too late. */
  const endings: [string, (live: LiveConversation, attempts: Attempt[]) => Promise<void>][] = [
    ["the user hangs up", (live) => live.hangUp()],
    ["it drops", async (_live, attempts) => attempts[0].handlers.onDropped()],
    [
      "the backend says why, then it drops",
      async (_live, attempts) => {
        attempts[0].handlers.onEvent({ type: "ended", reason: "turn_limit", message: "说了 20 轮了", counts: true });
        attempts[0].handlers.onDropped();
      },
    ],
    [
      "the next call has connected",
      async (live, attempts) => {
        await live.hangUp();
        await connected(live, attempts);
        const next = attempts[1].handlers;
        next.onEvent({ type: "user", text: "再说一次", reply: 1 });
        next.onEvent({ type: "delta", segment: 0, lang: "ja", text: "もう" });
        next.onEvent({ type: "bot-started-speaking" });
        next.onHearing("还有");
        // 下一通进行中的回合：迟到的消息不能撤掉它。
        expect(live.snapshot().entries.at(-1)).toMatchObject({ kind: "character", pending: true });
      },
    ],
  ];

  /** What comes too late from the first call. */
  const late: [string, (handlers: CallHandlers) => void][] = [
    ["what the user said", (h) => h.onEvent({ type: "user", text: "迟到的话", reply: 1 })],
    ["a delta of the reply", (h) => h.onEvent({ type: "delta", segment: 0, lang: "ja", text: "おそい" })],
    ["the reply recorded", (h) => h.onEvent({ type: "done", message: message(9, "character", "おそい"), affinity: null })],
    ["an error", (h) => h.onEvent({ type: "error", message: "角色暂时无法回复" })],
    [
      "the backend saying why it hangs up",
      (h) => h.onEvent({ type: "ended", reason: "duration_limit", message: "满 30 分钟了", counts: true }),
    ],
    ["what the user is saying so far", (h) => h.onHearing("迟到的转写")],
    ["the microphone opening", (h) => h.onMicOpen()],
    ["the microphone lost", (h) => h.onMicLost(null)],
    ["the call dropping", (h) => h.onDropped()],
    ["an event only her stand follows", (h) => h.onEvent({ type: "bot-started-speaking" })],
    ["her voice", (h) => h.onCharacterAudio(herTrack)],
  ];

  it.each(endings)("once %s, changes nothing by anything the call sends too late", async (_ending, end) => {
    const { live, attempts, listChanged, voice, tones } = await conversation({ messages: history });
    await connected(live, attempts);
    const { handlers } = attempts[0];
    handlers.onEvent({ type: "user", text: "你好", reply: 1 });
    handlers.onEvent({ type: "delta", segment: 0, lang: "ja", text: "こん" });
    await end(live, attempts);
    const before = live.snapshot();
    const told = listChanged();
    const played = voice.played.length;
    const heard = tones.calls.length;

    for (const [what, send] of late) {
      send(handlers);

      expect(live.snapshot(), what).toEqual(before);
      expect(listChanged(), what).toBe(told);
      expect(voice.played, what).toHaveLength(played);
      expect(tones.calls, what).toHaveLength(heard);
    }
  });
});

describe("LiveConversation: mic mute", () => {
  it("turns the microphone off and on in the browser only", async () => {
    const { live, attempts } = await conversation();
    const call = await connected(live, attempts);

    live.toggleMic();
    expect(live.snapshot().micMuted).toBe(true);
    live.toggleMic();

    expect(live.snapshot().micMuted).toBe(false);
    expect(call.micEnabled).toEqual([false, true]);
    expect(call.textOnly).toEqual([]);
  });

  it("still sends what is typed while the microphone is muted", async () => {
    // GLOSSARY.md「麦克风静音」：打字不受影响。
    const { live, attempts } = await conversation();
    const call = await connected(live, attempts);
    live.toggleMic();
    live.setDraft("你好");

    await live.submit();

    expect(call.sent).toEqual(["你好"]);
  });

  it("starts each call with the microphone on", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    live.toggleMic();
    await live.hangUp();

    await connected(live, attempts);

    expect(live.snapshot().micMuted).toBe(false);
  });

  it("does nothing until the call has connected", async () => {
    const { live } = await conversation();
    void live.startCall();

    live.toggleMic();

    expect(live.snapshot().micMuted).toBe(false);
  });
});

describe("LiveConversation: switching the microphone", () => {
  it("switches the call to the chosen microphone, which becomes the default for later calls", async () => {
    const { live, attempts, preferences } = await conversation();
    const call = await connected(live, attempts);
    call.switchTo = { id: "usb", fellBack: false };

    await live.switchMic("usb");

    expect(call.switches).toEqual(["usb"]);
    expect(call.hangUps).toBe(0);
    expect(live.snapshot().micId).toBe("usb");
    expect(preferences().micId).toBe("usb");
  });

  it("keeps the microphone muted across a switch", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    live.toggleMic();

    await live.switchMic("usb");

    expect(live.snapshot().micMuted).toBe(true);
  });

  it("starts a call on the default microphone, saying which one is in use", async () => {
    const { live, attempts } = await conversation();
    const calling = live.startCall();
    attempts[0].connect(new FakeCall(1800, false, { id: "usb", fellBack: false }));
    await calling;

    expect(attempts[0].preferences.micId).toBe("");
    expect(live.snapshot().micId).toBe("usb");
    expect(live.snapshot().micNotice).toBeNull();
  });

  it("says so when the default microphone is gone and the system default is used", async () => {
    const { live, attempts, preferences } = await conversation();
    await connected(live, attempts, new FakeCall(1800, false, { id: "", fellBack: true }));

    expect(live.snapshot().micId).toBe("");
    expect(live.snapshot().micNotice).toBe("fell_back");
    // Kept: plugged back in, it is used again.
    expect(preferences().micId).toBe("");
  });

  it("says so when the microphone in use is unplugged during the call", async () => {
    const { live, attempts } = await conversation();
    const call = new FakeCall(1800, false, { id: "usb", fellBack: false });
    await connected(live, attempts, call);

    attempts[0].handlers.onMicLost({ id: "", fellBack: true });

    expect(live.snapshot().micId).toBe("");
    expect(live.snapshot().micNotice).toBe("fell_back");
    expect(call.hangUps).toBe(0);
  });

  it("says so when the microphone is unplugged and no other one opens, keeping the call", async () => {
    const { live, attempts } = await conversation();
    const call = await connected(live, attempts, new FakeCall(1800, false, { id: "usb", fellBack: false }));

    attempts[0].handlers.onMicLost(null);

    expect(live.snapshot().micNotice).toBe("lost");
    expect(live.snapshot().call.phase).toBe("connected");
    expect(call.hangUps).toBe(0);
  });

  it("leaves a microphone lost while dialing to the call it connects with", async () => {
    const { live, attempts } = await conversation();
    void live.startCall();
    const dialing = live.snapshot();

    attempts[0].handlers.onMicLost(null);

    expect(live.snapshot()).toEqual(dialing);
  });

  it("keeps the microphone in use when the chosen one cannot be opened", async () => {
    const { live, attempts, preferences } = await conversation();
    const call = await connected(live, attempts, new FakeCall(1800, false, { id: "usb", fellBack: false }));
    call.switchTo = null;

    await live.switchMic("broken");

    expect(live.snapshot().micId).toBe("usb");
    expect(live.snapshot().micNotice).toBe("unavailable");
    expect(preferences().micId).toBe("");
  });

  it("clears the notice after a switch that works, and when the call is over", async () => {
    const { live, attempts } = await conversation();
    const call = await connected(live, attempts, new FakeCall(1800, false, { id: "", fellBack: true }));
    call.switchTo = { id: "usb", fellBack: false };

    await live.switchMic("usb");
    expect(live.snapshot().micNotice).toBeNull();

    attempts[0].handlers.onMicLost({ id: "", fellBack: true });
    await live.hangUp();
    expect(live.snapshot().micNotice).toBeNull();
  });

  it("does nothing until the call has connected", async () => {
    const { live, preferences } = await conversation();
    void live.startCall();

    await live.switchMic("usb");

    expect(preferences().micId).toBe("");
  });

  it("does not show a switch that finishes after the call is over", async () => {
    const { live, attempts } = await conversation();
    const call = await connected(live, attempts);
    call.switchTo = { id: "usb", fellBack: false };

    const switching = live.switchMic("usb");
    await live.hangUp();
    await switching;

    expect(live.snapshot().call.phase).toBe("idle");
    expect(live.snapshot().micId).toBe("");
  });
});

describe("LiveConversation: text only", () => {
  it("keeps the switch in the preferences and tells the call under way", async () => {
    const { live, attempts, preferences } = await conversation();
    const call = await connected(live, attempts);

    live.toggleTextOnly();

    expect(live.snapshot().textOnly).toBe(true);
    expect(preferences().muted).toBe(true);
    expect(call.textOnly).toEqual([true]);
  });

  it("starts a call with the switch as it is", async () => {
    const { live, attempts } = await conversation();
    live.toggleTextOnly();

    void live.startCall();

    expect(attempts[0].preferences.muted).toBe(true);
  });
});

/** A promise that never settles: what the test leaves waiting. */
function never<T>(): Promise<T> {
  return new Promise(() => {});
}

/** Where the conversation can be, and whether a call can be dialed and a new conversation
 * started there (`canDial`, `canStartNew`). Her conversation has a record in each: an empty
 * one is the new conversation tests'. */
const states: [string, () => Promise<ReturnType<typeof opening>>, boolean][] = [
  ["her conversation is opening", async () => opening({ ports: { listConversations: never } }), false],
  ["it is open", () => conversation({ messages: history }), true],
  [
    "a text turn is under way",
    async () => {
      const opened = await conversation({
        messages: history,
        turns: async function* () {
          yield { type: "user", text: "你好" };
          await never();
        },
      });
      opened.live.setDraft("你好");
      void opened.live.submit();
      await settled();
      return opened;
    },
    false,
  ],
  [
    "a new conversation is being started",
    async () => {
      const opened = await conversation({ messages: history, ports: { createConversation: never } });
      void opened.live.startNewConversation();
      return opened;
    },
    false,
  ],
  [
    "a call is dialing",
    async () => {
      const opened = await conversation({ messages: history });
      void opened.live.startCall();
      return opened;
    },
    false,
  ],
  [
    "a call is connected",
    async () => {
      const opened = await conversation({ messages: history });
      await connected(opened.live, opened.attempts);
      return opened;
    },
    false,
  ],
  [
    "the last call has ended",
    async () => {
      const opened = await conversation({ messages: history });
      await failing(opened.live, opened.attempts, new Error("无法接通，请稍后再试"));
      return opened;
    },
    true,
  ],
  [
    "it could not be opened",
    () =>
      conversation({
        ports: {
          listConversations: async () => {
            throw new Error("Failed to fetch");
          },
        },
      }),
    false,
  ],
];

describe("LiveConversation: what can be done", () => {
  it.each(states)(
    "when %s, says whether a call can be dialed and a new conversation started, and dials her voice only if so",
    async (_, at, can) => {
      const { live, voice } = await at();
      const dials = voice.dials.length;

      expect(live.snapshot()).toMatchObject({ canDial: can, canStartNew: can });
      void live.startCall();

      expect(voice.dials).toHaveLength(dials + (can ? 1 : 0));
    },
  );
});

describe("LiveConversation: a cooldown", () => {
  // GLOSSARY.md「冷却」：她暂时不想说话，到解除时刻自己恢复；页面上的时钟停在 NOW。
  const until = new Date(NOW + 60_000).toISOString();
  const cooling = { tier: "cold", cooldown_until: until } as const;
  const thawed = { tier: "cold", cooldown_until: null } as const;

  it("begins as a turn leaves her in one: no turns, no calls, but a new conversation; it ends by itself on time", async () => {
    vi.useFakeTimers();
    const change = { up: false, tier_changed: false, tier: "cold", cooldown: true, thawed: false } as const;
    const opened = opening({
      affinity: { tier: "cold", cooldown_until: null },
      turns: scripted([
        { type: "user", text: "随便你" },
        { type: "done", message: { ...message(4, "character", "そう"), affinity: change }, affinity: cooling },
      ]),
    });
    await vi.advanceTimersByTimeAsync(0);
    const { live, sentTo } = opened;
    live.setDraft("随便你");
    await live.submit();

    const shown = live.snapshot();
    live.setDraft("在吗");
    await live.submit();
    void live.startCall();
    const refused = live.snapshot();
    vi.advanceTimersByTime(59_999);
    const stillCooling = live.snapshot().cooling;
    vi.advanceTimersByTime(1);

    expect(shown).toMatchObject({ affinity: cooling, cooling: true, canDial: false, canStartNew: true });
    expect(sentTo).toEqual(["c1"]); // the second turn and the call never went out
    expect(refused).toMatchObject({ draft: "在吗", call: { phase: "idle" } });
    expect(stillCooling).toBe(true);
    expect(live.snapshot()).toMatchObject({ affinity: thawed, cooling: false, canDial: true });
    vi.useRealTimers();
  });

  it("opens in one read from the backend", async () => {
    const { live } = await conversation({ affinity: cooling });

    expect(live.snapshot()).toMatchObject({ cooling: true, canDial: false });
  });

  it("shows one a text turn is refused for, returning the text to the input, with no failure", async () => {
    const { live, listChanged } = await conversation({
      messages: history,
      affinity: { tier: "trust", cooldown_until: null },
      turns: async function* (): AsyncIterable<TurnEvent> {
        throw new ApiError("她现在不想说话，晚些时候再来吧", 409, { cooldownUntil: until });
      },
    });
    live.setDraft("你好");

    await live.submit();

    expect(live.snapshot()).toMatchObject({ draft: "你好", busy: false, failure: null, affinity: cooling, cooling: true });
    expect(live.snapshot().entries.map((e) => e.key)).toEqual(["m1", "m2"]);
    expect(listChanged()).toBe(0);
  });

  it("shows one a call is refused for, with no end to dismiss", async () => {
    const { live, attempts } = await conversation({ affinity: { tier: "trust", cooldown_until: null } });

    await failing(live, attempts, new ApiError("她现在不想说话，晚些时候再来吧", 409, { cooldownUntil: until }));

    expect(live.snapshot()).toMatchObject({ call: { phase: "idle" }, affinity: cooling, cooling: true });
  });
});

describe("LiveConversation: an ended conversation", () => {
  // GLOSSARY.md「已结束的对话」：她结束了这个对话，它从此只能翻看；开个新对话就能接着聊。
  const ended: ConversationEvent = {
    type: "event",
    id: 7,
    kind: "ended",
    created_at: "2026-09-27T00:00:01Z",
    affinity: null,
  };

  it("ends as the turn's done says she ended it: no turns, no calls, until a new conversation", async () => {
    const turns: TurnEvent[][] = [
      [
        { type: "user", text: "随便你" },
        { type: "done", message: message(4, "character", "そう"), affinity: null, ended },
      ],
      [
        { type: "user", text: "重新开始吧" },
        { type: "done", message: message(2, "character", "うん"), affinity: null, ended: null },
      ],
    ];
    const { live, sentTo, voice } = await conversation({
      messages: history,
      turns: () => scripted(turns.shift() ?? [])(),
    });
    live.setDraft("随便你");
    await live.submit();

    const shown = live.snapshot();
    live.setDraft("在吗");
    await live.submit();
    void live.startCall();
    const refused = live.snapshot();
    await live.startNewConversation();
    const fresh = live.snapshot();
    live.setDraft("重新开始吧");
    await live.submit();

    expect(shown).toMatchObject({ ended: true, canDial: false, canStartNew: true });
    expect(shown.entries.map((e) => e.key)).toEqual(["m1", "m2", "new-2", "m4", "e7"]);
    expect(refused).toMatchObject({ draft: "在吗", call: { phase: "idle" } });
    expect(voice.dials).toEqual([]);
    expect(fresh).toMatchObject({ conversation: { status: "open", id: "new1" }, ended: false, canDial: true });
    expect(sentTo).toEqual(["c1", "new1"]); // the turn and the call in the ended one never went out
    expect(live.snapshot().entries.map((e) => e.key)).toEqual(["new-0", "m2"]);
  });

  it("opens ended when its record has her ending it", async () => {
    const { live } = await conversation({ messages: [...history, ended] });

    expect(live.snapshot()).toMatchObject({ ended: true, canDial: false, canStartNew: true });
  });


  it("ends in a cooldown as the turn's done says both; a new conversation opened in it is cooling, not ended", async () => {
    const cooling = { tier: "cold", cooldown_until: new Date(NOW + 60_000).toISOString() } as const;
    const { live } = await conversation({
      messages: history,
      affinity: { tier: "cold", cooldown_until: null },
      turns: scripted([
        { type: "user", text: "随便你" },
        { type: "done", message: message(4, "character", "そう"), affinity: cooling, ended },
      ]),
    });
    live.setDraft("随便你");
    await live.submit();

    const shown = live.snapshot();
    await live.startNewConversation();

    expect(shown).toMatchObject({ ended: true, cooling: true, affinity: cooling, canDial: false, canStartNew: true });
    expect(live.snapshot()).toMatchObject({
      conversation: { status: "open", id: "new1" },
      ended: false,
      cooling: true,
      canDial: false,
    });
  });
});

describe("LiveConversation: her voice", () => {
  it("is told the phone texture as the call is dialed, still inside the user's click", async () => {
    const { live, attempts, voice, loaded } = await conversation({ preferences: { phoneTexture: true } });
    const read = loaded.length;

    void live.startCall();

    // Nothing awaited yet: the audio context may still be resumed.
    expect(voice.dials).toEqual([true]);
    expect(loaded).toHaveLength(read + 1);
    expect(attempts[0].preferences).toBe(loaded.at(-1));
  });

  it("dials an ASMR call by the ear, and asks for one, an ordinary call not", async () => {
    const { live, attempts, voice } = await conversation({ preferences: { phoneTexture: true } });

    void live.startCall(true);

    expect(voice.asmrDials).toEqual([true]);
    expect(attempts[0].asmr).toBe(true);
  });

  it("lets her voice ask the call under way for the ambience, and hands it what the backend picks", async () => {
    const { live, attempts, voice } = await conversation();
    const call = await connected(live, attempts);

    voice.setAmbienceAuto?.(true);
    attempts[0].handlers.onEvent({ type: "ambience", kind: "rain" });

    expect(call.ambienceAuto).toEqual([true]);
    expect(voice.picked).toEqual(["rain"]);
  });

  it("plays her voice in the call under way", async () => {
    const { live, attempts, voice } = await conversation();
    await connected(live, attempts);

    attempts[0].handlers.onCharacterAudio(herTrack);

    expect(voice.played).toEqual([herTrack]);
  });

  it("is silent from the start when text only was on already", () => {
    const { voice } = opening({ preferences: { muted: true } });

    expect(voice.textOnly).toEqual([true]);
  });

  it("is told at once when text only is switched", () => {
    const { live, voice } = opening();

    live.toggleTextOnly();
    expect(voice.textOnly).toEqual([false, true]);
    live.toggleTextOnly();
    expect(voice.textOnly).toEqual([false, true, false]);
  });

});

describe("LiveConversation: the call tones", () => {
  it("rings once the microphone is open, not before", async () => {
    let micOpen = () => {};
    const { live, tones } = await conversation({
      ports: {
        startCall: (_conversationId, _preferences, handlers) => {
          micOpen = handlers.onMicOpen;
          return new Promise(() => {});
        },
      },
    });

    void live.startCall();
    // 手机上打开麦克风会让正在放的声音断一下：等它打开了再响。
    expect(tones.calls).toEqual([]);

    micOpen();
    expect(tones.calls).toEqual(["ring"]);
  });

  /** The ways a call ends, however it got there. */
  const endings: [string, (live: LiveConversation, attempts: Attempt[]) => Promise<void>][] = [
    [
      "the user hangs up while dialing",
      async (live) => {
        void live.startCall();
        await live.hangUp();
      },
    ],
    [
      "the user hangs up a connected call",
      async (live, attempts) => {
        await connected(live, attempts);
        await live.hangUp();
      },
    ],
    [
      "the backend says why, then it drops",
      async (live, attempts) => {
        await connected(live, attempts);
        attempts[0].handlers.onEvent({ type: "ended", reason: "turn_limit", message: "说了 20 轮了", counts: true });
        attempts[0].handlers.onDropped();
      },
    ],
    [
      "it fails to connect",
      async (live, attempts) => {
        await failing(live, attempts, new Error("无法接通，请稍后再试"));
      },
    ],
  ];

  it.each(endings)("plays the hang-up tone once when %s", async (_what, end) => {
    const { live, attempts, tones } = await conversation();

    await end(live, attempts);

    expect(tones.calls.filter((call) => call === "hangUp")).toHaveLength(1);
  });

  it("stops the ringback alone when the page is left mid-call, never playing the hang-up tone", async () => {
    const connected1 = await conversation();
    await connected(connected1.live, connected1.attempts);

    connected1.live.dispose();

    expect(connected1.tones.calls).toEqual(["ring", "connect", "silence"]);

    // 拨号中离开也一样：铃停，不播挂断音。
    const dialing = await conversation();
    void dialing.live.startCall();

    dialing.live.dispose();

    expect(dialing.tones.calls).toEqual(["ring", "silence"]);
  });

  it("stays quiet through everything but the call itself", async () => {
    const { live, attempts, tones } = await conversation();
    const call = await connected(live, attempts);
    // 铃声已经响满一遍：立即接通，麦克风一直开着。
    expect(call.micEnabled).toEqual([]);

    // 只看文字，收起和展开通话层，都不碰电话的声音。
    live.collapse();
    live.expand();
    live.toggleTextOnly();
    expect(tones.calls).toEqual(["ring", "connect"]);

    // 断线才有结束页可看（用户自己挂断直接回 idle）。
    attempts[0].handlers.onDropped();
    expect(live.snapshot().call).toMatchObject({ phase: "ended" });
    expect(tones.calls).toEqual(["ring", "connect", "hangUp"]);

    // 看完结束页回到 idle、开新对话，都不发声。
    live.dismissEnd();
    await live.startNewConversation();
    expect(tones.calls).toEqual(["ring", "connect", "hangUp"]);
  });
});

describe("LiveConversation: the ringback rung once in full", () => {
  afterEach(() => void vi.useRealTimers());

  /** A call that connects 3 seconds before the ringback has rung once in full. */
  async function heldCall() {
    const opened = await conversation();
    vi.useFakeTimers();
    opened.tones.left = 3000;
    const call = new FakeCall();
    const calling = opened.live.startCall();
    opened.attempts[0].connect(call);
    await vi.advanceTimersByTimeAsync(0);
    return { ...opened, call, calling };
  }

  it("keeps dialing, the microphone off, until it has rung once, then connects, its time limit counted from before", async () => {
    const { live, tones, call, calling } = await heldCall();
    expect(live.snapshot().call).toEqual({ phase: "dialing", dial: 1 });
    expect(call.micEnabled).toEqual([false]);
    expect(tones.calls).toEqual(["ring"]);

    await vi.advanceTimersByTimeAsync(2999);
    expect(live.snapshot().call.phase).toBe("dialing");

    await vi.advanceTimersByTimeAsync(1);
    await calling;
    // 后端从真正连上时算起：多响的 3 秒也在时长上限里。
    expect(live.snapshot().call).toEqual({ phase: "connected", dial: 1, since: NOW, maxSeconds: 1797 });
    expect(call.micEnabled).toEqual([false, true]);
    expect(tones.calls).toEqual(["ring", "connect"]);
  });

  it("hangs the call up at once when the user hangs up while it rings on", async () => {
    const { live, tones, call, calling } = await heldCall();

    await live.hangUp();
    await calling;

    expect(live.snapshot().call).toEqual({ phase: "idle" });
    expect(call.hangUps).toBe(1);
    expect(call.micEnabled).toEqual([false]);
    expect(tones.calls).toEqual(["ring", "hangUp"]);
  });

  it("ends, never having connected, when it drops while it rings on", async () => {
    const { live, attempts, call, calling } = await heldCall();

    attempts[0].handlers.onDropped();
    await calling;

    expect(live.snapshot().call).toEqual({
      phase: "ended",
      dial: 1,
      end: { reason: "dropped", detail: null, counts: true },
      seconds: null,
    });
    expect(call.micEnabled).toEqual([false]);
  });

  it("leaves the call at once when the page is left while it rings on", async () => {
    const { live, tones, call, calling } = await heldCall();

    live.dispose();
    await calling;

    expect(call.leaves).toBe(1);
    expect(call.hangUps).toBe(0);
    expect(tones.calls).toEqual(["ring", "silence"]);
  });
});

describe("LiveConversation: typing", () => {
  it("keeps the draft within the 2000 characters the backend accepts", async () => {
    const { live } = await conversation();

    live.setDraft("あ".repeat(2005));

    expect(live.snapshot().draft).toBe("あ".repeat(2000));
  });

  it("records a text turn the backend answers", async () => {
    const { live } = await conversation({
      messages: history,
      turns: scripted([
        { type: "user", text: "你好" },
        { type: "delta", segment: 0, lang: "ja", text: "こんにちは" },
        { type: "done", message: message(4, "character", "こんにちは"), affinity: null },
      ]),
    });
    live.setDraft(" 你好 ");

    await live.submit();

    expect(live.snapshot()).toMatchObject({ draft: "", busy: false, failure: null });
    expect(live.snapshot().entries.at(-1)).toEqual({
      kind: "character",
      key: "m4",
      segments: [{ ja: "こんにちは", zh: "（こんにちは）" }],
      pending: false,
      at: "2026-09-27T00:00:00Z",
      affinity: null,
      photo: null,
    });
  });

  it("shows her affinity as each recorded turn leaves it, and how it changed for 2.6s", async () => {
    vi.useFakeTimers();
    const change = { up: true, tier_changed: true, tier: "close", cooldown: false, thawed: false } as const;
    const close = { tier: "close", cooldown_until: null } as const;
    const turn = (affinity: AffinityStatus | null) =>
      scripted([
        { type: "user", text: "你好" },
        { type: "done", message: { ...message(4, "character", "こんにちは"), affinity: change }, affinity },
      ])();
    let next = turn(close);
    const opened = opening({ affinity: { tier: "trust", cooldown_until: null }, turns: () => next });
    await vi.advanceTimersByTimeAsync(0);
    const { live } = opened;
    const opensWith = live.snapshot().affinity;

    live.setDraft("你好");
    await live.submit();
    const shown = live.snapshot();
    vi.advanceTimersByTime(2599);
    const stillShown = live.snapshot().affinityCue;
    vi.advanceTimersByTime(1);
    const gone = live.snapshot().affinityCue;
    next = turn(null); // turned off meanwhile: nothing to show
    live.setDraft("你好");
    await live.submit();

    expect(opensWith).toEqual({ tier: "trust", cooldown_until: null });
    expect(shown).toMatchObject({ affinity: close, affinityCue: { change, key: 1 } });
    expect(stillShown).not.toBeNull();
    expect(gone).toBeNull();
    expect(live.snapshot()).toMatchObject({ affinity: null, affinityCue: null });
    vi.useRealTimers();
  });

  it.each([
    [
      "the backend says it failed",
      scripted([
        { type: "user", text: "你好" },
        { type: "error", message: "角色暂时无法回复" },
      ]),
      "角色暂时无法回复",
      false,
    ],
    [
      "the reply stops without being done",
      scripted([
        { type: "user", text: "你好" },
        { type: "delta", segment: 0, lang: "ja", text: "こん" },
      ]),
      "回复中断了，请再试一次",
      // 连接断了，前端分不清是服务出错还是回复其实已经记下，所以不下结论。
      undefined,
    ],
    [
      "the request fails",
      async function* (): AsyncIterable<TurnEvent> {
        throw new ApiError("本小时的对话次数已用完", 429);
      },
      "本小时的对话次数已用完",
      false,
    ],
  ])(
    "takes back a text turn and returns the text to the input when %s, saying whether it counts and not telling the list",
    async (_, turns, why, counts) => {
      // GLOSSARY.md「限额」「回合」：失败的回合和被拒绝的回合都不计入限额。
      const { live, listChanged } = await conversation({ messages: history, turns });
      live.setDraft("你好");

      await live.submit();

      expect(live.snapshot()).toMatchObject({
        draft: "你好",
        busy: false,
        failure: { message: why, signedOut: false },
      });
      expect(live.snapshot().failure?.countsTowardLimit).toBe(counts);
      expect(live.snapshot().entries.map((e) => e.key)).toEqual(["m1", "m2"]);
      expect(listChanged()).toBe(0);
    },
  );

  it("hands what the user types in a call to the call instead of starting a text turn", async () => {
    const { live, attempts } = await conversation({ messages: history });
    const call = await connected(live, attempts);
    live.setDraft("你好");

    await live.submit();

    expect(call.sent).toEqual(["你好"]);
    expect(live.snapshot()).toMatchObject({ draft: "", busy: false });
    expect(live.snapshot().entries.map((e) => e.key)).toEqual(["m1", "m2"]);
  });

  it("sends nothing while a call is dialing, keeping the text in the input", async () => {
    const turns: string[] = [];
    const { live, attempts } = await conversation({
      turns: (text) => {
        turns.push(text);
        return scripted([])();
      },
    });
    const calling = live.startCall();
    live.setDraft("你好");

    await live.submit();
    const call = new FakeCall();
    attempts[0].connect(call);
    await calling;

    expect(turns).toEqual([]);
    expect(call.sent).toEqual([]);
    expect(live.snapshot().draft).toBe("你好");
  });

  it("lets a text turn under way finish when the page is left, showing no more of it", async () => {
    const { gate: answered, open: answer } = gate();
    let finished = false;
    const { live, published } = await conversation({
      turns: async function* () {
        yield { type: "user", text: "你好" };
        await answered;
        yield { type: "done", message: message(4, "character", "こんにちは"), affinity: null };
        finished = true;
      },
    });
    live.setDraft("你好");
    const sending = live.submit();

    live.dispose();
    const before = published();
    answer();
    await sending;

    expect(finished).toBe(true);
    expect(published()).toBe(before);
  });
});

describe("LiveConversation: photos", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const photoOf = (live: LiveConversation, key: string) => {
    const entry = live.snapshot().entries.find((e) => e.key === key);
    return entry?.kind === "character" ? entry.photo : undefined;
  };

  it("asks after a photo sent in a text turn every 2s until it comes out, then no more", async () => {
    vi.useFakeTimers();
    const asked: string[] = [];
    const answers: PhotoStatus[] = [
      { status: "pending", reason: null },
      { status: "ready", reason: null },
    ];
    const opened = opening({
      turns: scripted([
        { type: "user", text: "发张自拍" },
        { type: "photo", id: "p1", status: "pending" },
        { type: "delta", segment: 0, lang: "ja", text: "はい" },
        {
          type: "done",
          message: { ...message(4, "character", "はい"), photo: { id: "p1", status: "pending", reason: null } },
          affinity: null,
        },
      ]),
      ports: {
        getPhoto: async (id) => {
          asked.push(id);
          return answers.shift() ?? { status: "ready", reason: null };
        },
      },
    });
    await vi.advanceTimersByTimeAsync(0);
    const { live } = opened;

    live.setDraft("发张自拍");
    await live.submit();
    await vi.advanceTimersByTimeAsync(1999);
    const notYet = [...asked];
    await vi.advanceTimersByTimeAsync(1);
    const stillPending = photoOf(live, "m4");
    await vi.advanceTimersByTimeAsync(2000);
    const out = photoOf(live, "m4");
    await vi.advanceTimersByTimeAsync(10_000);

    expect(notYet).toEqual([]);
    expect(stillPending).toEqual({ id: "p1", status: "pending", reason: null });
    expect(out).toEqual({ id: "p1", status: "ready", reason: null });
    expect(asked).toEqual(["p1", "p1"]);
  });

  it("asks after one still being generated as the conversation opens, again if it cannot be asked", async () => {
    vi.useFakeTimers();
    const answers: (PhotoStatus | Error)[] = [new Error("offline"), { status: "failed", reason: "timeout" }];
    const opened = opening({
      messages: [
        message(1, "user", "发张自拍"),
        { ...message(2, "character", "はい"), photo: { id: "p1", status: "pending", reason: null } },
      ],
      ports: {
        getPhoto: async () => {
          const answer = answers.shift();
          if (!answer || answer instanceof Error) throw answer ?? new Error("asked once too often");
          return answer;
        },
      },
    });
    await vi.advanceTimersByTimeAsync(0);

    await vi.advanceTimersByTimeAsync(2000);
    const afterFailing = photoOf(opened.live, "m2");
    await vi.advanceTimersByTimeAsync(2000);

    expect(afterFailing).toEqual({ id: "p1", status: "pending", reason: null });
    expect(photoOf(opened.live, "m2")).toEqual({ id: "p1", status: "failed", reason: "timeout" });
    expect(answers).toEqual([]);
  });
});

describe("LiveConversation: photos in a call", () => {
  const pending = { id: "p1", status: "pending", reason: null } as const;

  /** A call in "c1", connected; `emit` sends the call under way events. */
  async function inCall() {
    const opened = await conversation({ messages: history });
    await connected(opened.live, opened.attempts);
    const emit = (...events: CallEvent[]) =>
      events.forEach((event) => opened.attempts.at(-1)!.handlers.onEvent(event));
    return { ...opened, emit };
  }

  /** The user's words taken up, then her reply 1 with a photo begun and recorded. */
  const withPhoto: CallEvent[] = [
    { type: "user", text: "拍一张给我看", reply: 1 },
    { type: "photo", reply: 1, id: "p1", status: "pending" },
    { type: "delta", segment: 0, lang: "ja", text: "はい" },
    { type: "done", message: { ...message(4, "character", "はい"), photo: pending }, affinity: null },
  ];
  const ready: CallEvent = { type: "photo", reply: 1, id: "p1", status: "ready", reason: null };

  const reply = (live: LiveConversation) => live.snapshot().entries.find((e) => e.key === "m4");

  it("shows the photo on the call layer and in the log as it is generated and as it comes out", async () => {
    const { live, emit } = await inCall();

    emit(...withPhoto);
    const generating = live.snapshot().callPhoto;
    emit(ready);

    expect(generating).toEqual(pending);
    expect(live.snapshot().callPhoto).toEqual({ id: "p1", status: "ready", reason: null });
    expect(reply(live)).toMatchObject({ photo: { id: "p1", status: "ready" } });
    // Out with the call layer unfolded: the user saw it come.
    expect(live.snapshot().newPhoto).toBe(false);
  });

  it("tells why one was not sent", async () => {
    const { live, emit } = await inCall();

    emit(...withPhoto, { type: "photo", reply: 1, id: "p1", status: "failed", reason: "timeout" });

    expect(live.snapshot().callPhoto).toEqual({ id: "p1", status: "failed", reason: "timeout" });
  });

  it("shows the next photo in the call in place of the last", async () => {
    const { live, emit } = await inCall();
    emit(...withPhoto, ready);

    emit(
      { type: "user", text: "再来一张", reply: 2 },
      { type: "photo", reply: 2, id: "p2", status: "pending" },
    );

    expect(live.snapshot().callPhoto).toEqual({ id: "p2", status: "pending", reason: null });
  });

  it("has the call bar tell of a photo out while the layer is folded away, until it is unfolded", async () => {
    const { live, emit } = await inCall();
    emit(...withPhoto);
    live.collapse();

    emit(ready);
    const folded = live.snapshot().newPhoto;
    live.expand();

    expect(folded).toBe(true);
    expect(live.snapshot().newPhoto).toBe(false);
  });

  it("has it seen once her conversation page is open", async () => {
    const { live, emit } = await inCall();
    emit(...withPhoto);
    live.collapse();
    emit(ready);

    live.photoSeen();

    expect(live.snapshot()).toMatchObject({ newPhoto: false, layer: { collapsed: true } });
  });

  it("tells of none not sent while the layer is folded away", async () => {
    const { live, emit } = await inCall();
    emit(...withPhoto);
    live.collapse();

    emit({ type: "photo", reply: 1, id: "p1", status: "failed", reason: "service_error" });

    expect(live.snapshot().newPhoto).toBe(false);
  });

  it("carries no photo on the end card, the one out after the call shown in the log as asked after", async () => {
    vi.useFakeTimers();
    try {
      const opened = opening({
        messages: history,
        ports: { getPhoto: async () => ({ status: "ready", reason: null }) },
      });
      await vi.advanceTimersByTimeAsync(0);
      const { live, attempts } = opened;
      const calling = live.startCall();
      attempts[0].connect(new FakeCall());
      await calling;
      withPhoto.forEach((event) => attempts[0].handlers.onEvent(event));
      const hungUp = { type: "event", id: 7, kind: "hung_up", created_at: "2026-10-13T03:00:00Z", affinity: null } as const;
      attempts[0].handlers.onEvent({ type: "ended", reason: "she_hung_up", message: "她挂断了电话。", counts: true, event: hungUp });
      attempts[0].handlers.onDropped();

      const ended = live.snapshot();
      await vi.advanceTimersByTimeAsync(2000);

      expect(ended).toMatchObject({ call: { phase: "ended" }, callPhoto: null });
      expect(reply(live)).toMatchObject({ photo: { id: "p1", status: "ready" } });
      expect(live.snapshot().newPhoto).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("has the photos used up told after her reply in the log", async () => {
    const { live, emit } = await inCall();

    emit(
      { type: "user", text: "拍一张给我看", reply: 1 },
      { type: "delta", segment: 0, lang: "ja", text: "また後でね" },
      { type: "photos-used-up", retry_at: "2026-09-27T01:00:00Z" },
      { type: "done", message: message(4, "character", "また後でね"), affinity: null },
    );

    expect(reply(live)).toMatchObject({ photo: null, photosUsedUp: "2026-09-27T01:00:00Z" });
    expect(live.snapshot().callPhoto).toBeNull();
  });
});

describe("LiveConversation: opening", () => {
  it("opens her newest conversation with its messages, leaving the list alone", async () => {
    const { live, listChanged, sentTo } = opening({
      conversations: [record("c1"), record("c0")],
      messages: history,
      turns: scripted([]),
    });
    expect(live.snapshot().conversation).toEqual({ status: "opening" });

    await settled();

    expect(live.snapshot()).toMatchObject({ conversation: { status: "open", id: "c1" }, failure: null });
    expect(live.snapshot().entries.map((e) => e.key)).toEqual(["m1", "m2"]);
    expect(listChanged()).toBe(0);
    live.setDraft("你好");
    await live.submit();
    expect(sentTo).toEqual(["c1"]);
  });

  it("starts a conversation when she has none, which the list then shows", async () => {
    const { live, created, listChanged, sentTo } = await conversation({
      conversations: [],
      turns: scripted([]),
    });

    expect(created).toEqual(["new1"]);
    expect(listChanged()).toBe(1);
    expect(live.snapshot()).toMatchObject({ conversation: { status: "open", id: "new1" }, entries: [] });
    live.setDraft("你好");
    await live.submit();
    expect(sentTo).toEqual(["new1"]);
  });

  it.each([
    ["it cannot be reached", new Error("Failed to fetch"), false],
    ["the browser is signed out", new ApiError("请先登录", 401), true],
  ])("says why it could not open when %s", async (_, error, signedOut) => {
    const { live } = await conversation({
      ports: {
        listConversations: async () => {
          throw error;
        },
      },
    });

    expect(live.snapshot()).toMatchObject({
      conversation: { status: "failed", failure: { message: error.message, signedOut } },
      // Not an error of the conversation open: that one has none.
      failure: null,
    });
  });

  it("sends nothing and dials nothing before it is open", async () => {
    const { gate: listed, open: list } = gate();
    const { live, attempts, sentTo } = opening({
      ports: {
        listConversations: async () => {
          await listed;
          return [record("c1")];
        },
      },
    });

    live.setDraft("你好");
    await live.submit();
    await live.startCall();

    expect(sentTo).toEqual([]);
    expect(attempts).toEqual([]);
    expect(live.snapshot().call).toEqual({ phase: "idle" });
    list();
  });

  it("shows nothing more once the page is left while it is opening", async () => {
    const { gate: listed, open: list } = gate();
    const { live, published } = opening({
      messages: history,
      ports: {
        listConversations: async () => {
          await listed;
          return [record("c1")];
        },
      },
    });

    live.dispose();
    const before = published();
    list();
    await settled();

    expect(published()).toBe(before);
    expect(live.snapshot().conversation).toEqual({ status: "opening" });
  });

  it("still tells the list about a conversation it started when the page is left meanwhile", async () => {
    // 对话已经在后端建好了，列表里她那一行应该跟着变；页面上不再有任何更新。
    const { gate: made, open: make } = gate();
    let creating = 0;
    const { live, published, listChanged } = opening({
      conversations: [],
      ports: {
        createConversation: async () => {
          creating++;
          await made;
          return record("new1");
        },
      },
    });
    await settled();
    expect(creating).toBe(1);

    live.dispose();
    const before = published();
    make();
    await settled();

    expect(listChanged()).toBe(1);
    expect(published()).toBe(before);
  });

  it("starts no conversation once the page is left before her conversations are listed", async () => {
    const { gate: listed, open: list } = gate();
    const { live, created, listChanged } = opening({
      ports: {
        listConversations: async () => {
          await listed;
          return [];
        },
      },
    });

    live.dispose();
    list();
    await settled();

    expect(created).toEqual([]);
    expect(listChanged()).toBe(0);
  });
});

describe("LiveConversation: a new conversation", () => {
  it("starts a new conversation with an empty log and input box, which the list then shows", async () => {
    const { live, created, listChanged, sentTo } = await conversation({
      messages: history,
      turns: scripted([]),
    });
    live.setDraft("还没发出去的话");

    await live.startNewConversation();

    expect(created).toEqual(["new1"]);
    expect(live.snapshot()).toMatchObject({
      conversation: { status: "open", id: "new1" },
      entries: [],
      draft: "",
      failure: null,
    });
    expect(listChanged()).toBe(1);
    live.setDraft("你好");
    await live.submit();
    expect(sentTo).toEqual(["new1"]);
  });

  it("is opening again while the new conversation is started, sending and dialing nothing", async () => {
    const { gate: made, open: make } = gate();
    const { live, attempts, sentTo } = await conversation({
      messages: history,
      turns: scripted([]),
      ports: {
        createConversation: async () => {
          await made;
          return record("new1");
        },
      },
    });

    const starting = live.startNewConversation();
    expect(live.snapshot().conversation).toEqual({ status: "opening" });
    live.setDraft("你好");
    await live.submit();
    await live.startCall();
    expect(sentTo).toEqual([]);
    expect(attempts).toEqual([]);

    make();
    await starting;
    expect(live.snapshot().conversation).toEqual({ status: "open", id: "new1" });
  });

  it("keeps the conversation open and says why when a new one cannot be started", async () => {
    const { live, listChanged } = await conversation({
      messages: history,
      ports: {
        createConversation: async () => {
          throw new Error("Failed to fetch");
        },
      },
    });

    live.setDraft("你好");
    await live.startNewConversation();

    expect(live.snapshot()).toMatchObject({
      conversation: { status: "open", id: "c1" },
      draft: "你好",
      failure: { message: "Failed to fetch", signedOut: false },
    });
    expect(live.snapshot().entries.map((e) => e.key)).toEqual(["m1", "m2"]);
    expect(listChanged()).toBe(0);
  });

  it("does not start one while her current conversation has no record, but once it has one", async () => {
    // spec「空的当前对话不能再开新对话」：免得堆出一串空对话。
    const { live, created } = await conversation({
      turns: scripted([
        { type: "user", text: "你好" },
        { type: "done", message: message(4, "character", "こんにちは"), affinity: null },
      ]),
    });
    const empty = live.snapshot().canStartNew;
    await live.startNewConversation();
    const refused = [...created];

    live.setDraft("你好");
    await live.submit();
    const said = live.snapshot().canStartNew;
    await live.startNewConversation();

    expect({ empty, refused, said }).toEqual({ empty: false, refused: [], said: true });
    expect(created).toEqual(["new1"]);
    expect(live.snapshot()).toMatchObject({ conversation: { status: "open", id: "new1" }, canStartNew: false });
  });

  it("does not start one over a text turn or a call", async () => {
    const { gate: answered, open: answer } = gate();
    const { live, attempts, created } = await conversation({
      turns: async function* () {
        yield { type: "user", text: "你好" };
        await answered;
        yield { type: "done", message: message(4, "character", "こんにちは"), affinity: null };
      },
    });
    live.setDraft("你好");
    const sending = live.submit();
    await live.startNewConversation();
    answer();
    await sending;

    await connected(live, attempts);
    await live.startNewConversation();

    expect(created).toEqual([]);
  });
});

describe("LiveConversation: the list", () => {
  it("is told once a text turn is recorded, not when it is sent", async () => {
    const { gate: answered, open: answer } = gate();
    const { live, listChanged } = await conversation({
      turns: async function* () {
        yield { type: "user", text: "你好" };
        yield { type: "delta", segment: 0, lang: "ja", text: "こん" };
        await answered;
        yield { type: "done", message: message(4, "character", "こんにちは"), affinity: null };
      },
    });
    live.setDraft("你好");

    const sending = live.submit();
    await settled();
    expect(listChanged()).toBe(0);
    answer();
    await sending;

    expect(listChanged()).toBe(1);
  });

  it("is told once for each turn recorded in a call", async () => {
    const { live, attempts, listChanged } = await conversation();
    await connected(live, attempts);
    const { handlers } = attempts[0];

    handlers.onEvent({ type: "user", text: "你好", reply: 1 });
    handlers.onEvent({ type: "delta", segment: 0, lang: "ja", text: "こん" });
    expect(listChanged()).toBe(0);
    handlers.onEvent({ type: "done", message: message(4, "character", "こんにちは"), affinity: null });

    expect(listChanged()).toBe(1);
  });

  it("is not told about a turn in a call that fails or is cut off", async () => {
    const { live, attempts, listChanged } = await conversation();
    await connected(live, attempts);
    const { handlers } = attempts[0];
    handlers.onEvent({ type: "user", text: "你好", reply: 1 });
    handlers.onEvent({ type: "error", message: "角色暂时无法回复" });
    handlers.onEvent({ type: "user", text: "你好", reply: 2 });
    handlers.onEvent({ type: "delta", segment: 0, lang: "ja", text: "こん" });

    await live.hangUp();

    expect(listChanged()).toBe(0);
  });
});

describe("LiveConversation: times", () => {
  it("dates the user's words when they are sent and the reply when the backend recorded it", async () => {
    const { live } = await conversation({
      messages: history,
      turns: scripted([
        { type: "user", text: "你好" },
        { type: "delta", segment: 0, lang: "ja", text: "こん" },
        { type: "done", message: message(4, "character", "こんにちは", "2026-09-28T08:49:00Z"), affinity: null },
      ]),
    });
    live.setDraft("你好");

    await live.submit();

    expect(live.snapshot().entries.map((e) => e.at)).toEqual([
      "2026-09-27T00:00:00Z",
      "2026-09-27T00:00:00Z",
      new Date(NOW).toISOString(),
      "2026-09-28T08:49:00Z",
    ]);
  });

  it("dates what the user says in a call when the backend takes it up", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);

    attempts[0].handlers.onEvent({ type: "user", text: "你好", reply: 1 });

    expect(live.snapshot().entries[0].at).toBe(new Date(NOW).toISOString());
  });
});

describe("LiveConversation: the call layer", () => {
  it("shows up and unfolds as the call is dialed", async () => {
    const { live } = await conversation();

    void live.startCall();

    expect(live.snapshot().layer).toMatchObject({ call: { phase: "dialing", dial: 1 }, collapsed: false });
  });

  it("keeps the last moment, folded away for good, when the user hangs up", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    expect(live.snapshot().layer).toMatchObject({ call: { phase: "connected" }, collapsed: false });

    await live.hangUp();

    expect(live.snapshot()).toMatchObject({
      call: { phase: "idle" },
      layer: { call: { phase: "connected", dial: 1 }, collapsed: true },
    });
    live.expand();
    expect(live.snapshot().layer?.collapsed).toBe(true);
  });

  it("keeps the end page, folded away, once it is dismissed", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    attempts[0].handlers.onDropped();
    expect(live.snapshot().layer).toMatchObject({ call: { phase: "ended" }, collapsed: false });

    live.dismissEnd();

    expect(live.snapshot()).toMatchObject({
      call: { phase: "idle" },
      layer: { call: { phase: "ended", dial: 1 }, collapsed: true },
    });
  });

  it("unfolds again when the call ends for a reason while it is collapsed", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    live.collapse();
    expect(live.snapshot().layer?.collapsed).toBe(true);

    attempts[0].handlers.onDropped();

    expect(live.snapshot()).toMatchObject({ layer: { call: { phase: "ended" }, collapsed: false } });
  });

  it("unfolds again when dialing, clearing the last failure", async () => {
    const { live, attempts } = await conversation({
      turns: async function* (): AsyncIterable<TurnEvent> {
        throw new ApiError("本小时的对话次数已用完", 429);
      },
    });
    await connected(live, attempts);
    live.collapse();
    await live.hangUp();
    live.setDraft("你好");
    await live.submit();
    expect(live.snapshot()).toMatchObject({ layer: { collapsed: true }, failure: { signedOut: false } });

    void live.startCall();

    expect(live.snapshot()).toMatchObject({ layer: { call: { phase: "dialing" }, collapsed: false }, failure: null });
  });

  it("collapses and expands on request, only while a call is under way", async () => {
    const { live, attempts } = await conversation();
    live.collapse();
    expect(live.snapshot().layer).toBeNull();

    const calling = live.startCall();
    live.collapse();
    attempts[0].connect(new FakeCall());
    await calling;
    expect(live.snapshot().layer).toMatchObject({ call: { phase: "connected" }, collapsed: true });
    live.expand();
    expect(live.snapshot().layer?.collapsed).toBe(false);
  });

  it("is kept when a new conversation is started", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    const shown = live.snapshot().layer?.call;
    await live.hangUp();

    await live.startNewConversation();

    expect(live.snapshot().layer?.call).toEqual(shown);
  });
});

describe("LiveConversation: the microphone list", () => {
  it("opens and closes while the call is connected", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);

    live.openMicPicker();
    expect(live.snapshot().pickingMic).toBe(true);
    live.closeMicPicker();
    expect(live.snapshot().pickingMic).toBe(false);
  });

  it("does not open until the call has connected", async () => {
    const { live } = await conversation();
    void live.startCall();

    live.openMicPicker();

    expect(live.snapshot().pickingMic).toBe(false);
  });

  it.each([
    ["drops", (_live: LiveConversation, attempts: Attempt[]) => attempts[0].handlers.onDropped()],
    ["is hung up", (live: LiveConversation) => live.hangUp()],
  ])("closes when the call %s", async (_, end) => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    live.openMicPicker();

    await end(live, attempts);

    expect(live.snapshot().pickingMic).toBe(false);
  });
});

// How she looks is ruled in `standDirection.test.ts`; these show the call moving it on.
describe("LiveConversation: the stand", () => {
  it("keeps her mouth shut in text only, even when switched on while she speaks", async () => {
    const { live, attempts } = await conversation();
    live.toggleTextOnly();
    await connected(live, attempts);
    const { handlers } = attempts[0];

    handlers.onEvent({ type: "bot-started-speaking" });
    expect(live.snapshot().stand.speaking).toBe(false);

    live.toggleTextOnly();
    handlers.onEvent({ type: "bot-started-speaking" });
    expect(live.snapshot().stand.speaking).toBe(true);
    live.toggleTextOnly();
    expect(live.snapshot().stand.speaking).toBe(false);
  });

  it("keeps her mouth shut from the first call when text only was on already", async () => {
    const saved: CallPreferences = { micId: "", quality: "standard", muted: true, phoneTexture: false };
    const { live, attempts } = await conversation({
      ports: { preferences: { load: () => saved, save: (change) => ({ ...saved, ...change }) } },
    });
    await connected(live, attempts);

    attempts[0].handlers.onEvent({ type: "bot-started-speaking" });

    expect(live.snapshot()).toMatchObject({ textOnly: true, stand: { speaking: false } });
  });

  it("looks at the user who interrupts by typing once her voice stops, until she speaks again", async () => {
    const { live, attempts } = await conversation();
    const call = await connected(live, attempts);
    const { handlers } = attempts[0];
    handlers.onEvent({ type: "bot-started-speaking" });

    live.setDraft("等一下");
    await live.submit();
    expect(call.sent).toEqual(["等一下"]);
    expect(live.snapshot().stand).toMatchObject({ speaking: true, looking: false });
    handlers.onEvent({ type: "bot-stopped-speaking" });

    expect(live.snapshot().stand).toMatchObject({ mood: "calm", speaking: false, looking: true });
    handlers.onEvent({ type: "bot-started-speaking" });
    expect(live.snapshot().stand).toMatchObject({ speaking: true, looking: false });
  });

  it("keeps following the call while the call layer is collapsed", async () => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    live.collapse();

    attempts[0].handlers.onEvent({ type: "bot-started-speaking" });

    expect(live.snapshot().stand.speaking).toBe(true);
  });

  it.each([
    ["is hung up", (live: LiveConversation) => live.hangUp()],
    ["drops", (_live: LiveConversation, attempts: Attempt[]) => attempts[0].handlers.onDropped()],
  ])("is cleared when the call %s, the next call starting calm", async (_, end) => {
    const { live, attempts } = await conversation();
    await connected(live, attempts);
    const { handlers } = attempts[0];
    handlers.onEvent({ type: "bot-started-speaking" });
    handlers.onEvent({ type: "user-started-speaking" });
    handlers.onEvent({ type: "user-stopped-speaking" });
    handlers.onEvent({ type: "user-started-speaking" });

    await end(live, attempts);
    expect(live.snapshot().stand).toEqual(CALM);

    await connected(live, attempts);
    expect(live.snapshot().stand).toEqual(CALM);
  });
});

describe("LiveConversation: what the list shows newer", () => {
  const reachOut: ReachOut = {
    type: "reach_out",
    id: 7,
    ja: "レッスン終わったよ",
    zh: "练习结束了哦",
    segments: [{ ja: "レッスン終わったよ", zh: "练习结束了哦" }],
    created_at: "2026-09-27T05:00:00Z",
  };

  /** The list's line for her: her conversation `id`, its last line said at `at`, a reach-out. */
  function listedAs(at: string, id = "c1"): LatestConversation {
    const last = { role: "character" as const, text: null, ja: "", zh: "", created_at: at, reach_out: true as const };
    return { character_id: "hikari", conversation_id: id, created_at: "2026-09-27T00:00:00Z", last_message: last, unseen_reach_out: true };
  }

  /** Her conversation, its record in the backend `now()` as it is then, each reading counted. */
  async function reading(options: Options = {}) {
    let record: Recorded[] = history;
    const reads: string[] = [];
    const opened = await conversation({
      ...options,
      ports: {
        listMessages: async (id) => {
          reads.push(id);
          return record;
        },
        ...options.ports,
      },
    });
    return { ...opened, reads, recordNow: (now: Recorded[]) => (record = now) };
  }

  const keys = (live: LiveConversation) => live.snapshot().entries.map((e) => e.key);

  it("reads her conversation again when the list shows something newer in it, a reach-out she just made", async () => {
    const { live, reads, recordNow } = await reading();
    recordNow([...history, reachOut]);

    live.listed(listedAs(reachOut.created_at));
    await settled();

    expect(reads).toEqual(["c1", "c1"]);
    expect(keys(live)).toEqual(["m1", "m2", "r7"]);
  });

  it("does not read it again when the list shows nothing newer, or another conversation of hers", async () => {
    const { live, reads } = await reading();

    live.listed(listedAs(history[1].created_at));
    live.listed(listedAs("2026-09-27T05:00:00Z", "c0"));
    await settled();

    expect(reads).toEqual(["c1"]);
  });

  it("puts it off while a text turn is under way, reading once the turn is over", async () => {
    const { gate: answered, open: answer } = gate();
    const reply = message(9, "character", "こんにちは", "2026-09-27T06:00:00Z");
    const { live, reads, recordNow } = await reading({
      turns: async function* () {
        yield { type: "user", text: "你好" };
        await answered;
        yield { type: "done", message: reply, affinity: null };
      },
    });
    recordNow([...history, reachOut]);
    live.setDraft("你好");
    const sending = live.submit();
    await settled();

    live.listed(listedAs(reachOut.created_at));
    await settled();
    expect(reads).toEqual(["c1"]);

    recordNow([...history, reachOut, message(8, "user", "你好", "2026-09-27T06:00:00Z"), reply]);
    answer();
    await sending;
    await settled();

    expect(reads).toEqual(["c1", "c1"]);
    expect(keys(live)).toEqual(["m1", "m2", "r7", "m8", "m9"]);
  });

  it("puts it off through a call, reading once it is over", async () => {
    const { live, attempts, reads, recordNow } = await reading();
    await connected(live, attempts);
    recordNow([...history, reachOut]);

    live.listed(listedAs(reachOut.created_at));
    await settled();
    expect(reads).toEqual(["c1"]);

    await live.hangUp();
    await settled();

    expect(reads).toEqual(["c1", "c1"]);
    expect(keys(live)).toEqual(["m1", "m2", "r7"]);
  });

  it("does not show a reading that comes back once a text turn has begun, reading again once it is over", async () => {
    const { gate: answered, open: answer } = gate();
    const { gate: read, open: comeBack } = gate();
    let record: Recorded[] = history;
    const reads: string[] = [];
    const { live } = await conversation({
      ports: {
        listMessages: async () => {
          reads.push("c1");
          const now = record;
          if (reads.length === 2) await read;
          return now;
        },
      },
      turns: async function* () {
        yield { type: "user", text: "你好" };
        await answered;
        yield { type: "error", message: "角色暂时无法回复" };
      },
    });
    record = [...history, reachOut];
    live.listed(listedAs(reachOut.created_at));
    live.setDraft("你好");
    const sending = live.submit();
    await settled();

    comeBack();
    await settled();
    expect(keys(live)).toEqual(["m1", "m2", "new-2", "new-3"]);

    answer();
    await sending;
    await settled();

    expect(reads).toEqual(["c1", "c1", "c1"]);
    expect(keys(live)).toEqual(["m1", "m2", "r7"]);
  });

  it("does not read it once the page is left, nor one put off through a call", async () => {
    const left = await reading();
    left.live.dispose();
    left.live.listed(listedAs(reachOut.created_at));
    await settled();
    expect(left.reads).toEqual(["c1"]);

    const calling = await reading();
    await connected(calling.live, calling.attempts);
    calling.live.listed(listedAs(reachOut.created_at));
    calling.live.dispose();
    await settled();
    expect(calling.reads).toEqual(["c1"]);
  });

  it("does not read one put off through a call once the page is left, the call going on in the shell", async () => {
    const { live, attempts, reads } = await reading();
    await connected(live, attempts);
    live.listed(listedAs(reachOut.created_at));

    live.offScreen();
    attempts[0].handlers.onEvent({ type: "ended", reason: "duration_limit", message: "满 30 分钟了", counts: true });
    attempts[0].handlers.onDropped();
    await settled();

    expect(live.snapshot().call.phase).toBe("ended");
    expect(reads).toEqual(["c1"]);
  });
});
