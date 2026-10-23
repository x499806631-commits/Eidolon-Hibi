"""Voicing the greetings (`eidolon greeting upload`, GLOSSARY.md「招呼」): each greeting the
character configuration has is voiced in her voice on the deployer's Fish Audio key, as
mp3, and put in the stands' bucket where `/api/characters` says it is; only those the
bucket lacks, her old ones deleted once the new one is up. One that cannot be voiced or
put is reported and the rest go on. No turn, no run record, no one's limit. Checked
against a fake Fish Audio and a fake bucket that keeps what it is sent."""

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from eidolon.characters import load_characters
from eidolon.config import FishAudioModel, Settings
from eidolon.greetings import FishAudioSettings, GreetingReport, greeting_path, upload_greetings
from eidolon.readings import load_readings, readings_path
from eidolon.s3 import S3Error, S3Settings
from tests.conftest import (
    KOGANE_SHEET, MANO_SHEET, Admin, CharacterConfig, ClientFactory, activity_in,
)
from tests.fakes import START, FakeFishAudio, FakeLLM
from tests.test_stand_upload import FakeBucket

ASSETS = "https://stand.example"
MANO = ("[softly] ほわっ……はじめまして、櫻木真乃です", "……初次见面，我是櫻木真乃")
KOGANE = ("うちが月岡恋鐘ばい！", "我就是月冈恋钟哦！")
MEGURU = ("はじめまして！八宮めぐるだよ！", "初次见面！我是八宫めぐる！")
STAND_FILE = "spine/idols/stand/1030010010/data.png"
READINGS = (("櫻木真乃", "sa0ku1ra1gi0ma1no0"), ("真乃", "ma1no0"), ("283", "tsu0ba1sa1"))


class Deployment:
    """The character configuration, the deployer's Fish Audio and the stands' bucket,
    which already has a stand in it."""

    def __init__(self, characters: CharacterConfig) -> None:
        self.characters = characters
        self.fish = FakeFishAudio()
        self.storage = FakeBucket()
        self.storage.objects[STAND_FILE] = (b"stand", {})
        self.refused_puts: set[str] = set()
        """Characters whose greetings the bucket refuses to take."""
        self.configure()

    def configure(
        self,
        mano: tuple[str, str] = MANO,
        kogane: tuple[str, str] | None = KOGANE,
        kogane_voice: str = "kogane-voice",
        mano_opener: tuple[str, str] | None = None,
    ) -> None:
        """Mano and Kogane with greetings (unless `kogane` is None), Hiori without one;
        Mano with a call opener if `mano_opener` is given. More can be added to `config`."""
        self.config = CharacterConfig(self.characters.directory).add(
            "mano", "櫻木真乃", MANO_SHEET, voice_id="mano-voice", greeting=mano, call_opener=mano_opener
        ).add(
            "kogane", "月岡恋鐘", KOGANE_SHEET, voice_id=kogane_voice, greeting=kogane
        ).add("hiori", "風野灯織", "sheet", voice_id="hiori-voice")

    def write_readings(self, *readings: tuple[str, str]) -> None:
        """The readings file has `readings`, each a name as written and its phonemes."""
        self.characters.write("readings.toml", "".join(
            f'[[readings]]\nwritten = "{written}"\nkana = "かな"\nphonemes = "{phonemes}"\n'
            for written, phonemes in readings
        ))

    async def upload(self, model: FishAudioModel = "s2.1-pro-free") -> GreetingReport:
        """As `eidolon greeting upload` does it, voicing with `model`."""
        characters = load_characters(self.characters.path, require_sheets=False)
        return await upload_greetings(
            characters.values(),
            S3Settings("http://storage.test", "stands", "key-id", "secret"),
            FishAudioSettings("deployer-fish-key", model),
            load_readings(readings_path(self.characters.path)),
            transport=httpx.MockTransport(self._handle),
        )

    def paths(self) -> dict[str, str | None]:
        """Where each character's greeting is, as the configuration is now."""
        characters = load_characters(self.characters.path, require_sheets=False)
        return {c.id: greeting_path(c) for c in characters.values()}

    def greetings(self) -> list[str]:
        return sorted(key for key in self.storage.objects if key.startswith("greetings/"))

    def call_openers(self) -> list[str]:
        return sorted(key for key in self.storage.objects if key.startswith("call-openers/"))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == FakeFishAudio.HOST:
            return self.fish.handle(request)
        if request.method == "PUT" and any(f"/greetings/{id}-" in request.url.path for id in self.refused_puts):
            return httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>")
        return self.storage.handle(request)


