"""The photos she sends with her replies (GLOSSARY.md「照片」): whether a turn could have one,
what she is told of it, and each photo from her tag to the picture kept with her reply.

A turn could have a photo where she has her photo tags (`characters`) and either it brings
the user's own image generation, or the deployer has image generation and the user's limit
on photos is not 0 (`usage`); only then is the judge
model asked whether the user asks for one, and only where it says so is she told she can
send one, in the system message that changes from turn to turn (ADR-0020), or, in the
coldest tier of her affinity, that she does not want to. In a turn that could not have one,
she is told she cannot send photos (`CANNOT_SEND`), the judge model never asked, so that she
never says she sent one that never comes.

The user's photos are limited an hour, text turns' and calls' alike. A turn only checks
there is one left (`eligibility`), taking none; with none left, she is told she cannot send
one now (`USED_UP`), and the user when they may ask again. A photo is counted as it begins
generating, before it waits its turn; with none left by then, used elsewhere meanwhile, it
is never sent (`used_up`). One not sent is given back; one that came out but is not kept,
its reply gone, is not, nor one still being generated as the server stopped, whose use
nothing tells any more. A photo on the user's own image generation counts towards nothing,
whatever their limit: its use is recorded as exempt, and a turn bringing it checks nothing.
It is generated on theirs alone, in the deployment's art style; where it fails, it was their
key that did (`own_key_failed`), told with the key masked, never then generated on the
deployer's.

A photo begins as her tag closes (`begin`), generating while she goes on speaking; its id is
the browser's to wait on. It is kept with her reply as the reply is recorded (`recorded`),
being generated, and once generated its picture is, or why there is none (`PhotoFailure`);
one generated before the reply is recorded is kept as it is. A reply that is never recorded
has its photo dropped once generated (`dropped`), as does one whose conversation, or user,
is deleted meanwhile; one whose conversation is merely no longer her current one is kept.
Nothing stops a photo once begun, not the page closing: what it costs is spent, so it is
never asked for again either. A photo still being generated when the server stopped never
will be: it is kept as having taken too long once the server starts again.

Each key to an image service generates so many photos at a time (the deployer's, as many
as it is set to), the others waiting their turn, first come first served, however many; a
key set to another number from then on generates as many apart from those it had under
way; a user's own, one at a time. None takes longer than `TIME_LIMIT` from her tag on, waiting its turn included; one
that waited all of it was never sent, and one that comes back later is left. Begun in a
turn, a photo is generated in the turn's context: its request to the image service is a
service call recorded under the turn (`activity`), however long after it the photo comes
out; one never sent is none.

Her later turns carry the tag of a photo not sent marked failed, and tell her what that
means (`NOT_RECEIVED`), so that she knows the user never saw it.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime

from eidolon.activity import (
    OWN_IMAGE_ORIGIN,
    ActivityLog,
    Recording,
    image_origin,
    recording,
    timed,
)
from eidolon.affinity import TIERS, Tier
from eidolon.characters import Character
from eidolon.config import ImageSettings
from eidolon.image_generation import TIME_LIMIT, ImageFailed, ImageGenerator
from eidolon.own_keys import masked_cause
from eidolon.photo_prompt import image_prompt, read_tag
from eidolon.store import Photo, PhotoFailure, Store
from eidolon.usage import OverLimit, UsageLimiter

log = logging.getLogger(__name__)

CAN_SEND = """\
# 照片
制作人在要你发一张照片。你可以发，也可以按你的性格和此刻的情境推掉（比如害羞、正在忙）。
要发的话，在回复的最前面、第一个 <ja> 之前写一个照片标签，一行一栏，格式如下：
<photo>
shot: selfie
framing: upper body
place: cafe, indoors, day, sunlight
outfit: white shirt, cardigan
action: peace sign, smile
scene: She holds a cup of latte up near her face, with a rainy window behind her.
note: 在咖啡店给他拍的自拍
</photo>
- shot：selfie（自拍）或 other（别人帮你拍）。
- framing：portrait、upper body、cowboy shot、full body 选一个；自拍只能选 portrait 或 upper body。
- place：2–4 个英文 tag，写地点、室内还是室外、时段或天气、光线。
- outfit：1–4 个英文 tag，写一套衣服，只写这个景别里看得见的。
- action：2–4 个英文 tag，写一个动作、一两个表情；不看镜头时加一个视线。
- scene：一句简短的英文，写画面怎么摆：你手里拿着什么、看着哪里、前景和背景各有什么。只写画面里真的有的东西：不用比喻，不写否定（不想要的就别提），左右写成画面的左右（on the left side of the picture），颜色用纯颜色词（red、pink，不用 peach、brick red 这种带着东西的颜色名）。不写发色、瞳色、身材，不写别的人。
- words：（可以不写）照片里真的会有的字，一个完整的词或短语，日语或英语都可以，越短越稳，最好四个字以内，例如招牌、蛋糕上的字、手写的卡片、白板。
- words_on：有 words 时写这段字在哪里，英文短语，例如 the shop sign、the whiteboard behind her。
- note：一句中文，写这是一张什么样的照片，只给你自己回看。
- place、outfit、action 合计不超过 12 个 tag。照片里只有你一个人；不写发色、瞳色、发型、身材，也不写画风或画师。
- 照着现在的时间和你们正在聊的事拍；他指定了地点或服装，在全年龄的范围内照办。
- 不拍裸露和半裸、内衣和情趣服装、透视、走光、洗澡，也不拍性感的姿势、表情和镜头；泳装、睡衣、躺在床上可以拍。他要这些时，你可以推掉，也可以换成全年龄的样子再拍。
- 一次回复最多一张。标签后面照常用 <ja><zh> 说话。"""
"""What she is told in a turn the user asks for a photo in, and she can send one."""

NOT_NOW = """\
# 照片
制作人在要你发一张照片，你现在不想给。用你自己的话推掉，不要写照片标签。"""
"""What she is told in a turn the user asks for a photo in, her affinity in its coldest
tier: she does not send one."""

USED_UP = """\
# 照片
制作人在要你发一张照片，但你这会儿发不了。用你自己的话推掉，不要写照片标签。"""
"""What she is told in a turn the user asks for a photo in, their photos this hour used up:
she does not send one."""

CANNOT_SEND = """\
# 照片
你发不了照片。制作人要你发照片时，用你自己的话推掉，不要写照片标签。"""
"""What she is told in every turn that could not have a photo, whether or not the user asks
for one: the judge model is not asked."""

NOT_RECEIVED = """\
# 没发出去的照片
上文里标了 status="failed" 的照片没有发出去，制作人没收到。"""
"""What she is told in a turn whose history has a photo she sent that was not sent after
all, its tag marked `status="failed"`."""

COLDEST: Tier = TIERS[0][0]


@dataclass(frozen=True)
class Eligibility:
    """What a turn that could have a photo may send: one, or, the user's photos this hour
    used up, none until `used_up_until`."""

    used_up_until: datetime | None = None


@dataclass(frozen=True)
class PhotoTurn:
    """What a turn the judge model says asks for a photo comes to: what she is told of it,
    whether a photo tag she writes is kept, and, the user's photos used up, when the user
    may ask again."""

    prompt: str
    keeps_tag: bool
    used_up_until: datetime | None = None


def photo_turn(asked: bool, tier: Tier | None, eligibility: Eligibility) -> PhotoTurn | None:
    """What a turn that could have a photo, as `eligibility` says, comes to, the user having
    `asked` for one or not, her affinity in `tier` (None where affinity is off); None if not
    asked. Used up, she cannot send one, whatever her affinity."""
    if not asked:
        return None
    if eligibility.used_up_until is not None:
        return PhotoTurn(USED_UP, keeps_tag=False, used_up_until=eligibility.used_up_until)
    if tier == COLDEST:
        return PhotoTurn(NOT_NOW, keeps_tag=False)
    return PhotoTurn(CAN_SEND, keeps_tag=True)


@dataclass
class _Generating:
    """A photo begun: whether its reply is recorded or never will be, how it came out
    once it has (its picture, or why there is none), and whether it is settled (`settled`):
    kept as it came out, or dropped."""

    recorded: bool = False
    dropped: bool = False
    outcome: tuple[bytes | None, PhotoFailure | None] | None = None
    settled: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class _Queue:
    """The photos generating on one key at most so many at a time (`room`), and how many
    are, or are waiting their turn."""

    room: asyncio.Semaphore
    photos: int = 0


class Photos:
    """Every photo she sends, from her tag to the picture kept with her reply."""

    def __init__(
        self,
        store: Store,
        generator: Callable[[], ImageGenerator | None],
        activity: ActivityLog,
        usage: UsageLimiter,
        own: Callable[[ImageSettings], ImageGenerator],
    ) -> None:
        """`generator` says, whenever asked, the deployer's image generation in effect;
        None while there is none. `own` generates on the user's own image service its
        settings name, in the deployment's art style. Their requests are recorded in
        `activity`, each photo counted in `usage`. The server starting, any photo still
        being generated when it stopped took too long."""
        self._store = store
        self._generator = generator
        self._own = own
        self._activity = activity
        self._usage = usage
        self._queues: dict[tuple[str, int], _Queue] = {}
        self._generating: dict[str, _Generating] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        if given_up := store.give_up_photos("timeout"):
            log.info("%s photos were being generated as the server stopped; not sent", given_up)

    def eligibility(
        self, character: Character, user_id: str, own: ImageSettings | None = None
    ) -> Eligibility | None:
        """What a turn of the user's with her may send, bringing their `own` image
        generation if any; None if it could have no photo: she has no photo tags, or it
        brings none and there is no image generation, or the user's limit is 0. Takes none
        of the user's photos, nor checks any on their own."""
        if not character.photographed:
            return None
        if own is not None:
            return Eligibility()
        if self._generator() is None:
            return None
        try:
            self._usage.check(user_id, "photo")
        except OverLimit as e:
            return None if e.retry_at is None else Eligibility(e.retry_at)
        return Eligibility()

    def begin(
        self, character: Character, tag: str, user_id: str, own: ImageSettings | None = None
    ) -> str:
        """Begin the photo her `tag` says, for the user, generating on their `own` image
        generation if the turn brings it, else on the deployer's, in the turn it is begun
        in; its id."""
        photo_id = uuid.uuid4().hex
        self._generating[photo_id] = _Generating()
        until = asyncio.get_running_loop().time() + TIME_LIMIT
        task = asyncio.create_task(
            self._generate(photo_id, character, tag, user_id, own, until, recording())
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return photo_id

    def recorded(self, photo_id: str) -> None:
        """Her reply is recorded with the photo, being generated: it is kept as it comes out,
        or now, if it has."""
        generating = self._generating[photo_id]
        generating.recorded = True
        if generating.outcome is not None:
            self._keep(photo_id, *generating.outcome)

    def dropped(self, photo_id: str) -> None:
        """Her reply is never to be recorded: the photo is not kept, however it comes out."""
        generating = self._generating[photo_id]
        if generating.outcome is not None:
            self._forget(photo_id)
        else:
            generating.dropped = True

    async def came_out(self, photo_id: str, user_id: str) -> Photo | None:
        """The photo, sent or not, once it has come out and is kept with her reply, for the
        user whose it is; None if it is not kept, its reply never recorded or its
        conversation deleted meanwhile, or is not theirs."""
        generating = self._generating.get(photo_id)
        if generating is not None:
            await generating.settled.wait()
        return self._store.photo(photo_id, user_id)

    async def _generate(
        self,
        photo_id: str,
        character: Character,
        tag: str,
        user_id: str,
        own: ImageSettings | None,
        until: float,
        turn: Recording | None,
    ) -> None:
        """Count the photo against the user's limit, as exempt on their `own` image
        generation, and generate it by `until` (the running loop's time), its request
        recorded under `turn`; given back if not sent."""
        outcome: tuple[bytes | None, PhotoFailure | None]
        try:
            use = self._usage.take(user_id, "photo", exempt=own is not None)
        except OverLimit:
            log.info("photo %s: the user's photos this hour were used up meanwhile", photo_id)
            outcome = None, "used_up"
        except Exception:  # never left being generated for good
            log.exception("counting photo %s failed", photo_id)
            outcome = None, "service_error"
        else:
            outcome = await self._generated(photo_id, character, tag, own, until, turn)
            if outcome[0] is None:
                use.give_back()
        generating = self._generating[photo_id]
        if generating.dropped:
            self._forget(photo_id)
        elif generating.recorded:
            self._keep(photo_id, *outcome)
        else:
            generating.outcome = outcome

    async def _generated(
        self,
        photo_id: str,
        character: Character,
        tag: str,
        own: ImageSettings | None,
        until: float,
        turn: Recording | None,
    ) -> tuple[bytes | None, PhotoFailure | None]:
        """The photo generated by `until`, on the user's `own` image generation if any,
        or why it was not: on their own, anything but taking too long is their key's
        failing."""
        try:
            generator = self._generator() if own is None else self._own(own)
            if generator is None:
                raise ImageFailed("service_error", "image generation is off")
            settings = generator.settings
            read = read_tag(tag)
            async with self._turn_on(settings.api_key, settings.concurrency, until):
                with turn.current() if turn is not None else nullcontext():
                    image = await timed(
                        self._activity,
                        image_origin(settings) if own is None else OWN_IMAGE_ORIGIN,
                        generator.generate(image_prompt(character, read, settings.style), until),
                        read.stripped,
                    )
            return image.webp, None
        except ImageFailed as e:
            if own is None:
                log.warning("photo %s was not generated: %s", photo_id, e)
                return None, e.reason
            log.warning(
                "photo %s was not generated on the user's own key: %s", photo_id,
                masked_cause(str(e), own.api_key),
            )
            return None, "timeout" if e.reason == "timeout" else "own_key_failed"
        except TimeoutError:
            log.warning("photo %s waited its turn longer than it could; never sent", photo_id)
            return None, "timeout"
        except Exception as e:  # never left being generated for good
            if own is None:
                log.exception("generating photo %s failed", photo_id)
                return None, "service_error"
            # Not with its traceback, whose messages may repeat the user's key.
            log.error(
                "generating photo %s on the user's own key failed: %s", photo_id,
                type(e).__name__,
            )
            return None, "own_key_failed"

    @asynccontextmanager
    async def _turn_on(self, key: str, at_a_time: int, until: float) -> AsyncIterator[None]:
        """Wait, first come first served, for the image service to have room for one more
        photo on `key`, `at_a_time` generating at most; raises TimeoutError if there is
        none by `until` (the running loop's time). Those on the key at another number wait
        apart."""
        queue = self._queues.setdefault(
            (key, at_a_time), _Queue(asyncio.Semaphore(at_a_time))
        )
        queue.photos += 1
        try:
            async with asyncio.timeout_at(until):
                await queue.room.acquire()
            try:
                if asyncio.get_running_loop().time() >= until:  # its turn came too late to send
                    raise TimeoutError
                yield
            finally:
                queue.room.release()
        finally:
            queue.photos -= 1
            if not queue.photos:
                del self._queues[key, at_a_time]

    def _keep(self, photo_id: str, image: bytes | None, reason: PhotoFailure | None) -> None:
        if not self._store.finish_photo(photo_id, image, reason):
            log.info("photo %s came out after its reply was deleted; not kept", photo_id)
        self._forget(photo_id)

    def _forget(self, photo_id: str) -> None:
        """The photo is settled: kept, or dropped."""
        self._generating.pop(photo_id).settled.set()

    async def aclose(self) -> None:
        """Stop generating: the server is shutting down."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for generating in self._generating.values():  # none waits on them for good
            generating.settled.set()
