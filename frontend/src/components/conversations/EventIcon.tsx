import { copy } from "@/i18n";
import type { EventKind } from "@/lib/api";
import { EndedIcon, GiftIcon, HangUpIcon, KeepsakeGivenIcon, MissedCallIcon } from "@/theme/icons";

/** 每种对话事件的图标：消息区的分隔线和列表的「最后一句」共用。 */
export const EVENT_ICONS: Record<EventKind, typeof HangUpIcon> = {
  hung_up: HangUpIcon,
  gift: GiftIcon,
  ended: EndedIcon,
  missed_call: MissedCallIcon,
  keepsake: KeepsakeGivenIcon,
};

/** 对话事件给用户看的那句话，两处共用；送礼记录写上礼物名（`gift`，别的事件没有）。 */
export function eventText(event: EventKind, name: string, gift: string | null): string {
  return copy.conversation.events[event](name, gift ?? "");
}
