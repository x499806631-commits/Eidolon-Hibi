"""The world setting in every turn: what the chat model reads after the character sheet,
taken from the data file beside the character configuration, and the backend refusing to
start when a character is none of its idols and none of the office's staff."""

import json
import logging
from datetime import UTC, datetime

import pytest

from eidolon.config import Settings
from eidolon.characters import load_characters
from eidolon.own_keys import BroughtKeys
from eidolon.world import WorldError, load_world, world_path
from tests.conftest import MANO_SHEET, CharacterConfig, ClientFactory
from tests.fakes import FakeBackgroundLLM, FakeLLM, build_faked, system_of
from tests.test_characters import REPO_CHARACTERS
from tests.test_turns import REPLY, Turns, turns  # noqa: F401  (the fixture)

CVS = ["関根瞳", "礒部花凜", "近藤玲奈", "津田健次郎"]


RELEASES = [
    ("LACM-14782", "THE IDOLM@STER SHINY COLORS BRILLI@NT WING 02 ヒカリのdestination", "2018-07-04",
     ["ヒカリのdestination", "Spread the Wings!!（イルミネーションスターズ Ver.）", None]),
    ("LACA-15861", "THE IDOLM@STER SHINY COLORS COLORFUL FE@THERS -Stella-", "2021-01-20",
     ["プラニスフィア ～planisphere～", "ありったけの輝きで", "アポイント・シグナル"]),
    ("LACA-9946～7", "THE IDOLM@STER SHINY COLORS WING COLLECTION -A side-", "2023-01-18",
     ["ヒカリのdestination", "スローモーション"]),
    ("LACA-35264～5", "283 Production MUSICAL Performance 騎士団のヴェール Music Collection【初回限定盤】",
     "2026-03-11", ["愚者の独白"]),
    ("LACA-25301", "THE IDOLM@STER SHINY COLORS HOPEFUL FE@THERS -Stella-", "2026-10-21",
     ["MAGICAL FOREST"]),
    ("LACM-14861", "THE IDOLM@STER SHINY COLORS FR@GMENT WING 01", "2019-04-10", ["Ambitious Eve"]),
]
"""Releases for `write_world`: catalog number, title, release date, and the songs on it
(None for a track that is no song, like a drama)."""

SONGS = [
    ("ヒカリのdestination", ["櫻木真乃", "風野灯織"], None),
    ("Spread the Wings!!（イルミネーションスターズ Ver.）", ["櫻木真乃", "風野灯織"], None),
    ("プラニスフィア ～planisphere～", ["櫻木真乃", "月岡恋鐘"], "Team.Stella"),
    ("ありったけの輝きで", ["櫻木真乃"], None),
    ("アポイント・シグナル", ["月岡恋鐘"], None),
    ("スローモーション", ["風野灯織"], None),
    ("愚者の独白", ["櫻木真乃", "風野灯織", "月岡恋鐘"], None),
    ("MAGICAL FOREST", ["櫻木真乃"], None),
    ("Ambitious Eve", ["櫻木真乃", "風野灯織", "月岡恋鐘"], None),
]
"""Songs for `write_world`: title, singers and the special unit that sang it, if one did.
Mano's are her own two solos, her unit's two songs and nothing else."""

LIVES = [
    ("THE IDOLM@STER SHINY COLORS 6thLIVE", [
        ("2024-03-02", "ベルーナドーム", ["櫻木真乃", "風野灯織", "月岡恋鐘"], []),
        ("2024-03-03", "ベルーナドーム", ["風野灯織", "月岡恋鐘"], ["櫻木真乃"]),
    ]),
    ("L'Antica 単独公演", [("2025-05-10", "Zepp Haneda", ["月岡恋鐘"], [])]),
    ("THE IDOLM@STER M@STERS OF IDOL WORLD!!!!! 2023", [
        ("2023-02-11", "東京ドーム", ["櫻木真乃", "月岡恋鐘"], []),
    ]),
    ("THE IDOLM@STER SHINY COLORS 8thLIVE", [
        ("2026-11-07", "Kアリーナ横浜", ["櫻木真乃"], []),
        ("2026-11-08", "Kアリーナ横浜", ["櫻木真乃", "風野灯織"], []),
    ]),
]
"""Lives for `write_world`: name, and each day's date, venue, performers and the idols
whose CV was absent that day (so not among the performers). Mano stood on 6thLIVE's
first day only, on MOIW, and is to stand on both days of 8thLIVE, still to come."""

