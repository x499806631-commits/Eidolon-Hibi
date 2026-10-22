// The conversation page for one character: opening her conversation (or starting a new
// one), its log, the text turn under way, the call and how the call layer is shown, her
// stand as the call moves it on (by the rules in `standDirection.ts`), and her affinity as
// each turn leaves it, and the photos she sent as they come out, kept outside React so the page
// only renders a snapshot and passes on clicks.

import type { AffinityChange, AffinityStatus } from "@/lib/affinity";
import type { AmbienceKind } from "@/lib/ambience";
import { appealFailure, messageIdOf, type Appealed } from "@/lib/appeals";
import {
  ApiError,
  type CallEvent,
  type Conversation,
  type LatestConversation,
  type Photo,
  type PhotoStatus,
  type Recorded,
  type Segment,
  type TurnEvent,
} from "@/lib/api";
import type { Call, CallHandlers, MicInUse } from "@/lib/call";
import { droppedEnd, backendEnd, NO_ANSWER, startFailedEnd, type CallEnd } from "@/lib/callEnd";
import type { CallPreferences } from "@/lib/callPreferences";
import {
  conversationLog,
  emptyLog,
  pendingPhotos,
  replySoFar,
  type ConversationLog,
  type Entry,
  type LogAction,
} from "@/lib/conversationLog";
import { toFailure, type Failure } from "@/lib/failure";
import {
  BLANK_CARD,
  lineOf,
  movedCard,
  nextPacedAt,
  pacedCard,
  previousLineOf,
  type LineCard,
  type LineCardEvent,
} from "@/lib/linePacing";
import { CALM, type StandDirection } from "@/lib/standAnimations";
import { directionOf, directStand, startingStand, type StandEvent, type StandState } from "@/lib/standDirection";

/** The longest message the backend accepts (`MAX_INPUT_LENGTH` in `engine.py`). */
const MAX_INPUT_LENGTH = 2000;

/**
 * Where the call is (spec「通话状态」):
 * - `dialing`: opening the microphone and connecting; it can only be hung up;
 * - `connected`: the character can hear and speak (GLOSSARY.md「接通」), since `since`; it
 *   may last `maxSeconds` (null for no limit);
 * - `ended`: over for a reason other than the user hanging up, shown until dismissed;
 *   a call the user hangs up goes straight back to `idle`.
 *
 * `dial` is which of this page's calls it is, from 1, going up on each `startCall` and kept
 * through dialing, connected and ended (the page uses it as the stand's key). `seconds` is
 * how long an ended call was connected, counted at that moment, or null if it never connected.
 */
export type CallState =
  | { phase: "idle" }
  | { phase: "dialing"; dial: number }
  | { phase: "connected"; dial: number; since: number; maxSeconds: number | null }
  | { phase: "ended"; dial: number; end: CallEnd; seconds: number | null };

/** How long a change of her affinity is shown, on the header's heart and the call screen's
 * status line (spec「页首标识」「变化胶囊和通话里的提示」). */
export const CUE_MS = 2600;

/** How often a photo she sent that is still being generated is asked after (GLOSSARY.md「照片」). */
export const PHOTO_POLL_MS = 2000;

/** A change of her affinity just recorded, shown for `CUE_MS`; `key` tells one from the next. */
export type AffinityCue = { change: AffinityChange; key: number };

/** A call under way: dialing or connected. Leaving the page hangs it up. */
export type LiveCall = Extract<CallState, { phase: "dialing" | "connected" }>;

export function isLive(call: CallState): call is LiveCall {
  return call.phase === "dialing" || call.phase === "connected";
}

/** How long the ringback rings on a call she does not pick up, in the bath (GLOSSARY.md「洗澡」), in ms. */
export const NO_ANSWER_RINGS = 12_000;

/** The call the call layer draws: dialing, connected, or ended for a reason (spec「通话层」). */
export type ShownCall = Extract<CallState, { phase: "dialing" | "connected" | "ended" }>;

/** The call layer: the call it draws and whether it is folded onto the conversation. */
export type Layer = { call: ShownCall; collapsed: boolean };

export type MicNotice = "fell_back" | "unavailable" | "lost";

/**
 * Whether her conversation is open, and which (`id`): `opening` while it is looked up (or
 * started) and its messages loaded, and again while a new conversation is started; `failed`
 * when it could not be opened, saying why. Nothing is sent or dialed unless it is `open`.
 */
export type OpenState =
  | { status: "opening" }
  | { status: "open"; id: string }
  | { status: "failed"; failure: Failure };

/** The conversation open, or null if none is: still opening, being left for a new one, or
 * failed to open. */
function openIdOf(state: OpenState): string | null {
  return state.status === "open" ? state.id : null;
}

