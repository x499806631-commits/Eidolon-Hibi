"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { listMessages, sendTurn, type Photo, type Recorded, type Segment } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";

/** 约会那段对话里的一句：制作人说的，或她说的（她先开口的那句、合照那句是搭话，也算她说的）。 */
export type TalkLine =
  | { key: string; mine: true; text: string }
  | { key: string; mine: false; segments: Segment[]; pending: boolean; photo: Photo | null };

/** 一段记录变成画面上的几句：对话事件不显示。 */
export function linesOf(record: Recorded[]): TalkLine[] {
  const lines: TalkLine[] = [];
  for (const entry of record) {
    if ("role" in entry) {
      if (entry.role === "user") lines.push({ key: `m${entry.id}`, mine: true, text: entry.text ?? "" });
      else
        lines.push({ key: `m${entry.id}`, mine: false, segments: entry.segments, pending: false, photo: entry.photo ?? null });
    } else if (entry.type === "reach_out") {
      lines.push({ key: `r${entry.id}`, mine: false, segments: entry.segments, pending: false, photo: entry.photo ?? null });
    }
  }
  return lines;
}

/** 照片还在画时多久再看一次（毫秒）。 */
const PHOTO_POLL_MS = 3000;

/**
 * 约会那段对话（GLOSSARY.md「约会」）：读出来、发一句、等她回完。约会存成一段自己的对话，
 * 不经过对话页的 LiveConversation；说的每一句照样是那段对话的回合。
 */
export function useOutingTalk(conversationId: string | null, userId: string) {
  const [lines, setLines] = useState<TalkLine[]>([]);
  const [isBusy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const alive = useRef(true);

  const reload = useCallback(async () => {
    if (!conversationId) return;
    try {
      const record = await listMessages(conversationId);
      if (alive.current) setLines(linesOf(record));
    } catch (e) {
      if (alive.current) setFailure(toFailure(e));
    }
  }, [conversationId]);

  useEffect(() => {
    alive.current = true;
    void reload();
    return () => {
      alive.current = false;
    };
  }, [reload]);

  // 合照还在画：过一会儿再读一次。
  const drawing = lines.some((l) => !l.mine && l.photo?.status === "pending");
  useEffect(() => {
    if (!drawing) return;
    const timer = setTimeout(() => void reload(), PHOTO_POLL_MS);
    return () => clearTimeout(timer);
  }, [drawing, lines, reload]);

  const send = useCallback(
    async (text: string) => {
      if (!conversationId || !text.trim() || isBusy) return;
      setBusy(true);
      setFailure(null);
      const at = Date.now();
      setLines((before) => [
        ...before,
        { key: `mine-${at}`, mine: true, text },
        { key: `hers-${at}`, mine: false, segments: [], pending: true, photo: null },
      ]);
      let done = false;
      try {
        for await (const event of sendTurn(conversationId, text, userId)) {
          if (event.type === "done") {
            done = true;
            const reply = event.message;
            setLines((before) =>
              before.map((l) =>
                l.key === `hers-${at}`
                  ? { key: `m${reply.id}`, mine: false, segments: reply.segments, pending: false, photo: reply.photo ?? null }
                  : l,
              ),
            );
          } else if (event.type === "error") {
            setFailure({ message: event.message, signedOut: false });
          }
        }
      } catch (e) {
        setFailure(toFailure(e));
      }
      if (!done) setLines((before) => before.filter((l) => l.key !== `hers-${at}` && l.key !== `mine-${at}`));
      setBusy(false);
    },
    [conversationId, isBusy, userId],
  );

  return { lines, isBusy, failure, send, reload };
}
