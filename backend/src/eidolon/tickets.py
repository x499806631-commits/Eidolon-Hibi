"""Tickets (GLOSSARY.md「体验券」): what a draw may give besides gifts and reset cards, each a
thing to have one of the characters do for the producer, used on the gifts page (the diary
key on the diary page).

Each kind is here once: what it is, and whether a user can use it now (`available`), which
is also whether a draw may give it; adding a kind is adding it here and saying what using it
does (`tickets_api`). A ticket is taken as it is used, and given back should using it fail.

- `liked_gift`: one of the gifts she likes, in the backpack (`Gifts.add_liked_gift`);
- `letter`: a letter from her now (`Letters.write_now`), where letters are on;
- `diary_key`: one page of her diary opened, all of it (`Diary.unlock`), where the diary is;
- `photo`: a photo of her in an outfit from her outfit list, one described or one she
  chooses (`TripPhotos.ask_photo`), where there is image generation and photos are on;
- `date`: a day out together, from tomorrow to a week on (`TripPhotos.invite`), where her
  schedule is on;
- `favor`: one favor asked of her, gifts from the backpack given up for it too (`favors`,
  GLOSSARY.md「拜托」), always; drawn twice as often as each other kind (`WEIGHTS`), as every
  favor takes one.
"""

from collections.abc import Callable
from typing import Literal, get_args

from eidolon.config import Settings
from eidolon.features import Switches

TicketKind = Literal["liked_gift", "letter", "diary_key", "photo", "date", "favor"]
TICKET_KINDS: tuple[TicketKind, ...] = get_args(TicketKind)
WEIGHTS: dict[TicketKind, int] = {"favor": 2}
"""How many times as often as another a kind is drawn, if not once."""


def available(
    settings: Settings, features: Switches, has_images: Callable[[], bool]
) -> Callable[[str], list[TicketKind]]:
    """The kinds of ticket a user can use now, in `TICKET_KINDS`' order, each as many times
    as its weight (`WEIGHTS`), for a draw to pick one of them as likely as another."""

    def kinds(user_id: str) -> list[TicketKind]:
        on = {
            "liked_gift": True,
            "letter": settings.letters and features.on(user_id, "letters"),
            "diary_key": settings.diary and features.on(user_id, "diary"),
            "photo": has_images() and features.on(user_id, "photos"),
            "date": settings.schedules and features.on(user_id, "schedules"),
            "favor": True,
        }
        return [k for k in TICKET_KINDS if on[k] for _ in range(WEIGHTS.get(k, 1))]

    return kinds
