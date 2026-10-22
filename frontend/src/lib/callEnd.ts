// Why a call ended other than by the user hanging up, and whether it counted towards the
// hourly limit (GLOSSARY.md「限额」「接通」). The backend names its reasons in the `ended`
// message; the rest are told apart here.

import { untilText } from "@/lib/affinity";
import { ApiError, type BackendEndReason, type CallEvent } from "@/lib/api";
import { sayServices } from "@/lib/exemptingKeys";
import type { OwnService } from "@/lib/localOwnKeys";
import { copy } from "@/i18n";

/**
 * The backend's reasons (it never really connected; a voice service failed, or the connection
 * broke, after it connected; its time or turn limit; an own key failed; she hung up, for what
 * the user said in the call or a cooldown begun), and three told apart here: the user has no
 * calls left this hour, the browser would not let the call use the microphone (or there is
 * none), or she did not pick up, in the bath (GLOSSARY.md「洗澡」).
 */
export type EndReason = BackendEndReason | "over_limit" | "mic_denied" | "no_answer";

export type CallEnd = {
  reason: EndReason;
  /** What the backend said beyond the reason (when the user can call again, which key
   * failed, why calls are unavailable); null when the reason says it all. */
  detail: string | null;
  /** Whether the call counted towards the hourly limit. */
  counts: boolean;
  /** When the cooldown she hung up for ends (ISO); only for `cooldown`, and only if known. */
  cooldownUntil?: string;
};

/** The microphone could not be opened for a call. */
export class MicUnavailable extends Error {}

/** The backend's code for a call she did not pick up (GLOSSARY.md「洗澡」). */
export const NO_ANSWER = "no_answer";

/** A call that could not start. None of them count: it was refused, or it never connected. */
export function startFailedEnd(e: unknown): CallEnd {
  if (e instanceof MicUnavailable) return { reason: "mic_denied", detail: null, counts: false };
  if (!(e instanceof ApiError)) return { reason: "not_connected", detail: null, counts: false };
  // 她在洗澡，没接（GLOSSARY.md「洗澡」）。
  if (e.code === NO_ANSWER) return { reason: "no_answer", detail: null, counts: false };
  const reason = e.overLimit ? "over_limit" : "not_connected";
  return { reason, detail: e.message, counts: false };
}

/** The backend hung up and said why. Only a failing own key needs its words: they say which. */
export function backendEnd(event: Extract<CallEvent, { type: "ended" }>): CallEnd {
  return {
    reason: event.reason,
    detail: event.reason === "own_key" ? event.message : null,
    counts: event.counts,
    ...(event.cooldown_until ? { cooldownUntil: event.cooldown_until } : {}),
  };
}

/** What the end card says, and the main button beside「回到对话」: redial, go and fill in
 * own keys, go and give her a gift, or none. */
export type EndCard = {
  title: string;
  detail: string | null;
  body: string | null;
  counts: boolean;
  action: "redial" | "own_keys" | "gift" | null;
};

/** Where the end card is shown: her `name`; `now` and the producer's `timeZone`, for when a
 * cooldown ends; whether gifts are open (`giftsOpen`); and the deployment's exempting keys
 * (`exemptingKeys`, from the account), null while not known. */
export type EndCardContext = {
  name: string;
  now: Date;
  timeZone: string;
  giftsOpen: boolean;
  exemptingKeys: readonly OwnService[] | null;
};

/** The end card of a call that ended so. She hanging up, it says only that she did, and for a
 * cooldown when it ends, written as the cooldown's input bar writes it: a cooldown offers no
 * redial, there being no calling her till then, but a gift, which thaws her sooner, if gifts
 * are open. Over the limit, it names the keys a call must bring to count towards none, and
 * offers the own keys. */
export function endCard(end: CallEnd, { name, now, timeZone, giftsOpen, exemptingKeys }: EndCardContext): EndCard {
  const t = copy.call;
  const { detail, counts } = end;
  if (end.reason === "she_hung_up" || end.reason === "cooldown") {
    const cooling = end.reason === "cooldown";
    const until = cooling && end.cooldownUntil ? untilText(new Date(end.cooldownUntil), now, timeZone) : null;
    const body = until ? t.hungUpCooling(until) : null;
    return { title: t.hungUp(name), detail, body, counts, action: !cooling ? "redial" : giftsOpen ? "gift" : null };
  }
  if (end.reason === "no_answer") {
    return { title: t.noAnswer(name), detail: null, body: t.noAnswerBody, counts, action: "redial" };
  }
  if (end.reason === "over_limit") {
    const { title, body } = t.endings.over_limit;
    return { title, detail, body: exemptingKeys && sayServices(body, exemptingKeys), counts, action: "own_keys" };
  }
  const ending = t.endings[end.reason];
  return { title: ending.title, detail, body: ending.body, counts, action: end.reason === "own_key" ? "own_keys" : "redial" };
}

/** The connection broke with no word from the backend. The call had connected, so it counts
 * unless it was on the user's own keys alone. */
export function droppedEnd(exempt: boolean): CallEnd {
  return { reason: "dropped", detail: null, counts: !exempt };
}
