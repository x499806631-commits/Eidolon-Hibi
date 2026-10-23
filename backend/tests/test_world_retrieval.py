"""The work facts (songs, releases and live days), driven through the text turn API:
retrieving references finds them along with the story and the character's facts,
reranked together, under a heading of their own; recalling how something went looks in
them too, for the lives and releases; the recall-story tool does not. No CV is in any of
it."""

import json
import sqlite3
from pathlib import Path

import pytest

from eidolon.characters import load_characters
from eidolon.config import Settings
from eidolon.story_import import import_csv_directory
from tests.conftest import KOGANE_SHEET, MANO_SHEET, CharacterConfig, ClientFactory
from tests.fakes import FakeEmbedder, FakeJudge, FakeLLM, FakeReranker, KeywordReranker
from tests.test_recall_story import ANSWER, recall, tool_results
from tests.test_retrieval import MANO_FACTS, PARK_LINE, REPLY, system_prompt
from tests.stories import corpus_in
from tests.test_text_turn import send_turn, start_conversation
from tests.test_world_setting import CVS, section, write_world

HIKARI = "歌曲「ヒカリのdestination」"
WING_02 = "THE IDOLM@STER SHINY COLORS BRILLI@NT WING 02 ヒカリのdestination"
WING_A = "THE IDOLM@STER SHINY COLORS WING COLLECTION -A side-"
ENTRIES = 9 + 6 + 6
"""Songs, releases and live days in `write_world`'s data file."""


@pytest.fixture
def characters(tmp_path: Path) -> CharacterConfig:
    config = (
        CharacterConfig(tmp_path / "characters")
        .add("mano", "櫻木真乃", MANO_SHEET, speakers=["真乃"], facts=MANO_FACTS)
        .add("kogane", "月岡恋鐘", KOGANE_SHEET, speakers=["恋鐘"])
    )
    write_world(config)
    return config


@pytest.fixture
async def embedder(settings: Settings, story: Path) -> FakeEmbedder:
    """The embedder the sample stories were imported with."""
    embedder = FakeEmbedder()
    async with corpus_in(settings.database_path, embedder) as corpus:
        characters = load_characters(settings.characters_path)
        await import_csv_directory(story, corpus, characters.values())
    return embedder


def entries(system: str) -> list[str]:
    """The work facts under the 作品资料 headings of the prompt."""
    return under(system, "作品资料")


def under(system: str, heading: str) -> list[str]:
    """What the prompt carries under each of its `heading` headings."""
    return [
        part.split("\n\n")[0].rstrip("\n") for part in system.split(f"\n## {heading}\n")[1:]
    ]


async def test_a_song_is_retrieved_with_its_singers_and_releases(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm, reranker = FakeLLM(REPLY), FakeReranker(HIKARI)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=reranker)

    system = await system_prompt(client, llm, "ヒカリのdestinationって誰が歌ってるんだっけ")

    references = section(system, "# 回忆与资料")
    assert "「作品资料」是关于歌曲、唱片和演出的事实" in references
    song = entries(system)[0]
    assert song.startswith(HIKARI)
    assert "演唱：illumination STARS（櫻木真乃、風野灯織）" in song
    assert f"《{WING_02}》（2018年7月4日发售）" in song
    assert f"《{WING_A}》（2023年1月18日发售）" in song
    assert not any(cv in system for cv in CVS)
    # One rerank for the story, her facts and the work facts together.
    [(_, candidates)] = reranker.calls
    assert any(c.startswith(HIKARI) for c in candidates)
    assert any("W.I.N.G.編" in c for c in candidates)
    assert any("風野灯織\nillumination STARS 的伙伴" in c for c in candidates)


async def test_work_facts_and_character_facts_go_under_their_own_headings(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    reranker = KeywordReranker({HIKARI: 0.9, "人物关系 / 風野灯織": 0.8}, rest=0.0)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=reranker)

    system = await system_prompt(client, llm, "ヒカリのdestinationと灯織ちゃんのこと")

    assert under(system, "角色资料") == ["人物关系 / 風野灯織\nillumination STARS 的伙伴，性格认真。"]
    [song] = under(system, "作品资料")
    assert song.startswith(HIKARI)


