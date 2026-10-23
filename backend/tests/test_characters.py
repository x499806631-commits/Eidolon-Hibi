"""Characters come from configuration: the list, the avatars, the image colours, call
backgrounds, stands and greetings, and each one's character sheet."""

import json
import logging
import re
from dataclasses import replace
from pathlib import Path

import pytest

from eidolon.characters import CharacterConfigError, Fact, load_characters, parse_facts
from eidolon.config import Settings, stand_assets_url_from_env
from tests.conftest import (
    AVATAR, BACKGROUND, KOGANE_SHEET, MANO_SHEET, SCSP_AVATAR, CharacterConfig, ClientFactory,
)
from tests.fakes import FakeLLM
from tests.test_text_turn import send_turn, start_conversation

REPO_CHARACTERS = Path(__file__).parents[2] / "characters"


async def test_character_list_shows_name_intro_avatar_colour_and_background_but_not_the_sheet(
    make_client: ClientFactory,
):
    """It is shown before signing in."""
    client = make_client(FakeLLM(), user=None)

    res = await client.get("/api/characters")

    assert res.status_code == 200
    assert res.json() == [
        {
            "id": "mano",
            "name": "櫻木真乃",
            "intro": "illumination STARS 的中心",
            "avatar_url": "/api/characters/mano/avatar",
            "scsp_avatar_url": "/api/characters/mano/scsp-avatar",
            "color": "#ffbad6",
            "background_url": "/api/characters/mano/background",
            "stand": None,  # no stand assets URL is configured
            "idol_id": 1,
            "name_zh": None,  # none configured
            "name_kana": None,
            "name_romaji": None,
            "unit": None,
            "unit_reading": None,  # no world setting
            "unit_order": None,
            "greeting": None,  # none configured
            "call_opener": None,
            "call_poster_url": None,  # no stand assets URL is configured
            "voxels_url": None,
        },
        {
            "id": "kogane",
            "name": "月岡恋鐘",
            "intro": "L'Antica 的偶像",
            "avatar_url": None,
            "scsp_avatar_url": None,
            "color": "#f84cad",
            "background_url": None,
            "stand": None,
            "idol_id": None,
            "name_zh": None,  # none configured
            "name_kana": None,
            "name_romaji": None,
            "unit": None,
            "unit_reading": None,
            "unit_order": None,
            "greeting": None,
            "call_opener": None,
            "call_poster_url": None,
            "voxels_url": None,
        },
    ]
    assert "ほわっ" not in res.text


async def test_character_list_is_in_idol_number_order_and_those_without_one_follow_as_configured(
    make_client: ClientFactory, characters: CharacterConfig
):
    """Mano is idol 1 and Kogane has no idol number (the fixture)."""
    characters.add("meguru", "八宮めぐる", "sheet", idol_id=3)
    characters.add("someone", "誰か", "sheet")
    characters.add("hiori", "風野灯織", "sheet", idol_id=2)
    client = make_client(FakeLLM(), user=None)

    listed = (await client.get("/api/characters")).json()

    assert [(c["id"], c["idol_id"]) for c in listed] == [
        ("mano", 1), ("hiori", 2), ("meguru", 3), ("kogane", None), ("someone", None),
    ]


async def test_character_list_gives_the_other_names_she_is_searched_by(
    make_client: ClientFactory, characters: CharacterConfig
):
    characters.add("meguru", "八宮めぐる", "sheet", other_names=("八宫巡", "はちみや めぐる", "Hachimiya Meguru"))
    client = make_client(FakeLLM(), user=None)

    listed = {c["id"]: c for c in (await client.get("/api/characters")).json()}

    assert [listed["meguru"][f] for f in ("name_zh", "name_kana", "name_romaji")] == [
        "八宫巡", "はちみや めぐる", "Hachimiya Meguru",
    ]


async def test_avatar_is_served_without_a_session_so_img_tags_can_load_it(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(), user=None)

    res = await client.get("/api/characters/mano/avatar")

    assert res.status_code == 200
    assert res.content == AVATAR
    assert (await client.get("/api/characters/kogane/avatar")).status_code == 404
    assert (await client.get("/api/characters/nobody/avatar")).status_code == 404