export type Snapshot = {
  conversation: OpenState;
  entries: Entry[];
  call: CallState;
  /** The call layer: the call it draws and whether it is folded onto the conversation. Null
   * until this page has dialed once; after that it keeps the last call, folded away, so the
   * layer has something to draw as it slides down. Dialing and a call that ends for a reason
   * unfold it, so the user sees the call or why it ended. */
  layer: Layer | null;
  /** The list of microphones is open; only while the call is connected. */
  pickingMic: boolean;
  /** What the user is saying in the call so far. */
  hearing: string;
  /** The line the call screen's line card shows, by the rules in `linePacing.ts`: with her
   * voice, the segment it is saying; without it, each segment in turn at her pace. Null until
   * the card has shown one in this call. */
  line: Segment | null;
  /** The line the card showed before that in this call, shown faintly in text only; null
   * until it has shown two. Never a segment it skipped, nor one of a reply dropped for a
   * follow-on. */
  previousLine: Segment | null;
  /** The microphone is off in the browser (GLOSSARY.md「麦克风静音」). */
  micMuted: boolean;
  /** The microphone the call is using; empty for the system default. */
  micId: string;
  /** Something to say about the microphone: the chosen one is not there and the system
   * default is used instead, the one just chosen could not be opened, or it was unplugged
   * and no other one opens. */
  micNotice: MicNotice | null;
  /** A text turn is under way. */
  busy: boolean;
  /** What is in the input box. */
  draft: string;
  /** Show the character's replies as text only (GLOSSARY.md「只看文字」). */
  textOnly: boolean;
  /** A call can be dialed now: `startCall` would. Not while she is in a cooldown. */
  canDial: boolean;
  /** A new conversation can be started now: `startNewConversation` would. Not while the
   * one open has nothing in its record, so empty ones do not pile up. */
  canStartNew: boolean;
  /** What went wrong in the conversation open: a turn failed, a new conversation could not
   * be started. Why it could not be opened at all is in `conversation`. */
  failure: Failure | null;
  /** How her stand should look (spec「立绘指令」), by the rules in `standDirection.ts`; calm
   * outside a call. It follows the call while the call layer is collapsed too. */
  stand: StandDirection;
  /** Her affinity with the user (GLOSSARY.md「好感度」), as read when the page opened and
   * left by each turn since; null while affinity is off, or before it is read, or if it
   * could not be. */
  affinity: AffinityStatus | null;
  /** The change of her affinity the last turn made, for `CUE_MS` after it was recorded. */
  affinityCue: AffinityCue | null;
  /** She is in a cooldown with the user (GLOSSARY.md「冷却」), until `affinity.cooldown_until`:
   * no text turns, no calls, until it ends by itself. A new conversation can still be started. */
  cooling: boolean;
  /** She has ended the conversation open (GLOSSARY.md「已结束的对话」), as a turn's `done` said
   * or its record has it: it can only be read, no text turns, no calls. A new conversation can
   * still be started. */
  ended: boolean;
  /** The photo she sent last in the call under way (GLOSSARY.md「照片」), as the log has it: the
   * call layer shows it until the next. Null until she sends one in it, and once the call is over:
   * the end card carries none. */
  callPhoto: Photo | null;
  /** A photo she sent in the call under way came out while the call layer was folded away: the
   * call bar atop the shell says so, until the layer is unfolded or her conversation page opened
   * (`photoSeen`). */
  newPhoto: boolean;
};

type State = Omit<
  Snapshot,
  "entries" | "line" | "previousLine" | "stand" | "canDial" | "canStartNew" | "cooling" | "ended" | "callPhoto"
> & {
  log: ConversationLog;
  /** The photo she sent last in the call under way or the last one, by its id. */
  callPhotoId: string | null;
  /** The call screen's line card, in the call under way or the last one. */
  card: LineCard;
  /** Which of this page's calls is under way or last shown, from 0 before the first. */
  dial: number;
  /** Her stand direction, moved on by the call under way. */
  stand: StandState;
};

const IDLE: Omit<State, "log" | "card" | "stand" | "dial" | "callPhotoId"> = {
  conversation: { status: "opening" },
  call: { phase: "idle" },
  layer: null,
  pickingMic: false,
  hearing: "",
  micMuted: false,
  micId: "",
  micNotice: null,
  busy: false,
  draft: "",
  textOnly: false,
  failure: null,
  affinity: null,
  affinityCue: null,
  newPhoto: false,
};

/** What the page shows before a conversation is open. */
export const NOT_OPEN: Snapshot = {
  ...IDLE,
  callPhoto: null,
  entries: [],
  line: null,
  previousLine: null,
  stand: CALM,
  canDial: false,
  canStartNew: false,
  cooling: false,
  ended: false,
};

/** Her voice in a call (`CharacterVoice`): plain or over a phone line, silent in text only. */
export type Voice = {
  /** A call is being dialed, inside the user's click: over the phone line or not, or close to
   * the ear in an ASMR call. */
  dial: (phoneTexture: boolean, asmr?: boolean) => void;
  /** Her voice has arrived in the call under way. */
  play: (track: MediaStreamTrack) => void;
  /** She starts or stops speaking in the call under way (the ambience under her goes down). */
  speaking?: (speaking: boolean) => void;
  /** The call is over, however it ended. */
  callEnded?: () => void;
  /** The call under way has connected: `setAmbienceAuto` asks the backend to pick the ambience. */
  callConnected?: (setAmbienceAuto: (on: boolean) => void) => void;
  /** The backend picked the ambience for the call under way. */
  ambiencePicked?: (kind: AmbienceKind) => void;
  /** She took the call under way in the tub (GLOSSARY.md「洗澡」): her voice sounds as in a bathroom. */
  bath?: () => void;
  setTextOnly: (textOnly: boolean) => void;
  /** The page is left. */
  dispose: () => void;
};