@pytest.fixture
def deployment(characters: CharacterConfig) -> Deployment:
    return Deployment(characters)


async def audio_urls(make_client: ClientFactory, settings: Settings) -> dict[str, str | None]:
    """Where `/api/characters` says each character's greeting is."""
    client = make_client(FakeLLM(), settings=replace(settings, stand_assets_url=ASSETS), user=None)
    listed = (await client.get("/api/characters")).json()
    return {c["id"]: c["greeting"] and c["greeting"]["audio_url"] for c in listed}


async def call_opener_url(make_client: ClientFactory, settings: Settings) -> str:
    """Where `/api/characters` says Mano's call opener is."""
    client = make_client(FakeLLM(), settings=replace(settings, stand_assets_url=ASSETS), user=None)
    listed = {c["id"]: c for c in (await client.get("/api/characters")).json()}
    return listed["mano"]["call_opener"]["audio_url"].removeprefix(f"{ASSETS}/")


def said(fish: FakeFishAudio) -> list[str]:
    return [request["text"] for request in fish.requests]


async def test_a_greeting_is_given_paired_tags_only_by_a_model_that_reads_them(
    deployment: Deployment,
):
    deployment.configure(mano=("<whisper>はじめまして</whisper>", MANO[1]), kogane=None)
    await deployment.upload()
    deployment.storage.objects.clear()
    await deployment.upload("drama-3")

    assert said(deployment.fish) == ["はじめまして", "<whisper>はじめまして</whisper>"]
    assert [r["model"] for r in deployment.fish.requests] == ["s2.1-pro-free", "drama-3"]


async def test_each_greeting_is_voiced_in_her_voice_and_put_where_the_character_list_says(
    deployment: Deployment, make_client: ClientFactory, settings: Settings
):
    report = await deployment.upload()

    # In her voice, delivery cues and all, on the deployer's key, as mp3.
    first, second = deployment.fish.requests
    assert first["reference_id"] == "mano-voice"
    assert first["text"].startswith("[softly]") and first["text"].endswith("ほわっ……はじめまして、櫻木真乃です")
    assert first["format"] == "mp3"
    assert (first["key"], first["model"]) == ("deployer-fish-key", "s2.1-pro-free")
    assert (second["reference_id"], second["text"]) == ("kogane-voice", KOGANE[0])
    # Where the character list says it is.
    urls = await audio_urls(make_client, settings)
    assert urls["hiori"] is None  # she has no greeting
    mano, kogane = (url.removeprefix(f"{ASSETS}/") for url in (urls["mano"], urls["kogane"]))  # pyright: ignore[reportOptionalMemberAccess]
    assert deployment.greetings() == sorted([mano, kogane])
    body, headers = deployment.storage.objects[mano]
    assert body == FakeFishAudio.audio(first["text"], "mano-voice")
    assert headers["content-type"] == "audio/mpeg"
    assert headers["cache-control"] == "public, max-age=31536000, immutable"
    assert report.voiced == ["櫻木真乃", "月岡恋鐘"]
    assert report.failed == []
    assert report.exit_code == 0


async def test_every_greeting_is_voiced_with_the_readings_as_its_pronunciation_dictionary(
    deployment: Deployment,
):
    """All of them, those her greeting does not use too; she is told to say her words as
    they are written."""
    deployment.write_readings(*READINGS)

    await deployment.upload()

    whole = [{"items": [
        {"key": written, "value": phonemes, "case_sensitive": True} for written, phonemes in READINGS
    ]}]
    first, second = deployment.fish.requests
    assert first["pronunciation_dictionary"] == whole
    assert second["pronunciation_dictionary"] == whole
    assert first["text"].endswith("はじめまして、櫻木真乃です")
    assert second["text"] == KOGANE[0]


