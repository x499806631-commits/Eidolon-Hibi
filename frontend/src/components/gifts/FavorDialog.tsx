"use client";

import { useEffect, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Drawer, DrawerContent, DrawerDescription, DrawerFooter, DrawerHeader, DrawerTitle } from "@/components/ui/drawer";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { copy } from "@/i18n";
import type { Character } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import {
  askAddress,
  askBond,
  askMakeUp,
  askParty,
  askWake,
  canBond,
  choicesFor,
  fallenOut,
  FAVOR_KINDS,
  getFavors,
  MAX_GUESTS,
  PARTY_IDEAS,
  pickMaterials,
  willing,
  type Elder,
  type FavorKind,
  type FavorOverview,
  type Slot,
} from "@/lib/favors";
import type { GiftCharacter, GiftInfo, Owned } from "@/lib/gifts";
import { dayAhead } from "@/lib/tickets";

import { days, IdolSelect, Labeled } from "./TicketDialog";
import { TicketTile } from "./Tickets";

const t = copy.gifts.favors;

/** 换选项时选过的偶像还在名单里就留着，不在就换成名单上第一位。 */
function among(list: Character[], id: string): Character | undefined {
  return list.find((c) => c.id === id) ?? list[0];
}

/**
 * 用一张拜托券（DESIGN.md「礼物页」）：宽屏对话框、窄屏底部面板，版式同用体验券的框。最上面一排五个键选拜托什么，
 * 下面一句这件事的说明，再下面按这件事选人和细节，最后是「要用的礼物」：每件材料一个下拉框，先替你选好背包里够格的，
 * 没有够格的就说缺什么、不能拜托；最下面一句提醒拜托出去券和礼物就用掉了。「拜托她」以后转圈，她回了话就换成一句结果
 * （换称呼她没答应时说只叫这一次）和「去对话」；拜托不了的（条件不对、她没能回话）在框里说原因，券和礼物都不扣。
 */