SETLISTS = {
    ("THE IDOLM@STER SHINY COLORS 6thLIVE", "2024-03-02"): [
        ("Ambitious Eve", ["櫻木真乃", "風野灯織", "月岡恋鐘"], [], None),
        ("Spread the Wings!!（イルミネーションスターズ Ver.）", ["櫻木真乃", "風野灯織"], [], None),
        ("アポイント・シグナル", ["月岡恋鐘"], ["櫻木真乃"], None),
        ("乱舞る (short ver.)", [], ["風野灯織"], None),
        ("Resonance⁺", ["風野灯織", "月岡恋鐘"], [], None),
    ],
    ("THE IDOLM@STER M@STERS OF IDOL WORLD!!!!! 2023", "2023-02-11"): [
        ("ドレミファクトリー！", ["櫻木真乃"], [], "シンデレラガールズ"),
    ],
}
"""What was performed on some of the days of `LIVES`: title, singers, dancers and the
other brand whose song it is, if it is one."""


def write_world(
    characters: CharacterConfig, idols: list[tuple[str, str | None, str]] | None = None
) -> None:
    """A data file as `eidolon world sync` writes it: Mano's and Kogane's units, the
    office's president and clerk, and the songs and releases above."""
    idols = idols or [
        ("櫻木真乃", "illumination STARS", "関根瞳"),
        ("風野灯織", "illumination STARS", "近藤玲奈"),
        ("月岡恋鐘", "L'Antica", "礒部花凜"),
    ]
    source = ["https://example.invalid/source"]
    characters.write("world.json", json.dumps({
        "as_of": "2026-09-28",
        "office": {"name": "283プロダクション", "staff": [
            {"name": "天井努", "reading": "あまいつとむ", "position": "社長", "sources": source},
            {"name": "七草はづき", "reading": "ななくさはづき", "position": "事務員", "sources": source},
        ]},
        "units": [
            {"name": "illumination STARS", "reading": "いるみねーしょんすたーず",
             "members": ["櫻木真乃", "風野灯織"], "sources": source},
            {"name": "L'Antica", "reading": "あんてぃーか", "members": ["月岡恋鐘"], "sources": source},
        ],
        "idols": [
            {"name": name, "reading": "", "unit": unit, "cv": cv, "sources": source}
            for name, unit, cv in idols
        ],
        "releases": [
            {"catalog": catalog, "title": title, "date": day,
             "tracks": [{"title": song or "スペシャルオーディオドラマ", "song": song} for song in songs],
             "sources": source}
            for catalog, title, day, songs in RELEASES
        ],
        "songs": [
            {"id": title, "title": title, "singers": singers, "special_unit": special,
             "releases": [catalog for catalog, _, _, songs in RELEASES if title in songs],
             "sources": source}
            for title, singers, special in SONGS
        ],
        "lives": [
            {"name": name, "days": [
                {"date": day, "venue": venue, "performers": performers, "absent": absent,
                 "sources": source, "setlist": [
                     {"title": title, "singers": singers, "dancers": dancers, "brand": brand}
                     for title, singers, dancers, brand in SETLISTS.get((name, day), [])
                 ]}
                for day, venue, performers, absent in days
            ], "sources": source}
            for name, days in LIVES
        ],
    }, ensure_ascii=False))


def section(system: str, heading: str) -> str:
    """The part of the system prompt under `heading`."""
    start = system.index(heading)
    end = system.find("\n# ", start + 1)
    return system[start:] if end == -1 else system[start:end]


def system_prompt(llm: FakeLLM) -> str:
    [prompt] = llm.prompts
    return system_of(prompt)


async def test_every_turn_carries_the_world_setting_after_the_character_sheet(
    turns: Turns, characters: CharacterConfig
):
    """The two open the prompt apart, with how she is to format her reply, the same for
    every user's turns with her that day, marked for a service to cache; the rest of what
    she is told follows on its own."""
    write_world(characters)
    llm = FakeLLM(REPLY)

    await turns.take(llm, "イルミネのメンバーって誰だっけ")

    [prompt] = llm.prompts
    shared = prompt[0]
    assert shared.cache_breakpoint
    assert shared.content.startswith(f"{MANO_SHEET}\n\n# 世界设定")
    world = shared.content.removeprefix(f"{MANO_SHEET}\n\n")
    assert "资料截止到 2026年9月28日" in world
    assert "社長：天井努" in world and "事務員：七草はづき" in world
    assert "illumination STARS（いるみねーしょんすたーず）：櫻木真乃、風野灯織" in world
    assert "L'Antica（あんてぃーか）：月岡恋鐘" in world  # her unit or not, every unit