/** The phone's own sounds around a call (GLOSSARY.md「铃声」「接通音」「挂断音」), told by the
 * conversation as the call is dialed, connects, ends, or the page is left. They are not her
 * voice: text only and collapsing the call layer never touch them. */
export type Tones = {
  /** The ringback starts looping: the user has dialed and the microphone is open. Not before:
   * on phones opening it would cut the ringback off for a moment (see `CallHandlers.onMicOpen`). */
  ring: () => void;
  /** How long until the ringback has rung once in full, in milliseconds; 0 once it has, or
   * if it is not ringing (it never started, or the browser would not play it). */
  ringLeft: () => number;
  /** The call connected: the ringback stops and the connect tone plays once. */
  connect: () => void;
  /** The call is over, hung up or not: the ringback stops and the hang-up tone plays once. */
  hangUp: () => void;
  /** The page is left: the ringback stops alone, no tone. */
  silence: () => void;
};

/** What the conversation needs from outside: the backend, this device's preferences, her
 * voice, the phone's own sounds, and the conversation list beside the page. */
export type Ports = {
  /** Her conversations, newest first. */
  listConversations: (characterId: string) => Promise<Conversation[]>;
  createConversation: (characterId: string) => Promise<Conversation>;
  /** A conversation's record; reading it has the user see her reach-outs in it. */
  listMessages: (conversationId: string) => Promise<Recorded[]>;
  /** The conversation list's line for her has changed: a turn was recorded, or a
   * conversation was started. Told even after the page is left. */
  onListChanged: () => void;
  /** `asmr` for an ASMR call (ASMR 模式通话). */
  startCall: (
    conversationId: string,
    preferences: CallPreferences,
    handlers: CallHandlers,
    asmr?: boolean,
  ) => Promise<Call>;
  sendTurn: (conversationId: string, text: string) => AsyncIterable<TurnEvent>;
  /** How a photo she sent is coming along. */
  getPhoto: (id: string) => Promise<PhotoStatus>;
  /** Her affinity with the user now; null while affinity is off. */
  getAffinity: (characterId: string) => Promise<AffinityStatus | null>;
  /** Says the user did not mean what her reply `messageId` was to (GLOSSARY.md「我不是这个意思」). */
  appeal: (messageId: number) => Promise<Appealed>;
  preferences: {
    load: () => CallPreferences;
    save: (change: Partial<CallPreferences>) => CallPreferences;
  };
  voice: Voice;
  tones: Tones;
  /** The time now, in milliseconds; `Date.now` unless a test stops the clock. */
  now?: () => number;
};

export class LiveConversation {
  private state: State;
  private published: Snapshot;
  private listeners = new Set<() => void>();
  private call: Call | null = null;
  /** The dialing of the call under way; null once that call is over, however it ended, or
   * the page is left (see `startCall`). */
  private attempt: object | null = null;
  private endedBy: CallEnd | null = null; // why the backend is hanging up
  /** Lets a call held for the ringback (see `startCall`) through at once, its dialing being
   * over; null while none is held. */
  private holding: (() => void) | null = null;
  private disposed = false;
  /** Moves the line card on when it is next due to at her pace; null while it is not. */
  private pacing: ReturnType<typeof setTimeout> | null = null;
  /** Stops showing the change of her affinity; null while none is shown. */
  private cueing: ReturnType<typeof setTimeout> | null = null;
  /** Ends the cooldown she is in as it is due to; null while she is in none. */
  private thawing: ReturnType<typeof setTimeout> | null = null;
  /** Asks after the photos in the log still being generated when next due to; null while it
   * is not due to, none being generated or the asking under way. */
  private polling: ReturnType<typeof setTimeout> | null = null;
  /** The list has shown something newer in the conversation open than the page has, which is
   * read again once nothing is under way (see `listed`). */
  private stale = false;
  /** The conversation open is being read again. */
  private rereading = false;

  /** Opens her newest conversation, or starts one if she has none. `characterId` says whose
   * conversation it is, for the page and the conversation list beside it. */
  constructor(
    readonly characterId: string,
    private readonly ports: Ports,
  ) {
    const textOnly = ports.preferences.load().muted;
    ports.voice.setTextOnly(textOnly);
    this.state = {
      ...IDLE,
      log: emptyLog,
      card: BLANK_CARD,
      dial: 0,
      textOnly,
      stand: startingStand(textOnly),
      callPhotoId: null,
    };
    this.published = toSnapshot(this.state);
    void this.open();
    void this.readAffinity();
  }

  /** A new object only after an update, as `useSyncExternalStore` needs. */
  snapshot = (): Snapshot => this.published;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** The page is left: hang up the call, even one still dialing, and stop her voice. A
   * text turn under way is not stopped; its reply is still recorded, just no longer shown.
   * A conversation still opening is not shown either. */
  dispose(): void {
    this.disposed = true;
    if (this.pacing !== null) clearTimeout(this.pacing);
    if (this.cueing !== null) clearTimeout(this.cueing);
    if (this.thawing !== null) clearTimeout(this.thawing);
    if (this.polling !== null) clearTimeout(this.polling);
    this.attempt = null;
    this.holding?.();
    this.call?.leave().catch(() => {});
    this.call = null;
    this.ports.tones.silence();
    this.ports.voice.dispose();
  }