async def test_a_release_is_retrieved_with_its_date_and_tracks(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker(f"唱片《{WING_A}》")
    )

    system = await system_prompt(client, llm, "WING COLLECTION -A side-の曲目は？")

    release = entries(system)[0]
    assert release.startswith(f"唱片《{WING_A}》")
    assert "发售日：2023年1月18日" in release
    assert "1. ヒカリのdestination\n2. スローモーション" in release
    assert not any(cv in system for cv in CVS)


async def test_a_live_day_is_retrieved_with_its_performers_and_no_cv(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker("2024年3月3日")
    )

    system = await system_prompt(client, llm, "ドームのライブの二日目って誰が出てた？")

    day = entries(system)[0]
    assert day.startswith("演出「THE IDOLM@STER SHINY COLORS 6thLIVE」")
    assert "日期：2024年3月3日" in day and "会场：ベルーナドーム" in day
    # Mano's CV was absent that day: she is not among its performers.
    assert "出演偶像：風野灯織、月岡恋鐘" in day
    assert not any(cv in system for cv in CVS)


async def test_a_live_day_is_retrieved_with_what_was_performed_on_it(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker("2024年3月2日")
    )

    system = await system_prompt(client, llm, "ドームの6thライブのDay1でどんな曲を歌った？")

    day = entries(system)[0]
    assert day.startswith("演出「THE IDOLM@STER SHINY COLORS 6thLIVE」")
    assert "日期：2024年3月2日" in day
    setlist = day.split("曲目单")[1]
    assert "1. Ambitious Eve：全体出演偶像演唱\n" in setlist
    assert "2. Spread the Wings!!（イルミネーションスターズ Ver.）：illumination STARS演唱\n" in setlist
    assert "3. アポイント・シグナル：月岡恋鐘演唱；櫻木真乃伴舞，没有演唱\n" in setlist
    assert "4. 乱舞る (short ver.)：風野灯織伴舞，没有演唱\n" in setlist
    assert "5. Resonance⁺：除櫻木真乃以外的全体出演偶像演唱" in setlist
    assert not any(cv in system for cv in CVS)


async def test_a_cover_at_a_joint_live_is_retrieved_as_another_brand_s_song(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker("2023年2月11日")
    )

    system = await system_prompt(client, llm, "東京ドームのMOIWで何歌ったっけ？")

    day = entries(system)[0]
    assert "标着别的品牌的是翻唱，不是闪耀色彩的歌" in day
    assert "1. ドレミファクトリー！（シンデレラガールズ的歌）：櫻木真乃演唱" in day
    assert "ドレミファクトリー！" not in section(system, "# 你的歌")


async def test_the_work_facts_are_shared_by_every_character(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker(HIKARI))

    system = await system_prompt(client, llm, "ヒカリのdestinationって誰の曲？", "kogane")

    assert entries(system)[0].startswith(HIKARI)


async def test_without_a_data_file_references_go_on_without_work_facts(
    make_client: ClientFactory, embedder: FakeEmbedder, characters: CharacterConfig
):
    (characters.directory / "world.json").unlink()
    llm, reranker = FakeLLM(REPLY), FakeReranker(PARK_LINE)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=reranker)

    system = await system_prompt(client, llm, "公園の鳩とヒカリのdestination")

    assert PARK_LINE in system
    assert "\n## 作品资料\n" not in system
    assert not any(HIKARI in c for _, docs in reranker.calls for c in docs)


