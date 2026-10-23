// Which of her lines the call layer's line card shows in a call, and when it moves on
// (spec「通话层的台词卡按她说话的节奏换段」). The card shows one segment at a time and only
// moves forward.
//
// - A reply voiced as it started (not text only) is followed by her voice to the end: the card
//   moves on to the segment her voice starts playing, skipping any it never plays. Until her
//   voice starts on it, the card stays on the line it showed last.
// - Should her voice stop short of the end of a recorded reply and not come back within
//   `VOICE_WAIT_MS`, or not start on it at all within `VOICE_WAIT_MS` of its being recorded, the
//   card plays out the rest at her pace (`lineTime`), unless the user interrupted her: then it
//   stays where it is until her next reply is said.
// - A reply started in text only, without her voice, is played at her pace to the end: its first
//   segment as it arrives, each next one once it has arrived and the one before has had its time.
//   Once the user's words start the next turn, the card stays where it is until her next reply's
//   first segment: the user speaking alone may come to nothing, and stops nothing she says.
// - A reply dropped for a follow-on leaves no trace on the card.
// - What the user types in the call shows at the card's foot until the card moves on to a reply
//   started after it, the one to it; the user speaking takes it off, as does typing again (only
//   the last thing typed shows).

import type { Segment, SegmentPlaying } from "@/lib/api";

/** How fast she speaks, in characters of Japanese a second, punctuation included: about what
 * the characters' recorded greetings on the landing page come to. */
const CHARACTERS_PER_SECOND = 5;

/** The least time a line stays on the card when the card keeps her pace, in milliseconds. */
const SHORTEST_LINE_MS = 1500;

/** How long the card waits for her voice to come to a segment of a recorded reply before playing
 * the rest out at her pace, in milliseconds. Her voice is a while behind her text: each segment
 * is voiced once its text has been synthesized, so a reply is often recorded before she starts
 * saying it, and she pauses between two segments while the next is synthesized. A pause of more
 * than 0.35s (Pipecat's `BOT_VAD_STOP_SECS`) counts as her voice stopping, so the card waits as
 * long after she stops as after the reply is recorded: her voice coming back is followed, and the
 * card never runs ahead of it; a voice that does not come back leaves the card a little behind,
 * the lesser harm (spec user stories 3 and 4). */
const VOICE_WAIT_MS = 3000;

/** A segment of one of her replies in the call. */
type SegmentAt = Pick<SegmentPlaying, "reply" | "segment">;

/** The segment of her latest reply the card is next to move on to at her pace, and when. */
type NextLine = { segment: number; at: number };

/** A segment the card has shown, since when, with its text as far as the card shows it. */
type Shown = SegmentAt & { text: Segment; at: number };

/** Her latest reply in the call. */
type Reply = {
  number: number;
  /** Voiced as it started (not text only), and so followed by her voice to the end. */
  voiced: boolean;
  /** Its segments so far; all of them once it is recorded. */
  segments: Segment[];
  /** When it was recorded; null until it is. */
  recordedAt: number | null;
  /** The user spoke or typed over it (GLOSSARY.md「打断」): the card is not to play out what her
   * voice did not say of it. */
  interrupted: boolean;
};

/** The call layer's line card in a call: what it has shown, and what it goes by to move on. */
export type LineCard = {
  /** The segments the card has shown in this call, in order; the last is the one it shows. */
  shown: Shown[];
  reply: Reply | null;
  /** Since when her voice has not been playing; null while it plays. */
  quietSince: number | null;
  /** What the user typed last, and the number of her latest reply as they sent it (-1 for none);
   * null once a later reply shows, or the user speaks. */
  typed: { text: string; afterReply: number } | null;
};

export type LineCardEvent =
  /** A reply starts (the turn's `user` event), voiced unless text only. */
  | { type: "reply"; number: number; voiced: boolean }
  /** The latest reply's text arrived: its segments so far. */
  | { type: "text"; segments: Segment[] }
  /** The latest reply is recorded, with these segments. */
  | { type: "recorded"; segments: Segment[] }
  /** Her voice started playing this segment. */
  | ({ type: "segment-playing" } & SegmentAt)
  /** Her voice started or stopped playing. */
  | { type: "voice"; playing: boolean }
  /** The user started speaking, or sent what they typed (`typed`): her latest reply is
   * interrupted, or dropped for a follow-on, which comes to the same here. Her `done` may come
   * before the user's words are taken up, so it is told apart by this, not by the next reply. */
  | { type: "interrupted"; typed?: string }
  /** The call is over: the card keeps its last moment, and moves on no more. */
  | { type: "call-over" };

/** As a call starts: nothing shown, and her voice has not played. */
export const BLANK_CARD: LineCard = { shown: [], reply: null, quietSince: -Infinity, typed: null };