  /** Leave this conversation for a new, empty one, with an empty input box. Not over a
   * text turn or a call. It is opening until the new one is started; if that fails, this
   * one stays open. */
  async startNewConversation(): Promise<void> {
    const current = this.openConversationId();
    if (current === null || !canStartNew(this.state)) return;
    this.update({ conversation: { status: "opening" }, failure: null });
    try {
      const conversation = await this.createConversation();
      this.showConversation(conversation.id, [], { call: { phase: "idle" }, draft: "" });
    } catch (e) {
      this.update({ conversation: { status: "open", id: current }, failure: toFailure(e) });
    }
  }

  /**
   * The conversation list was fetched again, her line in it `line`: if it shows the conversation
   * open ending in something newer than the page has (her reach-out, most likely), its record is
   * read again, and so seen. Not over a text turn or a call: once they are over, so as not to
   * sweep away the one under way. Not once the page is left.
   */
  listed(line: LatestConversation): void {
    const last = line.last_message;
    if (last === null || line.conversation_id !== this.openConversationId()) return;
    const shown = this.state.log.entries.at(-1);
    if (shown && Date.parse(last.created_at) <= Date.parse(shown.at)) return;
    this.stale = true;
    this.rereadIfFree();
  }

  /** Her current conversation is off the screen: her page is left, the call going on in the shell,
   * or shows one of her past conversations instead. What the list showed newer is not read once
   * nothing is under way, as the user would not see it. */
  offScreen(): void {
    this.stale = false;
  }

  setDraft(draft: string): void {
    this.update({ draft: draft.slice(0, MAX_INPUT_LENGTH) });
  }

  /** Send what is in the input box as a text turn. */
  async submit(): Promise<void> {
    const text = this.state.draft.trim();
    const conversationId = this.openConversationId();
    // One exchange at a time: not over a text turn, nor into a call still dialing; none in a
    // cooldown, nor into a conversation she has ended.
    if (!text || !conversationId || this.state.busy || this.state.call.phase === "dialing") return;
    if (isCooling(this.state) || isEnded(this.state)) return;
    this.update({ draft: "", failure: null });
    if (this.call) {
      // The call takes it up like speech; its reply arrives as call events. Typed while she
      // speaks, it interrupts her.
      this.call.sendText(text);
      this.moveCard({ type: "interrupted" }, { stand: this.directed({ type: "typed" }) });
      return;
    }
    this.apply({ type: "submitted", text, at: this.isoNow() }, { busy: true });

    let failure: Failure | null = null;
    let done = false;
    try {
      for await (const event of this.ports.sendTurn(conversationId, text)) {
        this.applyTurnEvent(event);
        if (event.type === "done") done = true;
        // A turn that failed leaves no record and does not count towards the limit.
        if (event.type === "error")
          failure = { message: event.message, signedOut: false, countsTowardLimit: false };
      }
      // Cut off: whether the backend still recorded (and counted) it cannot be told here.
      if (!done && !failure) failure = { message: "回复中断了，请再试一次", signedOut: false };
    } catch (e) {
      if (e instanceof ApiError && e.cooldownUntil !== null) {
        // Refused for a cooldown begun elsewhere: shown as one, the text kept for when it ends.
        this.apply({ type: "interrupted" }, { draft: text, busy: false });
        this.cooled(e.cooldownUntil);
        return;
      }
      failure = toFailure(e);
      // Refused by the backend (over the limit, own key failed, ...): not counted.
      if (e instanceof ApiError) failure.countsTowardLimit = false;
    }
    if (failure) {
      // A failed turn is not recorded: take it back off the log and return the text to the input.
      this.apply({ type: "interrupted" }, { draft: text, failure });
    }
    this.update({ busy: false });
  }