export function FavorDialog({
  isOpen,
  characters,
  statuses,
  backpack,
  catalog,
  isWide,
  timeZone,
  onClose,
  onDone,
  onGo,
}: {
  isOpen: boolean;
  characters: Character[];
  statuses: Map<string, GiftCharacter>;
  backpack: Owned[];
  catalog: Map<string, GiftInfo>;
  isWide: boolean;
  timeZone: string;
  onClose: () => void;
  onDone: () => void;
  onGo: (href: string) => void;
}) {
  const [overview, setOverview] = useState<FavorOverview | null>(null);
  const [kind, setKind] = useState<FavorKind>("address");
  const [herId, setHerId] = useState("");
  const [otherId, setOtherId] = useState("");
  const [address, setAddress] = useState("");
  const [at, setAt] = useState("07:30");
  const [mornings, setMornings] = useState(3);
  const [elder, setElder] = useState<Elder>("producer");
  const [occasion, setOccasion] = useState<string>("ordinary");
  const [what, setWhat] = useState<string>(PARTY_IDEAS[0]);
  const [ahead, setAhead] = useState(1);
  const [guests, setGuests] = useState<string[]>([]);
  const [chosen, setChosen] = useState<(string | null)[]>([]);
  const [isAsking, setAsking] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [done, setDone] = useState<{ note: string; href: string | null } | null>(null);

  // 每次打开从头来，重新读一遍谁怎么叫你、一周内的生日。
  const [openedAt, setOpenedAt] = useState(false);
  if (isOpen !== openedAt) {
    setOpenedAt(isOpen);
    if (isOpen) {
      setOverview(null);
      setKind("address");
      setAddress("");
      setChosen([]);
      setGuests([]);
      setOccasion("ordinary");
      setWhat(PARTY_IDEAS[0]);
      setFailure(null);
      setDone(null);
    }
  }
  useEffect(() => {
    if (!isOpen) return;
    let current = true;
    getFavors().then(
      (o) => current && setOverview(o),
      (e) => current && setFailure(toFailure(e)),
    );
    return () => {
      current = false;
    };
  }, [isOpen]);

  const status = (c: Character) => statuses.get(c.id);
  const willingOnes = characters.filter((c) => willing(status(c)));
  const bondable = characters.filter((c) => canBond(status(c)));
  const fallen = characters.filter((c) => fallenOut(status(c)));
  const birthday = overview?.birthdays.find((b) => b.character_id === occasion) ?? null;
  const honoree = birthday ? characters.find((c) => c.id === birthday.character_id) : undefined;

  // 各件事拜托的是谁：叫你的、结拜的是 her；托人和好时 her 去说情、other 是闹别扭的；聚会时 her 帮忙张罗。
  const target = kind === "make_up" ? among(fallen, otherId) : undefined;
  const asked =
    kind === "bond"
      ? bondable
      : kind === "make_up"
        ? willingOnes.filter((c) => c.id !== target?.id)
        : kind === "party"
          ? willingOnes.filter((c) => c.id !== honoree?.id)
          : willingOnes;
  const her = among(asked, herId);
  const invited = [...(honoree ? [honoree.id] : []), ...guests.filter((g) => g !== her?.id && g !== honoree?.id)];

  const slots: Slot[] = overview?.materials[kind] ?? [];
  const picked = pickMaterials(slots, backpack, catalog, chosen);
  const missing = slots.find((_, i) => picked[i] === null);

  function choose(next: FavorKind) {
    setKind(next);
    setChosen([]);
    setFailure(null);
  }

  function toggleGuest(id: string) {
    setGuests((now) => (now.includes(id) ? now.filter((g) => g !== id) : [...now, id]));
  }

  const partyWhat = honoree ? t.birthdayWhat(honoree.name) : what.trim();
  const partyDay = birthday ? birthday.day : dayAhead(new Date(), ahead, timeZone);
  const ready =
    !!overview &&
    !!her &&
    !missing &&
    !isAsking &&
    (kind !== "make_up" || !!target) &&
    (kind !== "party" || (partyWhat.length > 0 && invited.length > 0 && invited.length <= MAX_GUESTS));

  async function askHer() {
    if (!ready || !her) return;
    const gifts = picked.filter((p): p is string => p !== null);
    setAsking(true);
    setFailure(null);
    try {
      let note: string;
      let href = chatOf(her);
      if (kind === "address") {
        const { accepted } = await askAddress(her.id, address.trim() || null, gifts);
        note = accepted ? t.done.address(her.name) : t.done.addressDeclined(her.name);
      } else if (kind === "wake") {
        await askWake(her.id, at, mornings, gifts);
        note = t.done.wake(her.name);
      } else if (kind === "bond") {
        await askBond(her.id, elder, gifts);
        note = t.done.bond(her.name);
      } else if (kind === "make_up" && target) {
        await askMakeUp(her.id, target.id, gifts);
        note = t.done.make_up(her.name, target.name);
      } else {
        const [, month, day] = partyDay.split("-").map(Number);
        await askParty(her.id, invited, partyWhat, honoree?.id ?? null, partyDay, gifts);
        note = t.done.party(her.name, `${month}月${day}日`);
        href = chatOf(her);
      }
      setDone({ note, href });
      onDone();
    } catch (e) {
      setFailure(toFailure(e));
    } finally {
      setAsking(false);
    }
  }

  const tie = overview?.ties.find((x) => x.character_id === her?.id);
  const wake = overview?.wakes.find((w) => w.character_id === her?.id);

  const body = (
    <div className="flex flex-col gap-4">
      {done ? (
        <div className="flex flex-col items-center gap-3 py-4 text-center">
          <TicketTile kind="favor" size="large" />
          <p className="text-body">{done.note}</p>
        </div>
      ) : overview === null && !failure ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : (
        <>
          <Labeled label={t.pick}>
            <div className="flex flex-wrap gap-2">
              {FAVOR_KINDS.map((k) => (
                <Button key={k} size="sm" variant={kind === k ? "secondary" : "outline"} aria-pressed={kind === k} onClick={() => choose(k)}>
                  {t.kinds[k].name}
                </Button>
              ))}
            </div>
            <p className="text-subtitle text-muted-foreground">{t.kinds[kind].note}</p>
          </Labeled>

          {kind === "make_up" &&
            (target ? (
              <Labeled label={t.target}>
                <IdolSelect characters={fallen} value={target.id} onChange={setOtherId} label={t.target} />
              </Labeled>
            ) : (
              <p className="text-subtitle text-muted-foreground">{t.noneFallenOut}</p>
            ))}

          {kind === "party" && overview && (
            <>
              {overview.birthdays.length > 0 && (
                <Labeled label={t.occasion}>
                  <div className="flex flex-wrap gap-2">
                    {overview.birthdays.map((b) => {
                      const of = characters.find((c) => c.id === b.character_id);
                      const [, month, day] = b.day.split("-").map(Number);
                      return of ? (
                        <Button
                          key={b.character_id}
                          size="sm"
                          variant={occasion === b.character_id ? "secondary" : "outline"}
                          aria-pressed={occasion === b.character_id}
                          onClick={() => setOccasion(b.character_id)}
                        >
                          {t.birthdayOf(of.name, month, day)}
                        </Button>
                      ) : null;
                    })}
                    <Button size="sm" variant={occasion === "ordinary" ? "secondary" : "outline"} aria-pressed={occasion === "ordinary"} onClick={() => setOccasion("ordinary")}>
                      {t.ordinary}
                    </Button>
                  </div>
                </Labeled>
              )}
              {!honoree && (
                <>
                  <Labeled label={t.what}>
                    <div className="flex flex-wrap gap-2">
                      {PARTY_IDEAS.map((idea) => (
                        <Button key={idea} size="sm" variant={what === idea ? "secondary" : "outline"} onClick={() => setWhat(idea)}>
                          {idea}
                        </Button>
                      ))}
                    </div>
                    <Input value={PARTY_IDEAS.includes(what) ? "" : what} placeholder={t.whatPlaceholder} maxLength={30} onChange={(e) => setWhat(e.target.value || PARTY_IDEAS[0])} />
                  </Labeled>
                  <Labeled label={t.when}>
                    <Select
                      items={days(timeZone).map((d) => ({ value: String(d.ahead), label: d.label }))}
                      value={String(ahead)}
                      onValueChange={(next) => setAhead(Number(next) || 1)}
                    >
                      <SelectTrigger aria-label={t.when} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {days(timeZone).map((d) => (
                          <SelectItem key={d.ahead} value={String(d.ahead)}>
                            {d.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Labeled>
                </>
              )}
            </>
          )}

          {(kind !== "make_up" || target) &&
            (her ? (
              <Labeled label={kind === "make_up" ? t.mediator : kind === "party" ? t.helper : t.who}>
                <IdolSelect
                  characters={asked}
                  value={her.id}
                  onChange={setHerId}
                  label={kind === "make_up" ? t.mediator : kind === "party" ? t.helper : t.who}
                />
              </Labeled>
            ) : (
              <p className="text-subtitle text-muted-foreground">{kind === "bond" ? t.noBond : t.nobody}</p>
            ))}

          {kind === "address" && her && (
            <Labeled label={t.address}>
              {tie?.address && <p className="text-subtitle text-muted-foreground">{t.addressNow(her.name, tie.address)}</p>}
              <Input value={address} placeholder={t.addressPlaceholder} maxLength={20} onChange={(e) => setAddress(e.target.value)} />
            </Labeled>
          )}

          {kind === "wake" && her && (
            <>
              <Labeled label={t.at}>
                <Input type="time" value={at} min="04:00" max="11:59" step={300} aria-describedby="favor-at-hint" onChange={(e) => setAt(e.target.value || "07:30")} />
                <p id="favor-at-hint" className="text-subtitle text-muted-foreground">
                  {t.atHint}
                </p>
              </Labeled>
              <Labeled label={t.days}>
                <Select
                  items={Array.from({ length: overview?.max_days_ahead ?? 7 }, (_, i) => ({ value: String(i + 1), label: t.daysLabel(i + 1) }))}
                  value={String(mornings)}
                  onValueChange={(next) => setMornings(Number(next) || 1)}
                >
                  <SelectTrigger aria-label={t.days} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: overview?.max_days_ahead ?? 7 }, (_, i) => (
                      <SelectItem key={i + 1} value={String(i + 1)}>
                        {t.daysLabel(i + 1)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Labeled>
              {wake && <p className="text-subtitle text-muted-foreground">{t.wakesNow(her.name, wake.at, wake.left)}</p>}
            </>
          )}

          {kind === "bond" && her && (
            <Labeled label={t.elder}>
              {tie?.bond ? (
                <p className="text-subtitle text-muted-foreground">{t.bonded(her.name)}</p>
              ) : (
                <ToggleGroup
                  aria-label={t.elder}
                  variant="segment"
                  spacing={0}
                  className="w-full"
                  value={[elder]}
                  onValueChange={(value: unknown[]) => {
                    const next = value[0] as Elder | undefined;
                    if (next) setElder(next);
                  }}
                >
                  <ToggleGroupItem value="producer" className="flex-1">
                    {t.elders.producer}
                  </ToggleGroupItem>
                  <ToggleGroupItem value="her" className="flex-1">
                    {t.elders.her}
                  </ToggleGroupItem>
                </ToggleGroup>
              )}
            </Labeled>
          )}

          {kind === "party" && her && (
            <Labeled label={t.guests}>
              <div className="flex flex-wrap gap-2">
                {characters
                  .filter((c) => c.id !== her.id && c.id !== honoree?.id)
                  .map((c) => {
                    const on = guests.includes(c.id);
                    return (
                      <Button
                        key={c.id}
                        size="sm"
                        variant={on ? "secondary" : "outline"}
                        aria-pressed={on}
                        disabled={!on && invited.length >= MAX_GUESTS}
                        onClick={() => toggleGuest(c.id)}
                      >
                        {c.name}
                      </Button>
                    );
                  })}
              </div>
              <p className="text-subtitle text-muted-foreground">{t.guestsHint(MAX_GUESTS)}</p>
            </Labeled>
          )}

          {slots.length > 0 && (
            <Labeled label={t.materials}>
              {missing ? (
                <p className="text-subtitle text-muted-foreground">{t.missing(slotName(missing))}</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {slots.map((slot, i) => {
                    const options = choicesFor(i, slots, picked, backpack, catalog);
                    return (
                      <Select
                        key={i}
                        items={options.map((g) => ({ value: g.id, label: g.name }))}
                        value={picked[i] ?? ""}
                        onValueChange={(next) => typeof next === "string" && setChosen(picked.map((p, j) => (j === i ? next : p)))}
                      >
                        <SelectTrigger aria-label={slotName(slot)} className="w-full">
                          <span className="text-muted-foreground">{slotName(slot)}</span>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {options.map((g) => (
                            <SelectItem key={g.id} value={g.id}>
                              {g.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    );
                  })}
                </div>
              )}
            </Labeled>
          )}
          <p className="text-meta text-muted-foreground">{t.spent}</p>
          {failure && <FailureNotice failure={failure} />}
        </>
      )}
    </div>
  );

  const actions = done ? (
    <>
      {done.href && (
        <Button
          onClick={() => {
            const href = done.href;
            onClose();
            if (href) onGo(href);
          }}
        >
          {copy.gifts.tickets.goChat}
        </Button>
      )}
      <Button variant={done.href ? "ghost" : "default"} onClick={onClose}>
        {copy.gifts.drawing.keep}
      </Button>
    </>
  ) : (
    <Button disabled={!ready} onClick={askHer}>
      {isAsking && <Spinner data-icon="inline-start" />}
      {t.confirm}
    </Button>
  );
  const onOpenChange = (open: boolean) => !open && !isAsking && onClose();

  if (isWide) {
    return (
      <Dialog open={isOpen} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t.title}</DialogTitle>
            <DialogDescription>{t.description}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-y-auto">{body}</div>
          <DialogFooter>{actions}</DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Drawer open={isOpen} onOpenChange={onOpenChange}>
      <DrawerContent>
        <DrawerHeader>
          <DrawerTitle>{t.title}</DrawerTitle>
          <DrawerDescription>{t.description}</DrawerDescription>
        </DrawerHeader>
        <div className="max-h-[60vh] overflow-y-auto px-4">{body}</div>
        <DrawerFooter>{actions}</DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}

function chatOf(her: Character) {
  return `/conversation/${encodeURIComponent(her.id)}`;
}

/** 一件材料怎么说：任意礼物、稀有礼物、吃的、花草或小物件。 */
export function slotName(slot: Slot): string {
  if (slot.rarity === "rare") return t.slot.rare;
  const names = slot.categories.map((c) => t.slot[c]);
  if (names.length === 0) return t.slot.any;
  return names.reduce((a, b) => t.slot.or(a, b));
}