async def test_recalling_a_live_brings_its_days_even_when_no_episode_tells_it(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm, reranker = FakeLLM(ANSWER), FakeReranker("ベルーナドーム", others=0.01)
    client = make_client(llm, retrieval_judge=FakeJudge(answer="recall"), embedder=embedder, reranker=reranker)

    system = await system_prompt(client, llm, "ドームでのライブ、どうだった？")

    recalled = section(system, "# 回顾剧情")
    days = entries(recalled)
    assert [d.split("\n")[1] for d in days] == ["日期：2024年3月2日", "日期：2024年3月3日"]
    assert "没有经历过" not in system
    assert not any(cv in system for cv in CVS)
    # The episodes and the work facts are reranked together, once, the query embedded once.
    assert len(reranker.calls) == 1
    assert embedder.queries == ["ドームでのライブ、どうだった？"]


async def test_with_the_reranker_down_recalling_brings_the_nearest_live_or_release_too(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm, reranker = FakeLLM(ANSWER), FakeReranker()
    reranker.down = True
    client = make_client(llm, retrieval_judge=FakeJudge(answer="recall"), embedder=embedder, reranker=reranker)

    system = await system_prompt(client, llm, "公園の鳩とドームのライブ、どうだった？")

    recalled = section(system, "# 回顾剧情")
    assert "培育剧情 / W.I.N.G.編 /「公園の歌声」" in recalled
    [day] = entries(recalled)
    assert "ベルーナドーム" in day


async def test_recalling_brings_lives_and_releases_but_not_songs(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm, reranker = FakeLLM(ANSWER), FakeReranker("ヒカリのdestination", others=0.01)
    client = make_client(llm, retrieval_judge=FakeJudge(answer="recall"), embedder=embedder, reranker=reranker)

    system = await system_prompt(client, llm, "ヒカリのdestinationのCDを出した時のこと、覚えてる？")

    found = entries(section(system, "# 回顾剧情"))
    assert any(e.startswith(f"唱片《{WING_02}》") for e in found)
    assert not any(e.startswith("歌曲") for e in found)
    [(_, candidates)] = reranker.calls
    assert not any(c.startswith("歌曲") for c in candidates)


async def test_recalling_an_episode_still_brings_the_episode(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(ANSWER)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="recall"), embedder=embedder,
        reranker=FakeReranker(PARK_LINE, others=0.01),
    )

    system = await system_prompt(client, llm, "那次在公园唱歌是怎么回事？")

    assert "培育剧情 / W.I.N.G.編 /「公園の歌声」" in system
    assert f"真乃：{PARK_LINE}" in system
    assert "\n## 作品资料\n" not in system


async def test_recalling_nothing_does_not_say_she_never_went_through_it(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(ANSWER)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="recall"), embedder=embedder,
        reranker=FakeReranker("（どこにもない）", others=0.01),
    )

    system = await system_prompt(client, llm, "去年の夏のあのステージ、どうだった？")

    assert "没有想起" in system
    assert "没有经历过" not in system


async def test_the_recall_story_tool_only_looks_in_the_story(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm, reranker = FakeLLM([recall("ドームのライブ")], ANSWER), FakeReranker("ドーム")
    client = make_client(llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=reranker)
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, "ドームのライブのこと、話して")

    [result] = tool_results(llm.prompts[-1])
    assert "ベルーナドーム" not in result and "\n## 作品资料\n" not in result
    assert not any("ドーム" in c for _, docs in reranker.calls for c in docs)


def stored_entries(settings: Settings) -> dict[str, int]:
    with sqlite3.connect(settings.database_path) as db:
        rows = db.execute(
            "SELECT model, count(*) FROM embedded_texts WHERE scope = 'work' GROUP BY model"
        ).fetchall()
    db.close()
    return dict(rows)


async def test_an_edited_data_file_takes_effect_on_restart_embedding_only_what_changed(
    make_client: ClientFactory,
    embedder: FakeEmbedder,
    characters: CharacterConfig,
    settings: Settings,
):
    llm = FakeLLM(REPLY, REPLY)
    first = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker())
    await system_prompt(first, llm, "新しい曲の話をしよう")
    assert stored_entries(settings) == {"fake-embedding": ENTRIES}

    path = characters.directory / "world.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    [forest] = [s for s in data["songs"] if s["title"] == "MAGICAL FOREST"]
    forest["title"] = "MAGICAL FOREST!!"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    before = len(embedder.documents)

    restarted = make_client(
        llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker("MAGICAL FOREST!!")
    )
    system = await system_prompt(restarted, llm, "MAGICAL FOREST!!って誰の曲？")

    [changed] = embedder.documents[before:]
    assert changed.startswith("歌曲「MAGICAL FOREST!!」")
    assert entries(system)[0] == changed
    assert stored_entries(settings) == {"fake-embedding": ENTRIES}  # the old one is gone


async def test_the_work_facts_are_embedded_once_however_many_turns_search_them(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY, REPLY)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker())
    before = len(embedder.documents)

    await system_prompt(client, llm, "ヒカリのdestination")
    await system_prompt(client, llm, "WING COLLECTION", "kogane")

    songs = [d for d in embedder.documents[before:] if d.startswith("歌曲")]
    assert len(songs) == 9