  /**
   * Call the character, from idle or from the end of the last call. Not over a text turn.
   * Call it inside the user's click, before anything is awaited: her voice is told here,
   * and only then may it start its audio context (browsers keep one started outside a
   * user gesture suspended).
   *
   * This dialing is the call under way until it ends, however it ends: hung up, dropped
   * (with or without the backend saying why), failed to connect, or the page left. Once it
   * is not, the call's messages are too late and change nothing: not the log, the failure,
   * why the call ended, what is being heard, the microphone, her stand, her voice, the
   * ringback, or the list. That rule is kept here alone, in the gate every handler given to
   * the call goes through; the handlers behind it do not ask again.
   */
  async startCall(asmr = false): Promise<void> {
    const conversationId = this.openConversationId();
    if (!conversationId || !isFree(this.state) || isCooling(this.state) || isEnded(this.state)) return;
    // Read once: the phone texture her voice is told is the one the call is given.
    const preferences = this.ports.preferences.load();
    this.ports.voice.dial(preferences.phoneTexture, asmr);
    const attempt = {};
    this.attempt = attempt;
    this.endedBy = null;
    const dial = this.state.dial + 1;
    this.update({
      call: { phase: "dialing", dial },
      dial,
      micMuted: false,
      failure: null,
      callPhotoId: null,
      newPhoto: false,
    });
    const current = () => this.attempt === attempt;
    let held = false;
    // The gate: what the call sends once it is over changes nothing.
    const gated =
      <A extends unknown[]>(handler: (...args: A) => void) =>
      (...args: A): void => {
        if (current()) handler(...args);
      };
    let started: Call;
    try {
      started = await this.ports.startCall(conversationId, preferences, {
        onMicOpen: gated(() => this.ports.tones.ring()),
        onEvent: gated((event) => this.onCallEvent(event)),
        onHearing: gated((hearing) => this.update({ hearing })),
        onCharacterAudio: gated((track) => this.ports.voice.play(track)),
        onDropped: gated(() => {
          // Dropped while dialing: startCall fails, and that says why. Not once it has
          // connected and is held for the ringback: nothing else would end it.
          if (this.state.call.phase === "dialing" && !held) return;
          this.ended(this.endedBy ?? droppedEnd(started.exempt));
        }),
        onMicLost: gated((now) => {
          // Lost while dialing: the call says which microphone it has once it connects.
          if (this.state.call.phase === "dialing") return;
          this.update(now ? micInUse(now) : { micId: "", micNotice: "lost" });
        }),
      }, asmr);
    } catch (e) {
      if (!current()) return;
      if (e instanceof ApiError && e.code === NO_ANSWER) {
        // 她在洗澡没接（GLOSSARY.md「洗澡」）：铃声照样响一阵，没人接才结束；这期间挂断就只是挂断。
        await new Promise<void>((through) => setTimeout(through, NO_ANSWER_RINGS));
        if (!current()) return;
      }
      if (e instanceof ApiError && e.cooldownUntil !== null) {
        // Refused for a cooldown begun elsewhere: no end to show, the page shows the cooldown.
        this.ended(null);
        this.cooled(e.cooldownUntil);
      } else {
        this.ended(startFailedEnd(e));
      }
      return;
    }
    const left = current() ? this.ports.tones.ringLeft() : 0;
    if (left > 0) {
      // Connected before the ringback has rung once in full (GLOSSARY.md「铃声」): to the
      // user it is still dialing, and she does not hear them, until it has.
      held = true;
      started.setMicEnabled(false);
      await new Promise<void>((through) => {
        const timer = setTimeout(through, left);
        this.holding = () => {
          clearTimeout(timer);
          through();
        };
      });
      this.holding = null;
      if (current()) started.setMicEnabled(true);
    }
    if (!current()) {
      // Hung up, or the page left, while dialing: nothing else will hang this one up.
      await (this.disposed ? started.leave() : started.hangUp()).catch(() => {});
      return;
    }
    this.call = started;
    this.ports.voice.callConnected?.((on) => started.setAmbienceAuto?.(on));
    // 铃声响完、她听得到了：按行程她在睡觉时，由她先接起电话（GLOSSARY.md「行程」）。
    started.answered?.();
    this.ports.tones.connect();
    this.update({
      call: {
        phase: "connected",
        dial: this.state.dial,
        since: this.now(),
        // The backend counts from when it connected, the ringback's last few seconds too.
        maxSeconds: started.maxSeconds === null ? null : Math.floor(started.maxSeconds - left / 1000),
      },
      card: BLANK_CARD,
      ...micInUse(started.mic),
    });
  }

  /** The user hangs up, dialing or connected: the call screen just closes. */
  async hangUp(): Promise<void> {
    if (!isLive(this.state.call)) return;
    const ending = this.call;
    this.ended(null);
    await ending?.hangUp().catch(() => {});
  }

  /** Leave the end of the last call. */
  dismissEnd(): void {
    if (this.state.call.phase === "ended") this.update({ call: { phase: "idle" } });
  }

  /** Collapse the call layer onto the conversation, while the call is under way. */
  collapse(): void {
    const layer = this.state.layer;
    if (isLive(this.state.call) && layer && !layer.collapsed) this.update({ layer: { ...layer, collapsed: true } });
  }

  /** Unfold the call layer, while the call is under way: once it is over it cannot open again.
   * A new photo is seen there. */
  expand(): void {
    const layer = this.state.layer;
    if (isLive(this.state.call) && layer?.collapsed) {
      this.update({ layer: { ...layer, collapsed: false }, newPhoto: false });
    }
  }

  /** The user did not mean what lowered her affinity in her reply `key` (GLOSSARY.md「我不是这个意思」):
   * what it took off is given back, and she is told as she replies next. Resolves to why not, or null. */
  async appeal(key: string): Promise<string | null> {
    const entry = this.state.log.entries.find((e) => e.key === key);
    const messageId = entry ? messageIdOf(entry) : null;
    if (messageId === null) return appealFailure(null);
    try {
      const { affinity } = await this.ports.appeal(messageId);
      this.update({ log: conversationLog(this.state.log, { type: "appealed", key }) });
      this.showAffinity(affinity);
      return null;
    } catch (error) {
      return appealFailure(error);
    }
  }

  /** Her conversation page is open: a photo that came out while the call layer was folded away
   * is in the log there, no longer new. */
  photoSeen(): void {
    if (this.state.newPhoto) this.update({ newPhoto: false });
  }

  /** Open the list of microphones to switch to; only while the call is connected. */
  openMicPicker(): void {
    if (this.state.call.phase === "connected" && !this.state.pickingMic) this.update({ pickingMic: true });
  }