async def test_scsp_avatar_is_served_without_a_session_like_the_avatar(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(), user=None)

    res = await client.get("/api/characters/mano/scsp-avatar")

    assert res.status_code == 200
    assert res.content == SCSP_AVATAR
    assert (await client.get("/api/characters/kogane/scsp-avatar")).status_code == 404
    assert (await client.get("/api/characters/nobody/scsp-avatar")).status_code == 404


async def test_call_background_is_served_without_a_session_like_the_avatar(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(), user=None)

    res = await client.get("/api/characters/mano/background")

    assert res.status_code == 200
    assert res.content == BACKGROUND
    assert (await client.get("/api/characters/kogane/background")).status_code == 404
    assert (await client.get("/api/characters/nobody/background")).status_code == 404


@pytest.mark.parametrize("picture", ["avatar", "scsp-avatar", "background"])
async def test_avatar_and_background_are_cached_by_the_browser_and_revalidated_by_etag(
    make_client: ClientFactory, picture: str
):
    """The sidebar and the list filter show every avatar on each visit; without this
    they were downloaded again every time and flashed the name's first letter."""
    client = make_client(FakeLLM(), user=None)

    res = await client.get(f"/api/characters/mano/{picture}")

    assert res.headers["cache-control"] == "public, max-age=86400"
    again = await client.get(
        f"/api/characters/mano/{picture}", headers={"if-none-match": res.headers["etag"]}
    )
    assert again.status_code == 304
    assert again.headers["cache-control"] == "public, max-age=86400"
    stale = await client.get(f"/api/characters/mano/{picture}", headers={"if-none-match": '"stale"'})
    assert stale.status_code == 200


@pytest.mark.parametrize(
    ("character", "sheet", "other_sheet"),
    [("mano", MANO_SHEET, KOGANE_SHEET), ("kogane", KOGANE_SHEET, MANO_SHEET)],
)
async def test_each_character_talks_with_her_own_character_sheet(
    make_client: ClientFactory, character: str, sheet: str, other_sheet: str
):
    llm = FakeLLM(["<ja>はい</ja><zh>好</zh>"])
    client = make_client(llm)
    conversation_id = await start_conversation(client, character)

    await send_turn(client, conversation_id, "你好")

    system = llm.prompts[0][0]
    assert sheet in system.content
    assert other_sheet not in system.content


async def test_a_character_added_to_the_configuration_can_be_talked_to(
    make_client: ClientFactory, characters: CharacterConfig
):
    characters.add("hiori", "風野灯織", "# 人设\n你是風野灯織。", intro="认真的努力家")
    llm = FakeLLM(["<ja>はい</ja><zh>好</zh>"])
    client = make_client(llm)

    listed = (await client.get("/api/characters")).json()
    conversation_id = await start_conversation(client, "hiori")
    await send_turn(client, conversation_id, "你好")

    assert [c["id"] for c in listed] == ["mano", "kogane", "hiori"]
    assert "你是風野灯織。" in llm.prompts[0][0].content


async def test_conversation_with_a_character_removed_from_the_configuration_is_gone(
    make_client: ClientFactory, characters: CharacterConfig
):
    characters.add("hiori", "風野灯織", "# 人设\n你是風野灯織。")
    conversation_id = await start_conversation(make_client(FakeLLM()), "hiori")
    CharacterConfig(characters.directory).add("mano", "櫻木真乃", MANO_SHEET)
    client = make_client(FakeLLM())

    res = await client.post(f"/api/conversations/{conversation_id}/turns", json={"text": "你好"})

    assert res.status_code == 404
    assert res.json()["detail"] == "角色不存在"


