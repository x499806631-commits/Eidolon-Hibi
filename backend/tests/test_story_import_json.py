"""Importing the game's own script JSON into the story corpus, alongside SCTranslationData
CSVs (the spec's cut point B), checked by querying the corpus afterwards."""

from pathlib import Path

import pytest

from eidolon.characters import load_characters
from eidolon.corpus import StoryCorpus
from eidolon.data_access import Database
from eidolon.story_import import ImportReport, import_csv_directory, import_json_directory
from tests.conftest import CharacterConfig
from tests.fakes import FakeEmbedder
from tests.stories import (
    KNOCKED_OUT_SCRIPT,
    KOGANE_SCRIPT,
    PARK_SCRIPT,
    WON_SCRIPT,
    write_script,
)


class Importer:
    def __init__(self, db: Database, characters: CharacterConfig) -> None:
        self.embedder = FakeEmbedder()
        self.corpus = StoryCorpus(db, self.embedder)
        self._characters = characters

    async def csv(self, directory: Path) -> ImportReport:
        characters = load_characters(self._characters.path)
        return await import_csv_directory(directory, self.corpus, characters.values())

    async def json(self, directory: Path) -> ImportReport:
        characters = load_characters(self._characters.path)
        return await import_json_directory(directory, self.corpus, characters.values())


@pytest.fixture
async def importer(tmp_path: Path, characters: CharacterConfig):
    db = Database(tmp_path / "eidolon.db")
    yield Importer(db, characters)
    await db.close()


@pytest.fixture
def scripts(tmp_path: Path) -> Path:
    """Scripts saved the way the game serves them: `<type>/<number>.json`."""
    root = tmp_path / "scripts"
    write_script(root, "produce_events/100100101.json", PARK_SCRIPT)
    write_script(root, "produce_events/600100106.json", WON_SCRIPT)
    write_script(root, "produce_events/600100102.json", KNOCKED_OUT_SCRIPT)
    write_script(root, "produce_events/201100501.json", KOGANE_SCRIPT)
    return root


async def test_each_script_becomes_an_episode_keyed_by_its_path(
    importer: Importer, scripts: Path
):
    await importer.json(scripts)

    park = await importer.corpus.get("produce_events/100100101")
    assert park is not None
    assert park.category == "培育剧情"
    assert park.cast == {"mano"}
    assert [(line.speaker, line.ja) for line in park.lines][:2] == [
        ("プロデューサー", "（いよいよ俺が、アイドルを\nプロデュースする時が来た）"),
        ("真乃", "公園で鳩と一緒に歌っていました"),
    ]
    # Narration keeps its text without a speaker; frames without text are dropped.
    assert park.lines[-1].speaker is None and park.lines[-1].ja == "（夕日が公園を照らしている）"
    card = await importer.corpus.get("produce_events/201100501")
    assert card is not None
    # A joint line casts both; Kogane's mother is not Kogane.
    assert (card.category, card.cast) == ("P卡剧情", {"kogane", "mano"})


async def test_every_reaction_is_kept_but_not_which_option_the_producer_picked(
    importer: Importer, scripts: Path
):
    await importer.json(scripts)

    park = await importer.corpus.get("produce_events/100100101")
    assert park is not None
    texts = [line.ja for line in park.lines]
    assert "歌が好きなのか？" not in texts and "鳩が好きなのか？" not in texts
    assert texts[-4:-1] == [
        "はい、歌うのが大好きです",
        "ほわっ……鳩さん、かわいいですよね",
        "また、公園に来てくれますか？",
    ]
    assert all("いただきます" not in text for text in importer.embedder.documents)


async def test_json_only_episodes_have_no_translation_but_can_be_recalled(
    importer: Importer, scripts: Path
):
    await importer.json(scripts)

    card = await importer.corpus.get("produce_events/201100501")
    assert card is not None
    assert all(line.zh is None for line in card.lines)
    hits = await importer.corpus.search("ちゃんぽん", "kogane", limit=1)
    assert [p.episode_id for p in hits] == ["produce_events/201100501"]
    [source] = await importer.corpus.catalog("kogane")
    assert source.category == "P卡剧情"
    assert [e.episode_id for e in source.episodes] == ["produce_events/201100501"]


async def test_a_script_named_after_itself_is_filed_under_its_directory(
    importer: Importer, tmp_path: Path
):
    # The name the translation plugin gives a download it has no title for.
    root = tmp_path / "scripts"
    write_script(root, "月岡恋鐘/【ちゃんぽんの日】月岡恋鐘/produce_events_201100501.json", KOGANE_SCRIPT)

    await importer.json(root)

    card = await importer.corpus.get("produce_events/201100501")
    assert card is not None
    assert (card.source, card.title) == ("【ちゃんぽんの日】月岡恋鐘", "produce_events_201100501")