  closeMicPicker(): void {
    if (this.state.pickingMic) this.update({ pickingMic: false });
  }

  /** Mic mute: the microphone's track only, in the browser; typing still goes to the call. */
  toggleMic(): void {
    if (!this.call) return;
    const micMuted = !this.state.micMuted;
    this.call.setMicEnabled(!micMuted);
    this.update({ micMuted });
  }

  /** Switch the call to another microphone; it becomes the default for later calls too.
   * If it cannot be opened, the call keeps the one in use and the default is not changed. */
  async switchMic(micId: string): Promise<void> {
    const call = this.call;
    if (!call) return;
    let mic: MicInUse;
    try {
      mic = await call.switchMic(micId);
    } catch {
      if (this.call === call) this.update({ micNotice: "unavailable" });
      return;
    }
    this.ports.preferences.save({ micId });
    if (this.call === call) this.update(micInUse(mic));
  }

  /** Text only: kept in the preferences, and told to her voice and the call under way. */
  toggleTextOnly(): void {
    const textOnly = !this.state.textOnly;
    this.ports.preferences.save({ muted: textOnly });
    this.ports.voice.setTextOnly(textOnly);
    this.call?.setTextOnly(textOnly);
    this.update({ textOnly, stand: this.directed({ type: "text-only", textOnly }) });
  }

  /** An event of the call under way: one that is over never gets here (see `startCall`). */
  private onCallEvent(event: CallEvent): void {
    const stand = this.directed(event);
    switch (event.type) {
      case "ended":
        this.endedBy = backendEnd(event);
        if (event.event) {
          this.apply({ type: "event", event: event.event });
          this.ports.onListChanged();
        }
        return;
      case "user":
        return this.applyTurnEvent(event, { hearing: "", stand }, () => ({
          type: "reply",
          number: event.reply,
          voiced: !this.state.textOnly,
        }));
      case "error":
        return this.applyTurnEvent(event, { failure: { message: event.message, signedOut: false }, stand });
      case "delta":
        return this.applyTurnEvent(event, { stand }, (log) => ({ type: "text", segments: replySoFar(log) }));
      case "done":
        return this.applyTurnEvent(event, { stand }, () => ({ type: "recorded", segments: event.message.segments }));
      case "photo":
        if (event.status === "pending") {
          const { reply: _, ...photo } = event;
          return this.applyTurnEvent(photo, { stand, callPhotoId: event.id });
        }
        return this.photoCameOut(event.id, { status: event.status, reason: event.reason }, { stand });
      case "photos-used-up":
        return this.applyTurnEvent(event, { stand });
      case "segment-playing":
        return this.moveCard(event, { stand });
      case "bot-started-speaking":
      case "bot-stopped-speaking":
        this.ports.voice.speaking?.(event.type === "bot-started-speaking");
        return this.moveCard({ type: "voice", playing: event.type === "bot-started-speaking" }, { stand });
      case "user-started-speaking":
        return this.moveCard({ type: "interrupted" }, { stand });
      case "ambience":
        this.ports.voice.ambiencePicked?.(event.kind);
        return;
      case "bath":
        this.ports.voice.bath?.();
        return;
      default:
        this.update({ stand });
    }
  }

  /** The call screen's line card moved on by this event, along with any other change. */
  private moveCard(event: LineCardEvent, change: Partial<State> = {}): void {
    this.showCard(movedCard(this.state.card, event, this.now()), change);
  }

  /** Shows this line card, along with any other change, moved on at her pace if the card keeps
   * it and is due to now, and has the card move on again when it is next due to. Her stand
   * follows a segment the card moved on to at her pace. */
  private showCard(card: LineCard, change: Partial<State> = {}): void {
    const paced = pacedCard(card, this.now());
    const stand = change.stand ?? this.state.stand;
    const next: Partial<State> = paced
      ? { card: paced.card, stand: directStand(stand, { type: "line-shown", ...paced.movedTo }) }
      : { card };
    if (next.card !== this.state.card || Object.keys(change).length > 0) this.update({ ...change, ...next });
    this.pace();
  }

  /** Has the line card move on when it is next due to at her pace, if it keeps her pace. */
  private pace(): void {
    if (this.pacing !== null) clearTimeout(this.pacing);
    this.pacing = null;
    const at = nextPacedAt(this.state.card);
    if (at === null || this.disposed) return;
    this.pacing = setTimeout(() => {
      this.pacing = null;
      this.showCard(this.state.card);
    }, at - this.now());
  }

  /** Her stand, moved on by this event (see `standDirection.ts` for how she looks). */
  private directed(event: StandEvent): StandState {
    return directStand(this.state.stand, event);
  }

  /** Moves the log on by an event of a turn, typed or spoken in a call, along with any other
   * change; in a call the line card moves on too, by what `card` makes of the event given the
   * log moved on. */
  private applyTurnEvent(
    event: TurnEvent,
    change: Partial<State> = {},
    card?: (log: ConversationLog) => LineCardEvent,
  ): void {
    const log = conversationLog(this.state.log, event.type === "user" ? { ...event, at: this.isoNow() } : event);
    if (card) this.moveCard(card(log), { ...change, log });
    else this.update({ ...change, log });
    // Recorded: the list's line for her now ends with it, even if the page has been left.
    if (event.type === "done") {
      this.ports.onListChanged();
      this.affinityLeft(event.affinity, event.message.affinity);
    }
  }