async def test_the_world_setting_carries_the_rules_for_facts_of_the_original(
    turns: Turns, characters: CharacterConfig
):
    write_world(characters)
    llm = FakeLLM(REPLY)

    await turns.take(llm, "新曲の歌詞、ちょっと歌ってみて")

    system = system_prompt(llm)
    assert "查不到的，就老实说一时想不起来" in system
    assert "既不说「没有这回事」，也不顺着对方的说法附和" in system
    assert "不引用歌词，也不编造歌词" in system
    assert "不编造台上发生了什么" in system
    assert "不谈论声优" in system
    assert "不谈论具体的周边商品" in system
    assert "2026年9月28日以后的事" in system and "一概不知道" in system


def her_songs(system: str) -> str:
    """The part of the system prompt that lists her songs."""
    return section(system, "# 你的歌")


async def test_every_turn_carries_her_songs_with_their_releases(
    turns: Turns, characters: CharacterConfig
):
    write_world(characters)
    llm = FakeLLM(REPLY)

    await turns.take(llm, "真乃のソロ曲って何があったっけ")

    system = system_prompt(llm)
    assert system.index("# 世界设定") < system.index("# 你的歌") < system.index("# 制作人")
    songs = her_songs(system)
    assert "- ありったけの輝きで：" in songs  # sung by her alone
    assert "- ヒカリのdestination：" in songs  # sung by her whole unit
    assert "- Spread the Wings!!（イルミネーションスターズ Ver.）：" in songs
    line = next(line for line in songs.splitlines() if line.startswith("- ヒカリのdestination："))
    assert "《THE IDOLM@STER SHINY COLORS BRILLI@NT WING 02 ヒカリのdestination》（2018年7月4日发售）" in line
    assert "《THE IDOLM@STER SHINY COLORS WING COLLECTION -A side-》（2023年1月18日发售）" in line


async def test_her_songs_leave_out_anyone_elses(turns: Turns, characters: CharacterConfig):
    write_world(characters)
    llm = FakeLLM(REPLY)

    await turns.take(llm, "どんな歌を歌ってきたの？")

    songs = her_songs(system_prompt(llm))
    assert "アポイント・シグナル" not in songs  # another idol's solo
    assert "スローモーション" not in songs  # her unit mate's solo
    assert "プラニスフィア" not in songs  # a special unit's, with her in it
    assert "愚者の独白" not in songs  # her unit with someone from outside it
    assert "Ambitious Eve" not in songs  # everyone's


def song_line(system: str, title: str) -> str:
    return next(line for line in her_songs(system).splitlines() if line.startswith(f"- {title}"))


async def test_a_release_still_to_come_is_told_as_one(turns: Turns, characters: CharacterConfig):
    write_world(characters)  # the turn is on 2026-09-26; the release is on 2026-10-21
    llm = FakeLLM(REPLY)

    await turns.take(llm, "新しいCD、いつ出るんだっけ")

    line = song_line(system_prompt(llm), "MAGICAL FOREST")
    assert "（将于2026年10月21日发售）" in line
    assert "还没有发售" not in line
    past = song_line(system_prompt(llm), "ヒカリのdestination")
    assert "（2018年7月4日发售）" in past and "将于" not in past


async def test_whether_a_release_is_out_goes_by_the_day_of_each_turn(
    turns: Turns, settings: Settings, characters: CharacterConfig
):
    write_world(characters)  # the data runs to 2026-09-28; the release is on 2026-10-21
    now = [datetime(2026, 10, 20, 12, 0, tzinfo=UTC)]
    llm = FakeLLM(REPLY, REPLY)
    backend = build_faked(settings, llm, clock=lambda: now[0])
    try:
        llms = backend.own_keys.for_turn(BroughtKeys()).llms
        async for _ in backend.engine.turn(turns.conversation, "新しいCD、いつ出るんだっけ", llms):
            pass
        now[0] = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)  # the same backend, two days on
        async for _ in backend.engine.turn(turns.conversation, "新しいCD、もう出た？", llms):
            pass
    finally:
        await backend.aclose()

    before, after = (prompt[0].content for prompt in llm.prompts)
    assert "（将于2026年10月21日发售）" in song_line(before, "MAGICAL FOREST")
    assert "（2026年10月21日发售）" in song_line(after, "MAGICAL FOREST")