def test_repository_configures_its_characters_with_their_voices():
    loaded = load_characters(REPO_CHARACTERS / "characters.toml")

    assert {id: c.voice_id for id, c in loaded.items()} == {
        "mano": "a1150f3d14ac46fb8ea59c7803cd8357",
        "hiori": "df43849d16ae48fe87f5670b6a2b81b3",
        "meguru": "17ce7b6d4ccb43a2bedac1fdb4a219cb",
        "kogane": "a11bfd046e924099a84f989fb4049e95",
        "mamimi": "1cbb1ba24d094810a6cada880dd8dec0",
        "sakuya": "3543f8d3e498439794f14be23dafbe89",
        "yuika": "e6b09fdf01874e63adf5f8aa45c0d2ea",
        "kiriko": "115e921e1b4740b08f3ec723666b2b40",
        "toru": "db8fb218614745f6b854e24620c33445",
        "madoka": "9fa98a656a0d4242b6073fd955f5179b",
        "koito": "8ee2e17bbead4610a17f324fe540be49",
        "hinana": "565ffd381fbc427ebfa1cc4c689ac12d",
        "nichika": "b3757a5a71ba4078be1e25b446190975",
        "mikoto": "bf578eec18ca49daaf9216f5df3fabd4",
        "asahi": "2dcc8351008f4714b33506d7d45a861e",
        "fuyuko": "105efba2160a40119d6d21e5445b889e",
        "mei": "1f0033aac5b34d478ec975114c845bf9",
        "kaho": "d9e61a6914dd47669a9bbae71a78ab38",
        "chiyoko": "a48ea1f5d81a478ba528b49188bf4ccc",
        "juri": "8875da3542374b18a29b85fbf48f0de2",
        "rinze": "dd73b57d509b436b90ef9263d2e6c5d1",
        "natsuha": "70e8160d2cf34c5892b9002e42cbafdb",
        "amana": "d5a68fc022604b23b94e2a0b9f823b59",
        "tenka": "7f50c8ef897c402fa7967a11aa94261e",
        "chiyuki": "7de8d70dc5e04759be11c01f69c8c863",
        "luca": "79e3dd87aa7649dab07350896ce995ae",
        "hana": "ccfce7160c664853813988cea175d1ab",
        "haruki": "5dd68541a9a04f50b9332572e0d266ff",
        "hazuki": "a061289170654e1a9dda177a28764651",
    }
    assert {id: c.name for id, c in loaded.items()} == {
        "mano": "櫻木真乃",
        "hiori": "風野灯織",
        "meguru": "八宮めぐる",
        "kogane": "月岡恋鐘",
        "mamimi": "田中摩美々",
        "sakuya": "白瀬咲耶",
        "yuika": "三峰結華",
        "kiriko": "幽谷霧子",
        "toru": "浅倉透",
        "madoka": "樋口円香",
        "koito": "福丸小糸",
        "hinana": "市川雛菜",
        "nichika": "七草にちか",
        "mikoto": "緋田美琴",
        "asahi": "芹沢あさひ",
        "fuyuko": "黛冬優子",
        "mei": "和泉愛依",
        "kaho": "小宮果穂",
        "chiyoko": "園田智代子",
        "juri": "西城樹里",
        "rinze": "杜野凛世",
        "natsuha": "有栖川夏葉",
        "amana": "大崎甘奈",
        "tenka": "大崎甜花",
        "chiyuki": "桑山千雪",
        "luca": "斑鳩ルカ",
        "hana": "鈴木羽那",
        "haruki": "郁田はるき",
        "hazuki": "七草はづき",  # the office's clerk, not an idol
    }
    assert {id: c.speakers for id, c in loaded.items()} == {
        "mano": ("真乃",),
        "hiori": ("灯織",),
        "meguru": ("めぐる",),
        "kogane": ("恋鐘",),
        "mamimi": ("摩美々",),
        "sakuya": ("咲耶",),
        "yuika": ("結華",),
        "kiriko": ("霧子",),
        "toru": ("透",),
        "madoka": ("円香",),
        "koito": ("小糸",),
        "hinana": ("雛菜",),
        "nichika": ("にちか",),
        "mikoto": ("美琴",),
        "asahi": ("あさひ",),
        "fuyuko": ("冬優子",),
        "mei": ("愛依",),
        "kaho": ("果穂", "果穗"),
        "chiyoko": ("智代子",),
        "juri": ("樹里", "树里"),
        "rinze": ("凛世",),
        "natsuha": ("夏葉", "夏叶"),
        "amana": ("甘奈",),
        "tenka": ("甜花",),
        "chiyuki": ("千雪",),
        "luca": ("ルカ",),
        "hana": ("羽那",),
        "haruki": ("はるき",),
        "hazuki": ("はづき",),
    }
    assert all(c.sheet and c.facts and c.avatar and c.scsp_avatar for c in loaded.values())  # committed (ADR-0003)
    assert {id: c.color for id, c in loaded.items()} == {
        "mano": "#ffbad6",
        "hiori": "#144384",
        "meguru": "#ffe012",
        "kogane": "#f84cad",
        "mamimi": "#a846fb",
        "sakuya": "#006047",
        "yuika": "#3b91c4",
        "kiriko": "#d9f2ff",
        "toru": "#50d0d0",
        "madoka": "#be1e3e",
        "koito": "#7967c3",
        "hinana": "#ffc639",
        "nichika": "#a6ceb6",
        "mikoto": "#760f10",
        "asahi": "#f30100",
        "fuyuko": "#5ce626",
        "mei": "#ff00ff",
        "kaho": "#e5461c",
        "chiyoko": "#f93b90",
        "juri": "#ffc602",
        "rinze": "#89c3eb",
        "natsuha": "#90e667",
        "amana": "#f54275",
        "tenka": "#e75bec",
        "chiyuki": "#fbfafa",
        "luca": "#24130d",
        "hana": "#e0b5d3",
        "haruki": "#ead7a4",
        "hazuki": "#c3d825",
    }