async def test_without_readings_greetings_are_voiced_with_no_pronunciation_dictionary(
    deployment: Deployment,
):
    await deployment.upload()

    assert all("pronunciation_dictionary" not in request for request in deployment.fish.requests)


async def test_a_greeting_is_voiced_again_once_a_reading_it_uses_changes(deployment: Deployment):
    """Its address is of the readings it uses too: one whose name is read anew is at an
    address no browser has cached. One that uses none is where it always was."""
    before = deployment.paths()
    deployment.write_readings(*READINGS)
    read = deployment.paths()

    assert read["mano"] != before["mano"]
    assert read["kogane"] == before["kogane"] == "greetings/kogane-ae29b5dbfc3a.mp3"
    await deployment.upload()
    deployment.fish.requests.clear()

    deployment.write_readings(("櫻木真乃", "sa0ku1ra1gi1ma1no0"), *READINGS[1:])  # tuned by hand
    report = await deployment.upload()

    assert deployment.paths()["mano"] not in (read["mano"], before["mano"])
    assert [r["reference_id"] for r in deployment.fish.requests] == ["mano-voice"]
    assert report.voiced == ["櫻木真乃"]
    assert report.deleted == [read["mano"]]

    # Readings her greeting does not use change: nothing is voiced again.
    deployment.write_readings(("櫻木真乃", "sa0ku1ra1gi1ma1no0"), ("真乃", "ma0no1"), ("283", "tsu1ba0sa0"))
    deployment.fish.requests.clear()
    report = await deployment.upload()

    assert report.voiced == []
    assert deployment.fish.requests == []


async def test_only_the_greetings_the_bucket_lacks_are_voiced(deployment: Deployment):
    await deployment.upload()
    deployment.fish.requests.clear()
    deployment.storage.puts.clear()

    report = await deployment.upload()

    assert deployment.fish.requests == []
    assert deployment.storage.puts == []
    assert report.voiced == []
    assert report.unchanged == 2

    deployment.configure(kogane=("うちが恋鐘ばい！", KOGANE[1]))
    report = await deployment.upload()

    assert said(deployment.fish) == ["うちが恋鐘ばい！"]
    assert report.voiced == ["月岡恋鐘"]
    assert report.unchanged == 1


async def test_a_new_subtitle_alone_voices_nothing(deployment: Deployment):
    await deployment.upload()
    deployment.fish.requests.clear()
    deployment.configure(mano=(MANO[0], "初次见面"))

    await deployment.upload()

    assert deployment.fish.requests == []


async def test_her_old_greeting_is_deleted_once_the_new_one_is_up(
    deployment: Deployment, make_client: ClientFactory, settings: Settings
):
    # Another character whose identifier starts with hers: hers is not Mano's to delete.
    mano_2 = ("mano-2", "櫻木真乃（二）", "sheet")
    deployment.config.add(*mano_2, voice_id="mano-2-voice", greeting=MEGURU)
    await deployment.upload()
    old = await audio_urls(make_client, settings)

    deployment.configure(mano=("[happily] はじめまして！", MANO[1]), kogane_voice="new-kogane-voice")
    deployment.config.add(*mano_2, voice_id="mano-2-voice", greeting=MEGURU)
    report = await deployment.upload()

    new = await audio_urls(make_client, settings)
    assert deployment.greetings() == sorted(
        url.removeprefix(f"{ASSETS}/")  # pyright: ignore[reportOptionalMemberAccess]
        for url in (new["mano"], new["kogane"], old["mano-2"])
    )
    assert sorted(report.deleted) == sorted(
        url.removeprefix(f"{ASSETS}/") for url in (old["mano"], old["kogane"])  # pyright: ignore[reportOptionalMemberAccess]
    )
    assert STAND_FILE in deployment.storage.objects