async def test_a_special_unit_s_song_is_not_hers_even_if_its_idols_are_her_unit(
    turns: Turns, characters: CharacterConfig
):
    write_world(characters)
    path = characters.directory / "world.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["songs"].append({
        "id": "とくべつな歌", "title": "とくべつな歌", "singers": ["櫻木真乃", "風野灯織"],
        "special_unit": "Team.Hikari", "releases": ["LACM-14782"],
        "sources": ["https://example.invalid/source"],
    })
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    llm = FakeLLM(REPLY)

    await turns.take(llm, "どんな歌を歌ってきたの？")

    songs = her_songs(system_prompt(llm))
    assert "特别组合或者全体一起唱的歌不在这里" in songs
    assert "とくべつな歌" not in songs


async def test_a_song_whose_releases_are_all_missing_is_listed_by_its_title_alone(
    turns: Turns, characters: CharacterConfig
):
    write_world(characters)
    path = characters.directory / "world.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["songs"].append({
        "id": "まぼろしの歌", "title": "まぼろしの歌", "singers": ["櫻木真乃"], "special_unit": None,
        "releases": ["NONE-0000"], "sources": ["https://example.invalid/source"],
    })
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    llm = FakeLLM(REPLY)

    await turns.take(llm, "どんな歌を歌ってきたの？")

    assert song_line(system_prompt(llm), "まぼろしの歌") == "- まぼろしの歌"


async def test_every_turn_carries_the_lives_she_stood_on_and_is_to_stand_on(
    turns: Turns, characters: CharacterConfig
):
    write_world(characters)  # the turn is on 2026-09-26
    llm = FakeLLM(REPLY)

    await turns.take(llm, "今までどんなステージに立ってきたっけ")

    system = system_prompt(llm)
    assert system.index("# 你的歌") < system.index("# 你上过的演出") < system.index("# 制作人")
    lives = section(system, "# 你上过的演出")
    assert "- THE IDOLM@STER SHINY COLORS 6thLIVE：2024年3月2日（ベルーナドーム）" in lives
    assert "- THE IDOLM@STER M@STERS OF IDOL WORLD!!!!! 2023：2023年2月11日（東京ドーム）" in lives
    assert (
        "- THE IDOLM@STER SHINY COLORS 8thLIVE："
        "将于2026年11月7日、将于2026年11月8日（Kアリーナ横浜）"
    ) in lives


async def test_her_lives_leave_out_the_days_she_was_absent_and_the_lives_she_was_not_on(
    turns: Turns, characters: CharacterConfig
):
    write_world(characters)
    llm = FakeLLM(REPLY)

    await turns.take(llm, "6thライブの二日目、どうだった？")

    lives = section(system_prompt(llm), "# 你上过的演出")
    assert "2024年3月3日" not in lives  # her CV was absent that day
    assert "L'Antica 単独公演" not in lives  # only another unit stood on it


async def test_setlists_are_left_to_retrieval_and_covers_are_not_her_songs(
    turns: Turns, characters: CharacterConfig
):
    write_world(characters)
    llm = FakeLLM(REPLY)

    await turns.take(llm, "6thライブのDay1でどんな曲を歌った？")

    system = system_prompt(llm)
    assert "曲目单" not in system
    assert "乱舞る" not in system and "アポイント・シグナル" not in section(system, "# 你上过的演出")
    assert "ドレミファクトリー！" not in system  # a cover she sang is none of her songs


async def test_whether_a_live_is_still_to_come_goes_by_the_day_of_each_turn(
    turns: Turns, settings: Settings, characters: CharacterConfig
):
    write_world(characters)
    now = [datetime(2026, 11, 7, 12, 0, tzinfo=UTC)]
    llm = FakeLLM(REPLY)
    backend = build_faked(settings, llm, clock=lambda: now[0])
    try:
        llms = backend.own_keys.for_turn(BroughtKeys()).llms
        async for _ in backend.engine.turn(turns.conversation, "今日のライブ、楽しみ！", llms):
            pass
    finally:
        await backend.aclose()

    lives = section(system_prompt(llm), "# 你上过的演出")
    assert "2026年11月7日、将于2026年11月8日（Kアリーナ横浜）" in lives