def test_character_facts_are_split_by_heading(characters: CharacterConfig):
    characters.add(
        "hiori", "風野灯織", "sheet",
        facts="# 档案\n生日是2月27日。\n\n# 人物关系\n\n## 櫻木真乃\n同组合的伙伴。\n### 趣事\n一起去看鸟。\n",
    )

    assert load_characters(characters.path)["hiori"].facts == (
        Fact("档案", "档案\n生日是2月27日。"),
        Fact("人物关系 / 櫻木真乃", "人物关系 / 櫻木真乃\n同组合的伙伴。"),
        Fact("人物关系 / 櫻木真乃 / 趣事", "人物关系 / 櫻木真乃 / 趣事\n一起去看鸟。"),
    )


def test_text_before_any_heading_is_an_untitled_fact():
    assert parse_facts("生日是2月27日。\n\n# 档案\n身高155cm。") == (
        Fact(None, "生日是2月27日。"),
        Fact("档案", "档案\n身高155cm。"),
    )


def test_missing_character_sheet_is_reported_with_its_path(tmp_path: Path):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", sheet=None)

    with pytest.raises(CharacterConfigError, match=r"sheets[/\\]mano\.md"):
        load_characters(config.path)


def test_duplicate_character_ids_are_rejected(tmp_path: Path):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a").add("mano", "真乃", "b")

    with pytest.raises(CharacterConfigError, match="mano"):
        load_characters(config.path)


def test_character_missing_a_required_field_is_rejected(tmp_path: Path):
    config = CharacterConfig(tmp_path)
    config.write("characters.toml", '[[characters]]\nid = "mano"\nname = "櫻木真乃"\n')

    with pytest.raises(CharacterConfigError, match="intro、voice_id、sheet、color"):
        load_characters(config.path)


def test_character_id_must_be_safe_to_put_in_a_url(tmp_path: Path):
    config = CharacterConfig(tmp_path)
    config.write("characters.toml", '[[characters]]\nid = "Mano San"\nname = "a"\n'
                 'intro = ""\nvoice_id = "v"\nsheet = "s.md"\ncolor = "#ffbad6"\n')

    with pytest.raises(CharacterConfigError, match="Mano San"):
        load_characters(config.path)


def test_speakers_must_be_a_list_of_names(tmp_path: Path):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a")
    config.path.write_text(
        config.path.read_text(encoding="utf-8") + 'speakers = "真乃"\n', encoding="utf-8"
    )

    with pytest.raises(CharacterConfigError, match="speakers"):
        load_characters(config.path)


def test_two_characters_cannot_share_a_speaker_name(tmp_path: Path):
    config = (
        CharacterConfig(tmp_path)
        .add("mano", "櫻木真乃", "a", speakers=["真乃"])
        .add("mano2", "真乃", "b", speakers=["真乃"])
    )

    with pytest.raises(CharacterConfigError, match="真乃"):
        load_characters(config.path)


def test_missing_configuration_file_is_reported(tmp_path: Path):
    with pytest.raises(CharacterConfigError, match="characters.toml"):
        load_characters(tmp_path / "characters.toml")


@pytest.mark.parametrize("color", ["ffbad6", "#fff", "#ffbad", "#ffbad6ff", "#ggbad6", "pink", ""])
def test_image_colour_must_be_a_hex_rrggbb(tmp_path: Path, color: str):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a", color=color)

    with pytest.raises(CharacterConfigError, match="角色 mano 的 color.*#rrggbb"):
        load_characters(config.path)


