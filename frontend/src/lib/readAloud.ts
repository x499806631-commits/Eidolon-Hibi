// 朗读（GLOSSARY.md「朗读」）：点她一条消息旁的小喇叭，用她的声音念出来。同一时间只念一条，点另一条先停下这条；
// 念过的后端会留着，再点不再花钱。能念的只有她已经说完的回复和搭话（记录里 m<id>、r<id> 的那些）。

import type { Entry } from "@/lib/conversationLog";

/** 她这条消息能不能念：说完了、有字、是记录里的一条。 */
export function readableKey(entry: Entry): string | null {
  if (entry.kind !== "character" || entry.pending || entry.segments.length === 0) return null;
  return /^[mr]\d+$/.test(entry.key) ? entry.key : null;
}

/** 这条消息的声音在哪。 */
export function voiceUrl(key: string): string {
  return `/api/voice/${encodeURIComponent(key)}`;
}

type Listener = (playing: string | null, failed: string | null) => void;

let current: { key: string; audio: HTMLAudioElement } | null = null;
const listeners = new Set<Listener>();

function tell(playing: string | null, failed: string | null = null) {
  for (const listener of listeners) listener(playing, failed);
}

/** 听哪一条正在念、哪一条刚才没念成。 */
export function onReading(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 念 `key` 这一条；它正在念就停下。别的那条先停。 */
export function toggleReading(key: string): void {
  const playing = current;
  if (playing) {
    playing.audio.pause();
    current = null;
    tell(null);
    if (playing.key === key) return;
  }
  const audio = new Audio(voiceUrl(key));
  current = { key, audio };
  const done = (failed: boolean) => {
    if (current?.audio !== audio) return;
    current = null;
    tell(null, failed ? key : null);
  };
  audio.addEventListener("ended", () => done(false));
  audio.addEventListener("error", () => done(true));
  tell(key);
  audio.play().catch(() => done(true));
}
