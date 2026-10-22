// The conversation page's log: what it shows of the conversation, kept up to date from
// the same turn events whether the turn is typed or spoken in a call.

import type { AffinityChange } from "@/lib/affinity";
import type { ConversationEvent, EventKind, Photo, PhotoStatus, Recorded, Segment, TurnEvent } from "@/lib/api";

/** One message or conversation event shown, or her reach-out, shown as her reply is but marked
 * `reachOut` (GLOSSARY.md「搭话」). `at` (ISO) is when it was recorded; for the turn
 * under way, when the user sent or said the words, which the reply shares until it is recorded.
 * Her reply keeps how the turn changed her affinity, once recorded, and a gift record how the
 * gift did; null where it did not. A gift record keeps the gift's name too. Her reply keeps the
 * photo she sent with it (GLOSSARY.md「照片」), null if none; and, in a turn the user asked for a
 * photo with this hour's used up, when they may ask again (ISO), which is never recorded. */
export type Entry =
  | { kind: "user"; key: string; text: string; at: string }
  | {
      kind: "event";
      key: string;
      event: EventKind;
      at: string;
      gift: string | null;
      /** The keepsake she gave, for a keepsake's (GLOSSARY.md「纪念品」); null for the others. */
      keepsakeId: number | null;
      affinity: AffinityChange | null;
    }
  | {
      kind: "character";
      key: string;
      segments: Segment[];
      pending: boolean;
      at: string;
      affinity: AffinityChange | null;
      photo: Photo | null;
      photosUsedUp?: string;
      reachOut?: true;
      /** The user said they did not mean what lowered her affinity (GLOSSARY.md「我不是这个意思」). */
      appealed?: true;
    };

/** The exchange under way: the user's words and the reply, both the last entries shown.
 * A turn the user typed is unconfirmed until the backend's `user` event takes it up. */
export type Turn = { confirmed: boolean; userKey: string; replyKey: string };

export type ConversationLog = { entries: Entry[]; turn: Turn | null };

export type LogAction =
  /** The conversation was opened or started: show its record. */
  | { type: "loaded"; messages: Recorded[] }
  /** The user typed something outside a call, at `at`; show it before the backend takes it up. */
  | { type: "submitted"; text: string; at: string }
  /** The backend took up the user's words, heard at `at`; words typed keep the time they were sent. */
  | (Extract<TurnEvent, { type: "user" }> & { at: string })
  | Exclude<TurnEvent, { type: "user" }>
  /** The turn ended without `done` or `error`: the user hung up, the call dropped, or
   * the text turn's event stream closed early. */
  | { type: "interrupted" }
  /** Something that is not a turn happened, just recorded: she hung up. */
  | { type: "event"; event: ConversationEvent }
  /** A photo being generated was asked how it is coming along. */
  | { type: "photo-status"; id: string; status: PhotoStatus }
  /** The user appealed her reply `key` (GLOSSARY.md「我不是这个意思」). */
  | { type: "appealed"; key: string };

export const emptyLog: ConversationLog = { entries: [], turn: null };

export function conversationLog(log: ConversationLog, action: LogAction): ConversationLog {
  switch (action.type) {
    case "loaded":
      return { entries: action.messages.filter((m) => !isBlankUserMessage(m)).map(toEntry), turn: null };
    case "submitted":
      return opened(withoutTurn(log), action.text, action.at, false);
    case "user":
      if (log.turn && !log.turn.confirmed) {
        return { ...log, turn: { ...log.turn, confirmed: true } };
      }
      return opened(withoutTurn(log), action.text, action.at, true);
    case "photo": {
      const turn = log.turn;
      if (!turn) return log;
      const photo = { id: action.id, status: action.status, reason: null };
      return { ...log, entries: replaced(log.entries, turn.replyKey, (e) => withPhoto(e, photo)) };
    }
    case "delta": {
      const turn = log.turn;
      if (!turn) return log;
      return { ...log, entries: replaced(log.entries, turn.replyKey, (e) => withDelta(e, action)) };
    }
    case "photos-used-up": {
      const turn = log.turn;
      if (!turn) return log;
      const usedUp = (e: Entry): Entry => (e.kind === "character" ? { ...e, photosUsedUp: action.retry_at } : e);
      return { ...log, entries: replaced(log.entries, turn.replyKey, usedUp) };
    }
    case "done": {
      const turn = log.turn;
      if (!turn) return log;
      const reply = toEntry(action.message);
      // A photo already asked about may have come out by now: it stays as it came out. The photos
      // used up stay told of, as they are not recorded.
      const entries = replaced(log.entries, turn.replyKey, (e) =>
        withUsedUp(withPhoto(reply, settledOf(e, reply)), e),
      );
      // She ended the conversation after her reply: that comes after it.
      return { entries: action.ended ? [...entries, toEntry(action.ended)] : entries, turn: null };
    }
    case "error":
    case "interrupted":
      return withoutTurn(log);
    case "event":
      return { ...log, entries: [...log.entries, toEntry(action.event)] };
    case "appealed": {
      const appealed = (e: Entry): Entry => (e.kind === "character" ? { ...e, appealed: true } : e);
      return { ...log, entries: replaced(log.entries, action.key, appealed) };
    }
    case "photo-status": {
      if (action.status.status === "pending") return log;
      const photo = { id: action.id, ...action.status };
      const entries = log.entries.map((e) =>
        e.kind === "character" && e.photo?.id === action.id && e.photo.status === "pending" ? withPhoto(e, photo) : e,
      );
      return { ...log, entries };
    }
  }
}