async def test_an_old_greeting_left_behind_is_deleted_on_the_next_run(deployment: Deployment):
    """As when deleting it failed after the new one went up."""
    await deployment.upload()
    left_behind = "greetings/kogane-0123456789ab.mp3"
    deployment.storage.objects[left_behind] = (b"an old one", {})

    report = await deployment.upload()

    assert report.deleted == [left_behind]
    assert left_behind not in deployment.storage.objects


async def test_a_greeting_taken_out_of_the_configuration_is_deleted(deployment: Deployment):
    """As when her greeting, or she, is taken out: the bucket is public, and nothing
    points at it any more."""
    await deployment.upload()
    kogane = next(key for key in deployment.greetings() if key.startswith("greetings/kogane-"))
    deployment.configure(kogane=None)

    report = await deployment.upload()

    assert report.deleted == [kogane]
    assert [key.split("-")[0] for key in deployment.greetings()] == ["greetings/mano"]
    assert STAND_FILE in deployment.storage.objects


async def test_one_that_fails_is_reported_and_the_rest_go_on(deployment: Deployment):
    deployment.configure(kogane_voice="")  # Kogane has no voice
    deployment.config.add("meguru", "八宮めぐる", "sheet", voice_id="meguru-voice", greeting=MEGURU)
    deployment.config.add("toru", "浅倉透", "sheet", voice_id="toru-voice", greeting=("ふふっ", "呵呵"))
    deployment.fish.refuses["mano-voice"] = 402
    deployment.refused_puts.add("meguru")

    report = await deployment.upload()

    failed = dict(report.failed)
    assert sorted(failed) == ["八宮めぐる", "月岡恋鐘", "櫻木真乃"]
    assert "音色" in failed["月岡恋鐘"]
    assert "402" in failed["櫻木真乃"]
    assert "403" in failed["八宮めぐる"]
    assert report.voiced == ["浅倉透"]
    assert [key.split("-")[0] for key in deployment.greetings()] == ["greetings/toru"]
    assert report.exit_code != 0


MANO_OPENER = (
    "[shyly] あっ、もしもし……真乃です。えっと、今、お電話だいじょうぶですか？",
    "啊，喂……我是真乃。那个，现在方便接电话吗？",
)


async def test_her_call_opener_is_voiced_in_her_voice_and_put_where_the_character_list_says(
    deployment: Deployment, make_client: ClientFactory, settings: Settings
):
    """GLOSSARY.md「开场白」: as her greeting is, with the same readings, at an address apart
    from the greetings'."""
    deployment.write_readings(*READINGS)
    deployment.configure(mano_opener=MANO_OPENER)

    report = await deployment.upload()

    opener = next(r for r in deployment.fish.requests if "もしもし" in r["text"])
    assert opener["reference_id"] == "mano-voice"
    assert opener["text"].startswith("[shyly]") and opener["text"].endswith("お電話だいじょうぶですか？")
    assert opener["pronunciation_dictionary"] == deployment.fish.requests[0]["pronunciation_dictionary"]
    path = await call_opener_url(make_client, settings)
    assert deployment.call_openers() == [path]
    assert path.startswith("call-openers/mano-")
    body, headers = deployment.storage.objects[path]
    assert body == FakeFishAudio.audio(opener["text"], "mano-voice")
    assert headers["content-type"] == "audio/mpeg"
    assert len(deployment.greetings()) == 2  # hers and Kogane's, where they were
    assert sorted(report.voiced) == sorted(["櫻木真乃", "月岡恋鐘", "櫻木真乃的开场白"])
    assert report.exit_code == 0