async def test_scripts_saved_the_way_the_game_serves_them_are_grouped_by_card(
    importer: Importer, tmp_path: Path
):
    root = tmp_path / "scripts"
    write_script(root, "produce_events/201100501.json", KOGANE_SCRIPT)
    write_script(root, "produce_events/201100502.json", KOGANE_SCRIPT[:1])

    await importer.json(root)

    [source] = await importer.corpus.catalog("kogane")
    assert [e.episode_id for e in source.episodes] == [
        "produce_events/201100501",
        "produce_events/201100502",
    ]
    assert [e.title for e in source.episodes] == [
        "produce_events_201100501",
        "produce_events_201100502",
    ]


async def test_the_translated_csv_is_kept_over_the_json_of_the_same_script(
    importer: Importer, story: Path, scripts: Path
):
    await importer.csv(story)

    report = await importer.json(scripts)

    won = await importer.corpus.get("produce_events/600100106")
    assert won is not None
    assert won.title == "決勝後コミュ"
    assert won.lines[0].zh == "……优，优胜了呢"
    assert report.csv_versions_kept == 2  # Mano's first episode and her W.I.N.G. win
    ids = [e.id for e in await importer.corpus.list_episodes()]
    assert len(ids) == len(set(ids)) == 5


async def test_importing_the_csv_after_the_json_replaces_it(
    importer: Importer, story: Path, scripts: Path
):
    await importer.json(scripts)

    await importer.csv(story)

    won = await importer.corpus.get("produce_events/600100106")
    assert won is not None
    assert (won.title, won.lines[0].zh) == ("決勝後コミュ", "……优，优胜了呢")
    # Her two CSV episodes, and Kogane's card only the JSON has.
    assert len(await importer.corpus.list_episodes("mano")) == 4
    # And importing the JSON again leaves the CSV version alone.
    await importer.json(scripts)
    won = await importer.corpus.get("produce_events/600100106")
    assert won is not None and won.title == "決勝後コミュ"


async def test_importing_again_updates_in_place_without_duplicates(
    importer: Importer, scripts: Path
):
    await importer.json(scripts)
    embedded_first = len(importer.embedder.documents)

    report = await importer.json(scripts)

    assert len(await importer.corpus.list_episodes()) == 3
    assert report.embedded_passages == 0
    assert len(importer.embedder.documents) == embedded_first

    write_script(
        scripts,
        "produce_events/600100106.json",
        [*WON_SCRIPT, {"speaker": "真乃", "text": "本当にありがとうございましたっ！"}],
    )
    await importer.json(scripts)

    won = await importer.corpus.get("produce_events/600100106")
    assert won is not None and won.lines[-1].ja == "本当にありがとうございましたっ！"
    assert len(await importer.corpus.list_episodes()) == 3


async def test_the_same_script_saved_twice_is_imported_once(importer: Importer, scripts: Path):
    write_script(scripts, "produce_events_100100101.json", PARK_SCRIPT[:3])

    report = await importer.json(scripts)

    assert [e.id for e in await importer.corpus.list_episodes("mano")].count("produce_events/100100101") == 1
    park = await importer.corpus.get("produce_events/100100101")
    assert park is not None and park.lines[-1].ja == "（夕日が公園を照らしている）"
    assert any("produce_events/100100101" in w for w in report.warnings)


async def test_unreadable_or_unrecognised_files_are_skipped_with_a_warning(
    importer: Importer, tmp_path: Path
):
    root = tmp_path / "scripts"
    write_script(root, "produce_events/100100101.json", PARK_SCRIPT)
    (root / "produce_events/100100102.json").write_text("{not json", encoding="utf-8")
    write_script(root, "produce_events/100100103.json", {"frames": PARK_SCRIPT})
    # Neither names the script it is, so neither could be matched with a CSV of it.
    write_script(root, "櫻木真乃/公園の歌声.json", PARK_SCRIPT)
    write_script(root, "櫻木真乃/event_2.json", PARK_SCRIPT)

    report = await importer.json(root)

    assert [e.id for e in await importer.corpus.list_episodes()] == ["produce_events/100100101"]
    assert any("event_2" in w and "来源标识" in w for w in report.warnings)
    assert any("100100102" in w for w in report.warnings)
    assert any("100100103" in w for w in report.warnings)
    assert any("公園の歌声" in w and "来源标识" in w for w in report.warnings)