def test_image_colour_is_read_in_lower_case(tmp_path: Path):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a", color="#FFBAD6")

    assert load_characters(config.path)["mano"].color == "#ffbad6"


def test_missing_call_background_is_a_warning_and_treated_as_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a")
    config.path.write_text(
        config.path.read_text(encoding="utf-8") + 'background = "backgrounds/mano.jpg"\n',
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="eidolon.characters"):
        mano = load_characters(config.path)["mano"]

    assert mano.background is None
    assert "backgrounds" in caplog.text and "mano" in caplog.text


STAND_ASSETS = "https://stand.example/eidolon"


async def test_character_with_an_idol_and_a_default_outfit_has_a_stand_and_her_outfit_list_once_assets_are_served(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(), settings=replace(settings, stand_assets_url=STAND_ASSETS), user=None)

    listed = {c["id"]: c for c in (await client.get("/api/characters")).json()}

    assert listed["mano"]["stand"] == {
        "default_outfit": {
            "id": "stand/1030010010",
            "url": f"{STAND_ASSETS}/spine/idols/stand/1030010010/",
        },
        "outfits_url": f"{STAND_ASSETS}/outfits/1.json",
    }
    assert listed["kogane"]["stand"] is None  # no idol, no default outfit


@pytest.mark.parametrize(
    ("idol_id", "default_outfit"), [(1, None), (None, "stand_costume/1030010010")]
)
async def test_character_missing_her_idol_or_her_default_outfit_has_no_stand(
    make_client: ClientFactory,
    settings: Settings,
    characters: CharacterConfig,
    idol_id: int | None,
    default_outfit: str | None,
):
    characters.add("hiori", "風野灯織", "sheet", idol_id=idol_id, default_outfit=default_outfit)
    client = make_client(FakeLLM(), settings=replace(settings, stand_assets_url=STAND_ASSETS))

    listed = {c["id"]: c for c in (await client.get("/api/characters")).json()}

    assert listed["hiori"]["stand"] is None


def add_raw(config: CharacterConfig, line: str) -> None:
    config.path.write_text(config.path.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")


@pytest.mark.parametrize("value", ['"1"', "0", "1.5", "true"])
def test_idol_id_must_be_a_positive_whole_number(tmp_path: Path, value: str):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a")
    add_raw(config, f"idol_id = {value}")

    with pytest.raises(CharacterConfigError, match="角色 mano 的 idol_id"):
        load_characters(config.path)


@pytest.mark.parametrize(
    "value",
    [
        '"1030010010"',
        '"cb/1030010010"',
        '"stand/103001001"',
        '"stand/1030010010/"',
        '"spine/idols/stand/1030010010"',
        "1030010010",
    ],
)
def test_default_outfit_must_name_a_stand_outfit(tmp_path: Path, value: str):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a")
    add_raw(config, f"default_outfit = {value}")

    with pytest.raises(CharacterConfigError, match="角色 mano 的 default_outfit.*stand/<enzaId>"):
        load_characters(config.path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("name_zh", '"  "'), ("name_kana", "1"), ("name_romaji", '"Sakuragi\\rMano"')],
)
def test_other_names_must_each_be_a_line_of_text(tmp_path: Path, field: str, value: str):
    """Not configuring them at all is fine (the list test above)."""
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a")
    add_raw(config, f"{field} = {value}")

    with pytest.raises(CharacterConfigError, match=f"角色 mano 的 {field}"):
        load_characters(config.path)


def test_repository_gives_each_character_her_other_names():
    """Her reading has a space between her family name and her given name, and is the
    world setting's reading of her name but for it (an idol's, or the office staff's for the
    clerk)."""
    loaded = load_characters(REPO_CHARACTERS / "characters.toml")
    world = json.loads((REPO_CHARACTERS / "world.json").read_text(encoding="utf-8"))
    readings = {
        person["name"]: person["reading"] for person in world["idols"] + world["office"]["staff"]
    }

    for c in loaded.values():
        assert c.name_zh and c.name_kana and c.name_romaji, c.id
        assert c.name_kana.count(" ") == 1 and c.name_kana.replace(" ", "") == readings[c.name], c.id
        assert re.fullmatch(r"[A-Z][a-z]+ [A-Z][a-z]+", c.name_romaji), c.id