async def test_her_call_opener_and_her_greeting_are_voiced_again_each_by_its_own_words(
    deployment: Deployment,
):
    deployment.configure(mano_opener=MANO_OPENER)
    await deployment.upload()
    greetings, (old_opener,) = deployment.greetings(), deployment.call_openers()
    deployment.fish.requests.clear()

    report = await deployment.upload()

    assert deployment.fish.requests == []
    assert report.unchanged == 3

    deployment.configure(mano_opener=("[shyly] もしもし、真乃です", MANO_OPENER[1]))
    report = await deployment.upload()

    (text,) = said(deployment.fish)
    assert text.startswith("[shyly]") and text.endswith("もしもし、真乃です")
    assert report.voiced == ["櫻木真乃的开场白"]
    assert report.deleted == [old_opener]
    assert deployment.greetings() == greetings
    (new_opener,) = deployment.call_openers()
    assert new_opener != old_opener

    # Her greeting changes: her call opener stays where it is.
    deployment.configure(mano=("はじめまして", MANO[1]), mano_opener=("[shyly] もしもし、真乃です", MANO_OPENER[1]))
    report = await deployment.upload()

    assert report.voiced == ["櫻木真乃"]
    assert deployment.call_openers() == [new_opener]


async def test_her_call_opener_moves_when_her_voice_or_a_reading_it_uses_changes(deployment: Deployment):
    deployment.configure(mano_opener=MANO_OPENER)
    await deployment.upload()
    (first,) = deployment.call_openers()

    deployment.write_readings(*READINGS)  # 真乃 is read
    await deployment.upload()
    (read,) = deployment.call_openers()

    assert read != first


async def test_a_call_opener_taken_out_of_the_configuration_is_deleted_and_her_greeting_kept(
    deployment: Deployment,
):
    deployment.configure(mano_opener=MANO_OPENER)
    await deployment.upload()
    (opener,) = deployment.call_openers()
    greetings = deployment.greetings()

    deployment.configure()
    report = await deployment.upload()

    assert report.deleted == [opener]
    assert deployment.call_openers() == []
    assert deployment.greetings() == greetings


async def test_a_call_opener_without_a_voice_is_reported_as_hers(deployment: Deployment):
    deployment.configure(mano_opener=MANO_OPENER)
    deployment.config.add(
        "meguru", "八宮めぐる", "sheet", voice_id="", call_opener=("もしもし！", "喂！")
    )

    report = await deployment.upload()

    assert [name for name, _ in report.failed] == ["八宮めぐる的开场白"]
    assert report.exit_code != 0


async def test_a_bucket_that_cannot_be_listed_is_an_error_before_anything_is_voiced(deployment: Deployment):
    deployment.storage.down = True

    with pytest.raises(S3Error, match="503"):
        await deployment.upload()

    assert deployment.fish.requests == []


async def test_voicing_the_greetings_leaves_no_run_record(
    deployment: Deployment, make_client: ClientFactory, settings: Settings
):
    """Greetings are not turns: nothing about them is recorded, and no one's limit counts
    them."""
    make_client(FakeLLM())  # the deployment's database, as the server leaves it

    await deployment.upload()

    async with activity_in(settings.database_path, lambda: START) as activity:
        assert await activity.since(START - timedelta(days=365)) == ([], [])


def test_uploading_without_the_bucket_or_fish_audio_exits_1(
    admin: Admin, deployment: Deployment, monkeypatch: pytest.MonkeyPatch
):
    for name in ("ENDPOINT", "BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
        monkeypatch.delenv(f"EIDOLON_STAND_S3_{name}", raising=False)
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "deployer-fish-key")

    code, _, err = admin.invoke("greeting", "upload", "--characters", str(deployment.characters.path))

    assert code == 1
    assert "EIDOLON_STAND_S3_ENDPOINT" in err

    for name, value in {
        "ENDPOINT": "https://storage.example", "BUCKET": "stands", "ACCESS_KEY_ID": "key-id",
        "SECRET_ACCESS_KEY": "secret",
    }.items():
        monkeypatch.setenv(f"EIDOLON_STAND_S3_{name}", value)
    monkeypatch.delenv("FISH_AUDIO_API_KEY")

    code, _, err = admin.invoke("greeting", "upload", "--characters", str(deployment.characters.path))

    assert code == 1
    assert "FISH_AUDIO_API_KEY" in err