/** The card moved on by this event, at `now`. */
export function movedCard(card: LineCard, event: LineCardEvent, now: number): LineCard {
  switch (event.type) {
    case "reply": {
      // A reply not recorded by the time the next one starts was dropped for a follow-on,
      // which leaves no trace (GLOSSARY.md「补充」).
      const dropped = card.reply && card.reply.recordedAt === null ? card.reply.number : null;
      return {
        ...card,
        shown: card.shown.filter((s) => s.reply !== dropped),
        reply: { number: event.number, voiced: event.voiced, segments: [], recordedAt: null, interrupted: false },
      };
    }
    case "text":
    case "recorded": {
      const reply = card.reply;
      if (!reply) return card;
      const recordedAt = event.type === "recorded" ? now : null;
      return withText({ ...card, reply: { ...reply, segments: event.segments, recordedAt } });
    }
    case "segment-playing":
      if (event.reply !== card.reply?.number) return card;
      return shownUpTo(card, event.segment, now);
    case "voice":
      return { ...card, quietSince: event.playing ? null : (card.quietSince ?? now) };
    case "interrupted": {
      const typed = event.typed === undefined ? null : { text: event.typed, afterReply: card.reply?.number ?? -1 };
      return { ...card, typed, reply: card.reply && { ...card.reply, interrupted: true } };
    }
    case "call-over":
      return { ...card, reply: null, quietSince: card.quietSince ?? now };
  }
}

/** The card moved on at her pace and the segment it moved on to, if it keeps her pace now and it
 * is time; null if it is not to move on now. */
export function pacedCard(card: LineCard, now: number): { card: LineCard; movedTo: SegmentAt } | null {
  const next = nextPaced(card);
  if (next === null || next.at > now) return null;
  return { card: shownUpTo(card, next.segment, now), movedTo: { reply: card.reply!.number, segment: next.segment } };
}

/** When the card is next to move on at her pace; null if it does not keep her pace now. */
export function nextPacedAt(card: LineCard): number | null {
  return nextPaced(card)?.at ?? null;
}

/** The segment of her latest reply the card is next to move on to at her pace, and when; null
 * if it does not keep her pace now. */
function nextPaced(card: LineCard): NextLine | null {
  const { reply } = card;
  if (!reply) return null;
  return reply.voiced ? nextVoiced(card, reply) : nextUnvoiced(card, reply);
}

/** A reply without her voice: its first segment at once as it arrives, and each next one once it
 * has arrived and the one before has had its time. */
function nextUnvoiced(card: LineCard, reply: Reply): NextLine | null {
  const current = card.shown.at(-1);
  if (current?.reply !== reply.number) return reply.segments[0]?.ja ? { segment: 0, at: -Infinity } : null;
  const segment = current.segment + 1;
  if (!reply.segments[segment]?.ja) return null;
  return { segment, at: current.at + lineTime(current.text) };
}

/** A voiced reply moves on at her pace only once her voice falls short of it, and it is recorded
 * and not interrupted: from the segment her voice stopped on, once that has had its time and her
 * voice has not come back for a while, or from the first segment if her voice has not started on
 * the reply a while after it was recorded. */
function nextVoiced(card: LineCard, reply: Reply): NextLine | null {
  const { quietSince } = card;
  if (reply.recordedAt === null || reply.interrupted || quietSince === null) return null;
  const current = card.shown.at(-1);
  const next =
    current?.reply === reply.number
      ? {
          segment: current.segment + 1,
          at: Math.max(current.at + lineTime(current.text), quietSince + VOICE_WAIT_MS),
        }
      : { segment: 0, at: reply.recordedAt + VOICE_WAIT_MS };
  return next.segment < reply.segments.length ? next : null;
}

/** The line the card shows; null until it has shown one. */
export function lineOf(card: LineCard): Segment | null {
  return card.shown.at(-1)?.text ?? null;
}

/** What the user typed, shown at the card's foot until her reply to it is; null for nothing. */
export function typedOf(card: LineCard): string | null {
  return card.typed?.text ?? null;
}

/** The line the card showed before that; null until it has shown two. */
export function previousLineOf(card: LineCard): Segment | null {
  return card.shown.at(-2)?.text ?? null;
}

/** How long she takes to say this line, at her pace, in milliseconds: every character of its
 * Japanese but spaces counts, punctuation too. */
function lineTime(line: Segment): number {
  const characters = [...line.ja.replace(/\s/g, "")].length;
  return Math.max(SHORTEST_LINE_MS, (characters / CHARACTERS_PER_SECOND) * 1000);
}

/** The latest reply's text arrived: what the card shows of it fills in. */
function withText(card: LineCard): LineCard {
  const reply = card.reply!;
  const shown = card.shown.map((s) =>
    s.reply === reply.number && reply.segments[s.segment] ? { ...s, text: reply.segments[s.segment] } : s,
  );
  return { ...card, shown };
}

/** The card moves on to this segment of the latest reply at `now`, unless it is there or past
 * it already. */
function shownUpTo(card: LineCard, segment: number, now: number): LineCard {
  const reply = card.reply!;
  const current = card.shown.at(-1);
  if (current?.reply === reply.number && current.segment >= segment) return card;
  const text = reply.segments[segment] ?? { ja: "", zh: "" };
  // Her reply to what was typed: it is said.
  const typed = card.typed && reply.number > card.typed.afterReply ? null : card.typed;
  return { ...card, typed, shown: [...card.shown, { reply: reply.number, segment, text, at: now }] };
}