async def test_no_cv_is_named_in_any_prompt(turns: Turns, characters: CharacterConfig):
    write_world(characters)
    llm, memory = FakeLLM(REPLY, REPLY), FakeBackgroundLLM()

    await turns.take(llm, "真乃の声優って誰？", background_llm=memory)
    await turns.take(llm, "中の人は？", background_llm=memory)

    prompts = [m.content for prompt in [*llm.prompts, *memory.prompts] for m in prompt]
    assert prompts
    assert not [cv for cv in CVS for text in prompts if cv in text]


async def test_without_the_data_file_turns_go_on_without_the_world_setting(
    turns: Turns, settings: Settings, caplog: pytest.LogCaptureFixture
):
    llm = FakeLLM(REPLY)

    with caplog.at_level(logging.INFO):
        events = await turns.take(llm, "早上好")

    assert events[-1]["type"] == "done"
    assert "# 世界设定" not in system_prompt(llm)
    assert "world.json" in caplog.text and "world sync" in caplog.text


async def test_character_list_gives_each_character_her_unit_its_reading_and_where_it_is_in_the_unit_order(
    make_client: ClientFactory, characters: CharacterConfig
):
    """The data file lists illumination STARS before L'Antica."""
    characters.add("hiori", "風野灯織", "sheet", idol_id=2)
    write_world(characters)
    client = make_client(FakeLLM(), user=None)

    listed = (await client.get("/api/characters")).json()

    assert [(c["id"], c["unit"], c["unit_reading"], c["unit_order"]) for c in listed] == [
        ("mano", "illumination STARS", "いるみねーしょんすたーず", 0),
        ("hiori", "illumination STARS", "いるみねーしょんすたーず", 0),
        ("kogane", "L'Antica", "あんてぃーか", 1),
    ]


async def test_an_idol_whose_unit_the_sources_could_not_agree_on_is_listed_without_one(
    make_client: ClientFactory, characters: CharacterConfig
):
    write_world(characters, idols=[("櫻木真乃", "illumination STARS", "関根瞳"), ("月岡恋鐘", None, "礒部花凜")])
    client = make_client(FakeLLM(), user=None)

    listed = {c["id"]: c for c in (await client.get("/api/characters")).json()}

    assert (listed["kogane"]["unit"], listed["kogane"]["unit_reading"], listed["kogane"]["unit_order"]) == (
        None, None, None,
    )


def test_a_character_who_is_none_of_the_idols_stops_the_backend_from_starting(
    settings: Settings, characters: CharacterConfig
):
    write_world(characters, idols=[("櫻木真乃", "illumination STARS", "関根瞳")])

    with pytest.raises(WorldError, match="kogane.*月岡恋鐘"):
        build_faked(settings, FakeLLM())


async def test_the_offices_clerk_can_be_a_character(settings: Settings, characters: CharacterConfig):
    """She is one of the staff, not of the idols: the backend starts all the same."""
    characters.add("hazuki", "七草はづき", "sheet", speakers=["はづき"])
    write_world(characters)

    await build_faked(settings, FakeLLM()).aclose()


def test_the_clerk_is_told_the_world_setting_without_songs_or_lives_of_her_own(
    characters: CharacterConfig,
):
    """She does not sing or stand on stage, so her prompt has neither section; the songs and
    lives are still work facts she can look up."""
    write_world(characters)
    world = load_world(world_path(characters.path))
    assert world is not None

    prompt = world.prompt("七草はづき", datetime(2026, 10, 1, tzinfo=UTC).date())

    assert prompt.startswith("# 世界设定")
    assert "事務員：七草はづき" in prompt
    assert "# 你的歌" not in prompt and "# 你上过的演出" not in prompt
    assert "想在舞台上唱给他听" not in prompt and "不引用歌词，也不编造歌词" in prompt
    assert "# 你的歌" in world.prompt("櫻木真乃", datetime(2026, 10, 1, tzinfo=UTC).date())


def test_every_character_in_the_repository_is_one_of_the_idols_or_staff_in_its_world_setting():
    path = world_path(REPO_CHARACTERS / "characters.toml")
    world = load_world(path)

    assert world is not None
    world.check_characters(load_characters(REPO_CHARACTERS / "characters.toml"), path)
    assert len(world.units) == 8