def test_repository_gives_each_character_her_photo_tags():
    """Her character tag in lower case, and her looks with one eye colour among them."""
    loaded = load_characters(REPO_CHARACTERS / "characters.toml")

    for c in loaded.values():
        assert c.photo_tag and c.photo_appearance, c.id
        assert c.photo_tag == c.photo_tag.lower(), c.id
        looks = [tag.strip() for tag in c.photo_appearance.split(",")]
        eye_colours = [t for t in looks if t.endswith(" eyes") and not t.startswith("hair ")]
        assert len(eye_colours) == 1, c.id  # `hair between eyes` is how her bangs fall


def test_repository_gives_its_characters_their_idol_ids_and_default_outfits():
    loaded = load_characters(REPO_CHARACTERS / "characters.toml")

    assert {id: (c.idol_id, c.default_outfit) for id, c in loaded.items()} == {
        "mano": (1, "stand/1030010010"),
        "hiori": (2, "stand/1030020010"),
        "meguru": (3, "stand/1030030010"),
        "kogane": (4, "stand/1030040010"),
        "mamimi": (5, "stand/1030050010"),
        "sakuya": (6, "stand/1030060010"),
        "yuika": (7, "stand/1030070010"),
        "kiriko": (8, "stand/1030080010"),
        "toru": (20, "stand/1030200010"),
        "madoka": (21, "stand/1030210010"),
        "koito": (22, "stand/1030220010"),
        "hinana": (23, "stand/1030230010"),
        "nichika": (24, "stand/1030240010"),
        "mikoto": (25, "stand/1030250010"),
        "asahi": (17, "stand/1030170010"),
        "fuyuko": (18, "stand/1030180010"),
        "mei": (19, "stand/1030190010"),
        "kaho": (9, "stand/1030090010"),
        "chiyoko": (10, "stand/1030100010"),
        "juri": (11, "stand/1030110010"),
        "rinze": (12, "stand/1030120010"),
        "natsuha": (13, "stand/1030130010"),
        "amana": (14, "stand/1030140010"),
        "tenka": (15, "stand/1030150010"),
        "chiyuki": (16, "stand/1030160010"),
        "luca": (26, "stand/1030260010"),
        "hana": (27, "stand/1030270010"),
        "haruki": (28, "stand/1030280010"),
        "hazuki": (91, "stand/2040910010"),
    }


def _jpeg_size(path: Path) -> tuple[int, int]:
    """Width and height from a JPEG's start-of-frame segment, without an imaging library."""
    data = path.read_bytes()
    i = 2
    while i < len(data):
        marker, length = data[i + 1], int.from_bytes(data[i + 2 : i + 4])
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return int.from_bytes(data[i + 7 : i + 9]), int.from_bytes(data[i + 5 : i + 7])
        i += 2 + length
    raise ValueError(f"{path} has no start-of-frame segment")


def test_repository_avatars_are_shrunk_to_what_the_screens_show():
    """Shown at 112px at most, so 224px for high-density screens (characters.toml). The list
    filter loads every one of them; most 400px originals were 100–160 KB, a few about 30 KB,
    so the size alone wouldn't catch one being put back."""
    loaded = load_characters(REPO_CHARACTERS / "characters.toml")

    too_big = {}
    for id, c in loaded.items():
        for field, avatar in (("avatar", c.avatar), ("scsp_avatar", c.scsp_avatar)):
            if avatar is None:
                continue
            width, height = _jpeg_size(avatar)
            size = avatar.stat().st_size
            if width > 224 or height > 224 or size > 32 * 1024:
                too_big[id, field] = (width, height, size)
    assert too_big == {}


def test_stand_assets_url_is_read_without_its_trailing_slash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EIDOLON_STAND_ASSETS_URL", raising=False)
    assert stand_assets_url_from_env() is None

    monkeypatch.setenv("EIDOLON_STAND_ASSETS_URL", " https://stand.example/eidolon/ ")
    assert stand_assets_url_from_env() == "https://stand.example/eidolon"

    monkeypatch.setenv("EIDOLON_STAND_ASSETS_URL", "http://localhost:8765")
    assert stand_assets_url_from_env() == "http://localhost:8765"