  /** Her affinity as a recorded turn left it, and how the turn changed it, shown for
   * `CUE_MS`; a change while affinity is off is not shown. */
  private affinityLeft(affinity: AffinityStatus | null, change: AffinityChange | null): void {
    if (!affinity || !change) {
      this.showAffinity(affinity);
      return;
    }
    if (this.cueing !== null) clearTimeout(this.cueing);
    const key = (this.state.affinityCue?.key ?? 0) + 1;
    this.showAffinity(affinity, { affinityCue: { change, key } });
    this.cueing = setTimeout(() => {
      this.cueing = null;
      this.update({ affinityCue: null });
    }, CUE_MS);
  }

  /** Reads her affinity as the page opens; if it cannot be read, there is none to show. */
  private async readAffinity(): Promise<void> {
    let affinity: AffinityStatus | null = null;
    try {
      affinity = await this.ports.getAffinity(this.characterId);
    } catch {
      // Shown as if affinity were off: no badge, no capsules.
    }
    this.showAffinity(affinity);
  }

  /** The backend refused a turn or call for a cooldown it says ends `until`. */
  private cooled(until: string): void {
    this.showAffinity({ tier: "cold", cooldown_until: until });
  }

  /** Shows her affinity, along with any other change; a cooldown ends by itself as it is due
   * to, leaving her cold. */
  private showAffinity(affinity: AffinityStatus | null, change: Partial<State> = {}): void {
    if (this.thawing !== null) clearTimeout(this.thawing);
    this.thawing = null;
    this.update({ ...change, affinity });
    const until = affinity?.cooldown_until;
    if (until == null) return;
    this.thawing = setTimeout(
      () => {
        this.thawing = null;
        this.update({ affinity: { tier: "cold", cooldown_until: null } });
      },
      Math.max(0, Date.parse(until) - this.now()),
    );
  }

  /** Opens her newest conversation, or starts one if she has none. */
  private async open(): Promise<void> {
    let conversation: Conversation;
    let messages: Recorded[];
    try {
      const [newest] = await this.ports.listConversations(this.characterId);
      // The page left: no conversation is started for it.
      if (!newest && this.disposed) return;
      conversation = newest ?? (await this.createConversation());
      messages = await this.ports.listMessages(conversation.id);
    } catch (e) {
      this.update({ conversation: { status: "failed", failure: toFailure(e) } });
      return;
    }
    if (!this.disposed) this.showConversation(conversation.id, messages);
  }

  /** Starts a conversation with her. The list shows it at once, even if the page has been
   * left meanwhile: it is there in the backend all the same. */
  private async createConversation(): Promise<Conversation> {
    const conversation = await this.ports.createConversation(this.characterId);
    this.ports.onListChanged();
    return conversation;
  }

  /** Shows this conversation and its messages from now on: it is open. */
  private showConversation(conversationId: string, messages: Recorded[], change: Partial<State> = {}): void {
    this.apply(
      { type: "loaded", messages },
      { ...change, conversation: { status: "open", id: conversationId }, card: BLANK_CARD },
    );
  }

  /** Reads the conversation open again, if the list showed something newer in it and nothing is
   * under way. A reading that comes back once a turn or a call has begun is not shown: it is read
   * again once they are over. One that fails is left to the list's next fetch. */
  private rereadIfFree(): void {
    const id = this.openConversationId();
    if (!this.stale || this.rereading || this.disposed || id === null || !isFree(this.state)) return;
    this.stale = false;
    this.rereading = true;
    this.ports.listMessages(id).then(
      (messages) => {
        this.rereading = false;
        if (this.openConversationId() !== id) return;
        if (isFree(this.state)) this.apply({ type: "loaded", messages });
        else this.stale = true;
      },
      () => (this.rereading = false),
    );
  }

  /** The conversation to send to and call in, or null if none is open. */
  private openConversationId(): string | null {
    return openIdOf(this.state.conversation);
  }

  /** The time now, as the log dates entries. */
  private isoNow(): string {
    return new Date(this.now()).toISOString();
  }

  /** The call is over, hung up (no `end`) or not; a reply it cut off is not recorded. It is
   * no longer the call under way, so whatever it sends from now on is too late (see
   * `startCall`). */
  private ended(end: CallEnd | null): void {
    // How long it was connected, counted now, before the call state is cleared.
    const connected = this.state.call.phase === "connected" ? this.state.call.since : null;
    const seconds = connected === null ? null : Math.max(0, Math.floor((this.now() - connected) / 1000));
    this.attempt = null;
    this.holding?.();
    this.call = null;
    this.ports.tones.hangUp();
    this.ports.voice.callEnded?.();
    this.moveCard(
      { type: "call-over" },
      {
        log: conversationLog(this.state.log, { type: "interrupted" }),
        call: end ? { phase: "ended", dial: this.state.dial, end, seconds } : { phase: "idle" },
        hearing: "",
        micMuted: false,
        micId: "",
        micNotice: null,
        newPhoto: false,
        stand: this.directed({ type: "call-over" }),
      },
    );
  }

