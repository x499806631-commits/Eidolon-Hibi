"use client";

import { useEffect, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { copy } from "@/i18n";
import { photoImageUrl, type Character } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import { getAlbum, type AlbumPhoto } from "@/lib/home";
import { cn } from "@/lib/utils";

const t = copy.home.album;

/**
 * 相册（GLOSSARY.md「制作人的家」）：她们发来的所有照片，最新的在前，三列（宽屏四列）的方格。点一张挂到墙上的相框里，
 * 挂着的那张描一圈、角上「挂着」；挂着照片时底下一个「取下照片」。每次打开重新读一次。
 */
export function AlbumDialog({
  open,
  onOpenChange,
  framed,
  characters,
  onFrame,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  framed: string | null;
  characters: Character[];
  onFrame: (photoId: string | null) => void;
}) {
  const [photos, setPhotos] = useState<AlbumPhoto[] | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);

  useEffect(() => {
    if (!open) return;
    let current = true;
    getAlbum().then(
      (album) => current && setPhotos(album),
      (e) => current && setFailure(toFailure(e)),
    );
    return () => {
      current = false;
    };
  }, [open]);

  const nameOf = (id: string) => characters.find((c) => c.id === id)?.name ?? id;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t.title}</DialogTitle>
          <DialogDescription>{t.description}</DialogDescription>
        </DialogHeader>
        {failure && <FailureNotice failure={failure} />}
        {photos === null ? (
          !failure && <p className="text-body text-muted-foreground">{t.loading}</p>
        ) : photos.length === 0 ? (
          <p className="text-body text-muted-foreground">{t.empty}</p>
        ) : (
          <ul className="grid max-h-[60vh] grid-cols-3 gap-2 overflow-y-auto sm:grid-cols-4">
            {photos.map((photo) => {
              const at = new Date(photo.sent_at);
              const isFramed = photo.id === framed;
              return (
                <li key={photo.id}>
                  <button
                    type="button"
                    aria-label={t.photoLabel(nameOf(photo.character_id), at.getMonth() + 1, at.getDate())}
                    aria-pressed={isFramed}
                    onClick={() => onFrame(photo.id)}
                    className={cn(
                      "relative block aspect-square w-full overflow-hidden rounded-xl bg-muted outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                      isFramed && "ring-3 ring-ring",
                    )}
                  >
                    <img src={photoImageUrl(photo.id)} alt="" loading="lazy" className="size-full object-cover" />
                    {isFramed && (
                      <Badge variant="secondary" className="absolute top-1.5 right-1.5">
                        {t.framed}
                      </Badge>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {framed && (
          <DialogFooter>
            <Button variant="outline" onClick={() => onFrame(null)}>
              {t.takeDown}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