/** The photos in the log still being generated, to ask how they are coming along. */
export function pendingPhotos(log: ConversationLog): string[] {
  return log.entries.flatMap((e) => (e.kind === "character" && e.photo?.status === "pending" ? [e.photo.id] : []));
}

/** The segments of her reply in the turn under way, as far as they have arrived; none if no turn
 * is under way. */
export function replySoFar(log: ConversationLog): Segment[] {
  const turn = log.turn;
  const reply = turn && log.entries.find((entry) => entry.key === turn.replyKey);
  return reply?.kind === "character" ? reply.segments : [];
}

/** Opens a turn with the user's words and an empty reply to fill. The turn in progress is
 * always the last two entries, so an entry's position is never shared by two entries
 * shown together and serves as its key until the entry is replaced by the recorded one. */
function opened(log: ConversationLog, text: string, at: string, confirmed: boolean): ConversationLog {
  const position = log.entries.length;
  const turn = { confirmed, userKey: `new-${position}`, replyKey: `new-${position + 1}` };
  return {
    entries: [
      ...log.entries,
      // 她先接起电话的那一轮没有用户的话：不画用户那一侧（GLOSSARY.md「行程」）。
      ...(text ? [{ kind: "user" as const, key: turn.userKey, text, at }] : []),
      { kind: "character", key: turn.replyKey, segments: [], pending: true, at, affinity: null, photo: null },
    ],
    turn,
  };
}

/** A turn that ends without `done` left no record: take it back off the log. */
function withoutTurn(log: ConversationLog): ConversationLog {
  const turn = log.turn;
  if (!turn) return log;
  const entries = log.entries.filter((e) => e.key !== turn.userKey && e.key !== turn.replyKey);
  return { entries, turn: null };
}

function replaced(entries: Entry[], key: string, update: (entry: Entry) => Entry): Entry[] {
  return entries.map((entry) => (entry.key === key ? update(entry) : entry));
}

function withPhoto(entry: Entry, photo: Photo | null): Entry {
  return entry.kind === "character" ? { ...entry, photo } : entry;
}

/** The reply as recorded, with the photos used up as the one shown was told of, if it was. */
function withUsedUp(recorded: Entry, shown: Entry): Entry {
  return recorded.kind === "character" && shown.kind === "character" && shown.photosUsedUp
    ? { ...recorded, photosUsedUp: shown.photosUsedUp }
    : recorded;
}

/** The photo of the reply as recorded, unless the one shown is that photo, come out already. */
function settledOf(shown: Entry, recorded: Entry): Photo | null {
  const photo = recorded.kind === "character" ? recorded.photo : null;
  const before = shown.kind === "character" ? shown.photo : null;
  return before && photo && before.id === photo.id && before.status !== "pending" ? before : photo;
}

function withDelta(entry: Entry, event: TurnEvent & { type: "delta" }): Entry {
  if (entry.kind !== "character") return entry;
  const segments = [...entry.segments];
  const current = segments[event.segment] ?? { ja: "", zh: "" };
  segments[event.segment] = { ...current, [event.lang]: current[event.lang] + event.text };
  return { ...entry, segments };
}

/** 她先接起电话的那一轮，用户那条话是空的：不显示。 */
function isBlankUserMessage(message: Recorded): boolean {
  return !("type" in message) && message.role === "user" && !message.text;
}

function toEntry(message: Recorded): Entry {
  if ("type" in message && message.type === "reach_out") {
    return {
      kind: "character",
      key: `r${message.id}`,
      segments: message.segments,
      pending: false,
      at: message.created_at,
      affinity: null,
      photo: message.photo ?? null,
      reachOut: true,
    };
  }
  if ("type" in message) {
    return {
      kind: "event",
      key: `e${message.id}`,
      event: message.kind,
      at: message.created_at,
      gift: message.gift_name ?? null,
      keepsakeId: message.keepsake_id ?? null,
      affinity: message.affinity,
    };
  }
  return message.role === "user"
    ? { kind: "user", key: `m${message.id}`, text: message.text ?? "", at: message.created_at }
    : {
        kind: "character",
        key: `m${message.id}`,
        segments: message.segments,
        pending: false,
        at: message.created_at,
        affinity: message.affinity,
        photo: message.photo ?? null,
        ...(message.appealed ? { appealed: true as const } : {}),
      };
}