@pytest.mark.parametrize("value", ["stand.example/eidolon", "/stand", "ftp://stand.example"])
def test_stand_assets_url_must_be_a_web_address(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("EIDOLON_STAND_ASSETS_URL", value)

    with pytest.raises(RuntimeError, match="EIDOLON_STAND_ASSETS_URL"):
        stand_assets_url_from_env()


MANO_GREETING = ("[softly] ほわっ……はじめまして、櫻木真乃です", "……初次见面，我是櫻木真乃")


def mano_with_greeting(
    characters: CharacterConfig,
    greeting: tuple[str, str] = MANO_GREETING,
    voice_id: str = "mano-voice",
) -> None:
    """Rewrite the configuration with Mano alone, greeting as given."""
    CharacterConfig(characters.directory).add(
        "mano", "櫻木真乃", MANO_SHEET, voice_id=voice_id, greeting=greeting
    )


async def listed_greeting(
    make_client: ClientFactory, settings: Settings, assets: str | None = STAND_ASSETS
) -> dict:
    client = make_client(FakeLLM(), settings=replace(settings, stand_assets_url=assets), user=None)
    listed = {c["id"]: c for c in (await client.get("/api/characters")).json()}
    return listed["mano"]["greeting"]


async def test_greeting_is_shown_without_its_delivery_cues_with_its_subtitle_and_where_to_hear_it(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    mano_with_greeting(characters)

    greeting = await listed_greeting(make_client, settings)

    assert greeting["ja"] == "ほわっ……はじめまして、櫻木真乃です"
    assert greeting["subtitle"] == "……初次见面，我是櫻木真乃"
    assert re.fullmatch(rf"{re.escape(STAND_ASSETS)}/greetings/mano-[0-9a-f]+\.mp3", greeting["audio_url"])


async def test_greeting_has_no_audio_unless_the_stand_assets_are_served(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    mano_with_greeting(characters)

    greeting = await listed_greeting(make_client, settings, assets=None)

    assert greeting == {
        "ja": "ほわっ……はじめまして、櫻木真乃です",
        "subtitle": "……初次见面，我是櫻木真乃",
        "audio_url": None,
    }


async def test_greeting_audio_moves_when_its_words_or_her_voice_change_but_not_its_subtitle(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    """The address changes with what she says, cues and all, and in what voice, so a
    browser never plays an old one from its cache."""
    mano_with_greeting(characters)
    first = (await listed_greeting(make_client, settings))["audio_url"]
    mano_with_greeting(characters, (MANO_GREETING[0], "初次见面"))
    resubtitled = (await listed_greeting(make_client, settings))["audio_url"]
    mano_with_greeting(characters, ("[happily] ほわっ……はじめまして、櫻木真乃です", MANO_GREETING[1]))
    recued = (await listed_greeting(make_client, settings))["audio_url"]
    mano_with_greeting(characters, voice_id="another-voice")
    revoiced = (await listed_greeting(make_client, settings))["audio_url"]

    assert resubtitled == first
    assert len({first, recued, revoiced}) == 3


async def test_greeting_of_a_character_without_a_voice_has_no_audio(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    mano_with_greeting(characters, voice_id="")

    greeting = await listed_greeting(make_client, settings)

    assert greeting["ja"] == "ほわっ……はじめまして、櫻木真乃です"
    assert greeting["audio_url"] is None


@pytest.mark.parametrize(
    "lines",
    [
        'greeting = "はじめまして"',
        'greeting_subtitle = "初次见面"',
        'greeting = "[softly]"\ngreeting_subtitle = "初次见面"',
    ],
)
def test_greeting_needs_both_its_words_and_its_subtitle(tmp_path: Path, lines: str):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a")
    add_raw(config, lines)

    with pytest.raises(CharacterConfigError, match="角色 mano 的招呼"):
        load_characters(config.path)


def test_greeting_is_optional(tmp_path: Path):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a")

    assert load_characters(config.path)["mano"].greeting is None


MANO_CALL_OPENER = (
    "[shyly] あっ、もしもし……真乃です。えっと、今、お電話だいじょうぶですか？",
    "啊，喂……我是真乃。那个，现在方便接电话吗？",
)


@pytest.mark.parametrize(
    "lines",
    [
        'call_opener = "もしもし"',
        'call_opener_subtitle = "喂"',
        'call_opener = "[shyly]"\ncall_opener_subtitle = "喂"',
    ],
)
def test_call_opener_needs_both_its_words_and_its_subtitle(tmp_path: Path, lines: str):
    """As her greeting does, and said the same way."""
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a")
    add_raw(config, lines)

    with pytest.raises(CharacterConfigError, match="角色 mano 的开场白"):
        load_characters(config.path)


def test_call_opener_is_optional(tmp_path: Path):
    config = CharacterConfig(tmp_path).add("mano", "櫻木真乃", "a")

    assert load_characters(config.path)["mano"].call_opener is None


def test_call_opener_is_seen_without_its_delivery_cues_apart_from_her_greeting(tmp_path: Path):
    config = CharacterConfig(tmp_path).add(
        "mano", "櫻木真乃", "a", greeting=MANO_GREETING, call_opener=MANO_CALL_OPENER
    )

    mano = load_characters(config.path)["mano"]

    assert mano.call_opener is not None and mano.greeting is not None
    assert mano.call_opener.ja == "あっ、もしもし……真乃です。えっと、今、お電話だいじょうぶですか？"
    assert mano.call_opener.subtitle == MANO_CALL_OPENER[1]
    assert mano.greeting.ja == "ほわっ……はじめまして、櫻木真乃です"


async def listed_call_opener(
    make_client: ClientFactory, settings: Settings, assets: str | None = STAND_ASSETS
) -> dict:
    client = make_client(FakeLLM(), settings=replace(settings, stand_assets_url=assets), user=None)
    listed = {c["id"]: c for c in (await client.get("/api/characters")).json()}
    return listed["mano"]["call_opener"]


async def test_call_opener_is_listed_without_its_delivery_cues_with_its_subtitle_and_where_to_hear_it(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    CharacterConfig(characters.directory).add(
        "mano", "櫻木真乃", MANO_SHEET, voice_id="mano-voice", greeting=MANO_GREETING, call_opener=MANO_CALL_OPENER
    )

    opener = await listed_call_opener(make_client, settings)

    assert opener["ja"] == "あっ、もしもし……真乃です。えっと、今、お電話だいじょうぶですか？"
    assert opener["subtitle"] == "啊，喂……我是真乃。那个，现在方便接电话吗？"
    assert re.fullmatch(rf"{re.escape(STAND_ASSETS)}/call-openers/mano-[0-9a-f]+\.mp3", opener["audio_url"])
    greeting = await listed_greeting(make_client, settings)
    assert greeting["audio_url"] != opener["audio_url"]


async def test_call_opener_has_no_audio_unless_the_stand_assets_are_served(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    CharacterConfig(characters.directory).add(
        "mano", "櫻木真乃", MANO_SHEET, voice_id="mano-voice", call_opener=MANO_CALL_OPENER
    )

    opener = await listed_call_opener(make_client, settings, assets=None)

    assert opener == {
        "ja": "あっ、もしもし……真乃です。えっと、今、お電話だいじょうぶですか？",
        "subtitle": "啊，喂……我是真乃。那个，现在方便接电话吗？",
        "audio_url": None,
    }
    assert (await listed_greeting(make_client, settings)) is None


async def test_call_poster_and_voxels_are_where_the_bucket_keeps_them(make_client: ClientFactory, settings: Settings):
    """Her card on the landing page's incoming call and her voxel figure in its act about
    memory, both put in the bucket by hand: listed without asking the bucket whether they are
    there (the page falls back to her colour)."""
    client = make_client(FakeLLM(), settings=replace(settings, stand_assets_url=STAND_ASSETS), user=None)

    listed = (await client.get("/api/characters")).json()

    assert {c["id"]: (c["call_poster_url"], c["voxels_url"]) for c in listed} == {
        "mano": (f"{STAND_ASSETS}/call-posters/mano.webp", f"{STAND_ASSETS}/landing-voxels/mano.json"),
        "kogane": (f"{STAND_ASSETS}/call-posters/kogane.webp", f"{STAND_ASSETS}/landing-voxels/kogane.json"),
    }


async def test_landing_memory_still_is_where_the_bucket_keeps_it(make_client: ClientFactory, settings: Settings):
    """The landing page's act about memory as one picture, for visitors who see no animation:
    put in the bucket by hand and listed as her call poster is, without asking the bucket."""
    served = make_client(FakeLLM(), settings=replace(settings, stand_assets_url=STAND_ASSETS), user=None)
    unserved = make_client(FakeLLM(), user=None)

    assert (await served.get("/api/landing")).json() == {"memory_still_url": f"{STAND_ASSETS}/landing-memory/still.webp"}
    assert (await unserved.get("/api/landing")).json() == {"memory_still_url": None}