  private now(): number {
    return (this.ports.now ?? Date.now)();
  }

  /** Moves the log on by one action, along with any other change. */
  private apply(action: LogAction, change: Partial<State> = {}): void {
    this.update({ ...change, log: conversationLog(this.state.log, action) });
  }

  private update(change: Partial<State>): void {
    if (this.disposed) return;
    this.state = withCallScreenRules(this.state, { ...this.state, ...change });
    this.published = toSnapshot(this.state);
    for (const listener of this.listeners) listener();
    if (change.log) this.watchPhotos();
    this.rereadIfFree();
  }

  /** Has the photos in the log still being generated asked after every `PHOTO_POLL_MS`, until
   * none is: one sent in this turn, or found being generated as the conversation opens. */
  private watchPhotos(): void {
    if (this.polling !== null || this.disposed || pendingPhotos(this.state.log).length === 0) return;
    this.polling = setTimeout(() => void this.askAfterPhotos(), PHOTO_POLL_MS);
  }

  /** Asks after each photo still being generated, showing those that came out; one that cannot
   * be asked after now is asked again next time. */
  private async askAfterPhotos(): Promise<void> {
    const asked = await Promise.all(
      pendingPhotos(this.state.log).map(async (id) => {
        try {
          return { id, status: await this.ports.getPhoto(id) };
        } catch {
          return null;
        }
      }),
    );
    this.polling = null;
    for (const photo of asked) if (photo) this.photoCameOut(photo.id, photo.status);
    this.watchPhotos();
  }

  /** Shows how a photo still being generated is coming along, told by the call or asked after,
   * along with any other change. The call's, out while the call layer is folded away, is new. */
  private photoCameOut(id: string, status: PhotoStatus, change: Partial<State> = {}): void {
    const layer = this.state.layer;
    const isNew =
      status.status === "ready" &&
      id === this.state.callPhotoId &&
      isLive(this.state.call) &&
      layer?.collapsed === true &&
      pendingPhotos(this.state.log).includes(id);
    this.apply({ type: "photo-status", id, status }, isNew ? { ...change, newPhoto: true } : change);
  }
}

/**
 * How the call screen follows the call, from one state to the next: the layer draws the call
 * under way, and dialing, and a call ending for a reason, open it up again, so the user sees
 * the call or why it ended. Back at idle it keeps that last call, folded away, until the next
 * call. The microphone list is only for a connected call.
 */
function withCallScreenRules(before: State, next: State): State {
  const layer = nextLayer(before, next);
  const pickingMic = next.pickingMic && next.call.phase === "connected";
  if (layer === next.layer && pickingMic === next.pickingMic) return next;
  return { ...next, layer, pickingMic };
}

/** The layer for the next state: the call under way, or the last one kept folded away. */
function nextLayer(before: State, next: State): Layer | null {
  const layer = next.layer;
  if (next.call.phase === "idle") {
    // Over and dismissed, or hung up: still draw the last moment, folded away.
    if (!layer || layer.collapsed) return layer;
    return { call: layer.call, collapsed: true };
  }
  // The call is the same one: keep the layer, as the user folded or unfolded it.
  if (next.call === before.call) return layer;
  // Dialing and a call that ended for a reason unfold it; a call that connects leaves it be.
  const call = next.call;
  return { call, collapsed: call.phase === "connected" ? (layer?.collapsed ?? false) : false };
}

/** Her conversation is open with nothing under way in it, no text turn and no call: a
 * call can be dialed, or a new conversation started (`canDial`, `canStartNew`). */
function isFree(state: State): boolean {
  return state.conversation.status === "open" && !state.busy && !isLive(state.call);
}

/** A new conversation can be started (`canStartNew`): nothing is under way, and the one
 * open has something in its record (spec「空的当前对话不能再开新对话」). */
function canStartNew(state: State): boolean {
  return isFree(state) && state.log.entries.length > 0;
}

/** She is in a cooldown with the user: no turns, no calls. */
function isCooling(state: State): boolean {
  return state.affinity !== null && state.affinity.cooldown_until !== null;
}

/** She has ended the conversation open: its log has her ending it. No turns, no calls. */
function isEnded(state: State): boolean {
  return state.log.entries.some((entry) => entry.kind === "event" && entry.event === "ended");
}

function micInUse(mic: MicInUse): Pick<State, "micId" | "micNotice"> {
  return { micId: mic.id, micNotice: mic.fellBack ? "fell_back" : null };
}

function toSnapshot(state: State): Snapshot {
  const { log, card, dial, stand, callPhotoId, ...rest } = state;
  const free = isFree(state);
  return {
    ...rest,
    entries: log.entries,
    line: lineOf(card),
    previousLine: previousLineOf(card),
    stand: directionOf(stand),
    canDial: free && !isCooling(state) && !isEnded(state),
    canStartNew: canStartNew(state),
    cooling: isCooling(state),
    ended: isEnded(state),
    callPhoto: isLive(state.call) ? photoOf(log, callPhotoId) : null,
  };
}

/** The photo by this id in the log, as it has it; null if none. */
function photoOf(log: ConversationLog, id: string | null): Photo | null {
  if (id === null) return null;
  for (const entry of log.entries) if (entry.kind === "character" && entry.photo?.id === id) return entry.photo;
  return null;
}
