"""`eidolon eval rerank`: the calibration set's questions are run through the same
reference sources, candidates and reranking as a turn (the story, the character's facts
and the work facts together), and the scores the reranker gives what is labelled relevant
and what is not are laid side by side, against the model's thresholds."""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from eidolon.characters import load_characters
from eidolon.corpus import Episode, Line
from eidolon.data_access import Database
from eidolon.rerank import RerankThresholds
from eidolon.rerank_eval import (
    CalibrationError,
    evaluate,
    load_calibration,
    report,
)
from eidolon.retrieval import open_reference_sources
from eidolon.world import World, load_world, world_path
from tests.conftest import Admin, CharacterConfig
from tests.fakes import FakeEmbedder, KeywordReranker
from tests.test_world_setting import write_world

REPO = Path(__file__).parents[2]

PARK = Episode(
    "produce_events/100100101", "培育剧情", "W.I.N.G.編", "公園の歌声", frozenset({"mano"}),
    (Line("真乃", "公園の鳩が好きです"),),
)
FINAL = Episode(
    "produce_events/100100109", "培育剧情", "W.I.N.G.編", "決勝", frozenset({"mano"}),
    (Line("真乃", "優勝しました"), Line("真乃", "写真も撮りました")),
)
FACTS = "# 鸟\n## ピーちゃん\n家里养的鸟，会说话。\n\n# 档案\n生日是4月25日。\n"

CALIBRATION = """
[[memories]]
key = "cat"
source = "mano"
text = "制作人养了一只叫小白的猫"

[[memories]]
key = "trip"
source = "kogane"
text = "制作人下个月去北海道出差"

[[cases]]
id = "park"
character = "mano"
kind = "story"
query = "公園の鳩のこと覚えてる？"
relevant = ["produce_events/100100101", "produce_events/100100999"]
acceptable = ["鸟 / ピーちゃん"]

[[cases]]
id = "smalltalk"
character = "mano"
kind = "story"
query = "今天天气真好"
relevant = []

[[cases]]
id = "cat"
character = "kogane"
kind = "memory"
query = "我家的猫最近胖了"
relevant = ["cat"]
"""


HIKARI = "歌曲「ヒカリのdestination」"
WING_02 = "唱片《THE IDOLM@STER SHINY COLORS BRILLI@NT WING 02 ヒカリのdestination》（LACM-14782）"
WING_A = "唱片《THE IDOLM@STER SHINY COLORS WING COLLECTION -A side-》（LACA-9946～7）"

WORK_CASE = f"""
[[cases]]
id = "hikari"
character = "mano"
kind = "work"
query = "ヒカリのdestinationって誰が歌ってる？"
relevant = ["{HIKARI}"]
acceptable = ["{WING_02}"]
"""


@pytest.fixture
def world(tmp_path: Path):
    config = CharacterConfig(tmp_path / "characters")
    config.add("mano", "櫻木真乃", "sheet", speakers=["真乃"], facts=FACTS)
    config.add("kogane", "月岡恋鐘", "sheet", speakers=["恋鐘"])
    calibration = tmp_path / "calibration.toml"
    calibration.write_text(CALIBRATION, encoding="utf-8")
    characters = load_characters(config.path)
    db = Database(tmp_path / "eidolon.db")
    yield characters, calibration, open_reference_sources(
        db, FakeEmbedder(), characters.values(), None
    )
    asyncio.run(db.close())


async def test_each_candidate_is_scored_and_labelled(world):
    characters, calibration, sources = world
    await sources.corpus.save([PARK, FINAL])
    reranker = KeywordReranker({"鳩": 0.9, "ピーちゃん": 0.4, "猫": 0.02})

    park, smalltalk, cat = await evaluate(
        load_calibration(calibration), characters, reranker, sources
    )

    assert {(s.label, s.score, s.verdict) for s in park.scored} == {
        ("produce_events/100100101", 0.9, "relevant"),
        ("produce_events/100100109", 0.05, "irrelevant"),
        ("鸟 / ピーちゃん", 0.4, "acceptable"),
        ("档案", 0.05, "irrelevant"),
    }
    # A labelled episode the vector search never offered is a recall problem.
    assert park.not_retrieved == ["produce_events/100100999"]
    assert all(s.verdict == "irrelevant" for s in smalltalk.scored)
    # Memories are shared: Kogane finds the one told to Mano.
    assert {(s.label, s.verdict) for s in cat.scored} == {
        ("cat", "relevant"), ("trip", "irrelevant")
    }


@pytest.fixture
def work(world, tmp_path: Path):
    """`write_world`'s data file, and the reference sources with its work facts."""
    characters, _, sources = world
    config = CharacterConfig(tmp_path / "characters")
    write_world(config)
    data = load_world(world_path(config.path))
    assert data is not None
    db = Database(tmp_path / "eidolon.db")
    yield data, open_reference_sources(db, sources.embedder, characters.values(), data)
    asyncio.run(db.close())


def with_work_case(calibration: Path, data: World):
    calibration.write_text(CALIBRATION + WORK_CASE, encoding="utf-8")
    return load_calibration(calibration, data)


async def test_work_facts_are_labelled_by_their_titles(world, work):
    characters, calibration, sources = world
    data, sources = work
    await sources.corpus.save([PARK, FINAL])
    reranker = KeywordReranker({HIKARI: 0.8, "唱片《THE IDOLM@STER SHINY COLORS BRILLI@NT WING 02": 0.5})

    *_, hikari = await evaluate(
        with_work_case(calibration, data), characters, reranker, sources
    )

    verdicts = {s.label: (s.score, s.verdict) for s in hikari.scored}
    assert verdicts[HIKARI] == (0.8, "relevant")
    assert verdicts[WING_02] == (0.5, "acceptable")
    assert verdicts[WING_A] == (0.05, "irrelevant")
    assert hikari.not_retrieved == []


async def test_an_episode_scores_as_its_best_passage(world):
    characters, calibration, sources = world
    long_final = Episode(
        FINAL.id, FINAL.category, FINAL.source, FINAL.title, FINAL.cast,
        tuple(Line("真乃", "あ" * 700) for _ in range(3)) + (Line("真乃", "鳩"),),
    )
    await sources.corpus.save([long_final])
    reranker = KeywordReranker({"鳩": 0.7})

    [park, *_] = await evaluate(
        load_calibration(calibration), characters, reranker, sources
    )

    [final] = [s for s in park.scored if s.label == FINAL.id]
    assert final.score == 0.7


async def test_the_report_sets_relevant_against_irrelevant_at_the_thresholds(world):
    characters, calibration, sources = world
    await sources.corpus.save([PARK, FINAL])
    reranker = KeywordReranker({"鳩": 0.9, "ピーちゃん": 0.4, "猫": 0.02})
    results = await evaluate(
        load_calibration(calibration), characters, reranker, sources
    )

    text = report(results, "fake-reranker", RerankThresholds(references=0.3, memories=0.01))

    assert "fake-reranker" in text
    # References: the park (0.9) found; of six irrelevant, the small talk lets in the
    # park and the bird (this reranker ignores the query).
    assert "阈值 0.3：相关命中 1/1，不相关误放 2/6" in text
    # Memories: the cat (0.02) found, the trip (0.05) let in.
    assert "阈值 0.01：相关命中 1/1，不相关误放 1/1" in text
    assert "park：produce_events/100100999" in text


async def test_the_report_puts_work_facts_among_the_references(world, work):
    characters, calibration, sources = world
    data, sources = work
    await sources.corpus.save([PARK, FINAL])
    reranker = KeywordReranker({"鳩": 0.9, HIKARI: 0.8, "猫": 0.02})
    results = await evaluate(
        with_work_case(calibration, data), characters, reranker, sources
    )

    text = report(results, "fake-reranker", RerankThresholds(references=0.3, memories=0.01))

    # The story, the character's facts and the work facts share the references' threshold.
    assert "## 参考资料（3 条问题）" in text
    assert "阈值 0.3：相关命中 2/2，" in text
    assert "## 记忆（1 条问题）" in text
    assert "剧情与角色资料" not in text
    assert "hikari（work）ヒカリのdestinationって誰が歌ってる？" in text
    assert f"  + 0.8000 {HIKARI}" in text


class RecordingReranker(KeywordReranker):
    def __init__(self, scores: dict[str, float]) -> None:
        super().__init__(scores)
        self.queries: list[str] = []

    async def rerank(
        self, query: str, documents: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]:
        self.queries.append(query)
        return await super().rerank(query, documents, top_n)


async def test_a_memory_question_after_a_reply_is_asked_as_a_turn_would(world):
    """A memory question may say what the character replied just before, which a turn
    finds memories with too (`memory.memory_query`); one that does not is asked alone."""
    characters, calibration, sources = world
    calibration.write_text(
        CALIBRATION + """
[[cases]]
id = "cat-pronoun"
character = "mano"
kind = "memory"
previous = "小猫还好吗？"
query = "它昨晚又叫了一夜"
relevant = ["cat"]
""",
        encoding="utf-8",
    )
    reranker = RecordingReranker({"猫": 0.6})

    *_, cat, pronoun = await evaluate(
        load_calibration(calibration), characters, reranker, sources
    )

    assert sources.embedder.queries[-2:] == ["我家的猫最近胖了", "小猫还好吗？\n它昨晚又叫了一夜"]
    assert reranker.queries[-2:] == ["我家的猫最近胖了", "小猫还好吗？\n它昨晚又叫了一夜"]
    assert pronoun.case.previous == "小猫还好吗？"
    assert cat.case.previous is None


async def test_a_reference_question_after_a_reply_is_asked_as_a_turn_would(world, work):
    """A story, character-fact or work-fact question may say what the character replied
    just before, which a turn looks references up with too; one that does not is asked
    alone."""
    characters, calibration, _ = world
    data, sources = work
    await sources.corpus.save([PARK, FINAL])
    calibration.write_text(
        CALIBRATION + WORK_CASE + f"""
[[cases]]
id = "park-tired"
character = "mano"
kind = "story"
previous = "是和鸽子一起在公园唱歌的那天吧"
query = "那时候累吗？"
relevant = ["produce_events/100100101"]

[[cases]]
id = "pichan-pronoun"
character = "mano"
kind = "facts"
previous = "ピーちゃんのこと？"
query = "它会说话吗？"
relevant = ["鸟 / ピーちゃん"]

[[cases]]
id = "hikari-who"
character = "mano"
kind = "work"
previous = "ヒカリのdestination、いい曲ですよね"
query = "那是谁唱的？"
relevant = ["{HIKARI}"]
""",
        encoding="utf-8",
    )
    reranker = RecordingReranker({"鳩": 0.9, HIKARI: 0.8})

    park, _, _, hikari, tired, pronoun, who = await evaluate(
        load_calibration(calibration, data), characters, reranker, sources
    )

    asked = [
        "是和鸽子一起在公园唱歌的那天吧\n那时候累吗？",
        "ピーちゃんのこと？\n它会说话吗？",
        "ヒカリのdestination、いい曲ですよね\n那是谁唱的？",
    ]
    assert reranker.queries[-3:] == asked
    assert sources.embedder.queries[-3:] == asked
    # Without a reply, as before.
    assert reranker.queries[0] == park.case.query
    assert reranker.queries[-4] == hikari.case.query
    assert ("produce_events/100100101", 0.9, "relevant") in [
        (s.label, s.score, s.verdict) for s in tired.scored
    ]
    assert pronoun.case.previous == "ピーちゃんのこと？"
    assert (HIKARI, 0.8, "relevant") in [(s.label, s.score, s.verdict) for s in who.scored]
    text = report([tired], "fake-reranker", RerankThresholds(references=0.3, memories=0.01))
    assert "park-tired（story）那时候累吗？\n  上一句：是和鸽子一起在公园唱歌的那天吧\n" in text


@pytest.mark.parametrize(
    "broken, complaint",
    [
        ('[[cases]]\nid = "x"\ncharacter = "mano"\nkind = "chat"\nquery = "q"\nrelevant = []\n',
         "kind 只能是 story、facts、work、memory，而不是 chat"),
        ('[[cases]]\nid = "x"\ncharacter = "mano"\nkind = "memory"\nquery = "q"\n'
         'relevant = ["dog"]\n', "dog"),
        ('[[cases]]\nid = "x"\ncharacter = "mano"\nkind = "story"\nrelevant = []\n', "query"),
        ("[[cases]\n", "格式有误"),
        ('[[cases]]\nid = "x"\ncharacter = "mano"\nkind = "story"\nquery = "q"\n'
         'previous = 1\nrelevant = []\n', "previous"),
    ],
)
def test_a_broken_calibration_file_is_reported(tmp_path: Path, broken: str, complaint: str):
    path = tmp_path / "calibration.toml"
    path.write_text(broken, encoding="utf-8")

    with pytest.raises(CalibrationError, match=complaint):
        load_calibration(path)


def test_a_work_fact_question_names_only_work_facts_in_the_data_file(
    tmp_path: Path, work
):
    data, _ = work
    path = tmp_path / "calibration.toml"
    path.write_text(
        WORK_CASE.replace("acceptable = [", 'acceptable = ["歌曲「ヒカリのdestinatoin」", '),
        encoding="utf-8",
    )

    with pytest.raises(CalibrationError, match="hikari.*歌曲「ヒカリのdestinatoin」"):
        load_calibration(path, data)
    # Without a data file there are no work facts to name.
    with pytest.raises(CalibrationError, match="hikari.*world.json"):
        load_calibration(path)


def test_any_question_names_only_work_facts_in_the_data_file(tmp_path: Path, work):
    data, _ = work
    path = tmp_path / "calibration.toml"
    # The work facts are reranked with the story, so a story question may name them too.
    path.write_text(
        CALIBRATION.replace('acceptable = ["鸟 / ピーちゃん"]',
                            'acceptable = ["鸟 / ピーちゃん", "歌曲「ヒカリのdestinatoin」"]'),
        encoding="utf-8",
    )

    with pytest.raises(CalibrationError, match="park.*歌曲「ヒカリのdestinatoin」"):
        load_calibration(path, data)
    with pytest.raises(CalibrationError, match="park.*world.json"):
        load_calibration(path)
    path.write_text(
        CALIBRATION.replace('acceptable = ["鸟 / ピーちゃん"]', f'acceptable = ["{HIKARI}"]'),
        encoding="utf-8",
    )
    assert load_calibration(path, data).cases[0].acceptable == (HIKARI,)


def test_the_committed_calibration_set_names_only_facts_the_characters_have():
    config = REPO / "characters" / "characters.toml"
    # Its work-fact questions are checked against the committed data file as it loads.
    calibration = load_calibration(
        REPO / "backend" / "eval" / "rerank-calibration.toml", load_world(world_path(config))
    )
    characters = load_characters(config)

    titles = {f.title for c in characters.values() for f in c.facts}
    named = {
        label
        for case in calibration.cases
        for label in case.relevant + case.acceptable
        if " / " in label and case.kind in ("story", "facts")
    }
    assert named <= titles, named - titles
    assert {case.character for case in calibration.cases} <= characters.keys()


class Closing:
    async def aclose(self) -> None:
        pass


class ClosableEmbedder(FakeEmbedder, Closing):
    pass


class ClosableReranker(KeywordReranker, Closing):
    pass


def test_the_command_needs_the_reranker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-key")
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    calibration = tmp_path / "calibration.toml"
    calibration.write_text(CALIBRATION, encoding="utf-8")
    admin = Admin(tmp_path / "eidolon.db", capsys)

    characters = REPO / "characters" / "characters.toml"

    err = admin.fails("eval", "rerank", "--file", str(calibration), "--characters", str(characters))

    assert "SILICONFLOW_API_KEY" in err


def test_the_command_checks_work_fact_questions_against_the_data_file(
    world, work, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    _, calibration, _ = world
    calibration.write_text(WORK_CASE.replace(HIKARI, "歌曲「ヒカリのdestinatoin」"), encoding="utf-8")
    admin = Admin(tmp_path / "eidolon.db", capsys)

    err = admin.fails(
        "eval", "rerank", "--file", str(calibration),
        "--characters", str(tmp_path / "characters" / "characters.toml"),
    )

    assert "问题 hikari 引用了 world.json 里没有的作品资料 歌曲「ヒカリのdestinatoin」" in err


def test_the_command_prints_the_report_for_the_model_asked_for(
    world, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    _, calibration, sources = world
    asyncio.run(sources.corpus.save([PARK, FINAL]))
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.delenv("RERANK_MIN_RELEVANCE", raising=False)
    monkeypatch.setattr("eidolon.embedding.open_embedder", lambda *_: ClosableEmbedder())
    used: list[str] = []

    def reranker(api_key: str, model: str, base_url: str) -> ClosableReranker:
        used.append(model)
        return ClosableReranker({"鳩": 0.9})

    monkeypatch.setattr("eidolon.rerank.SiliconFlowReranker", reranker)
    admin = Admin(tmp_path / "eidolon.db", capsys)
    config = tmp_path / "characters" / "characters.toml"

    out = admin.run(
        "eval", "rerank", "--model", "Qwen/Qwen3-Reranker-0.6B", "--file", str(calibration),
        "--characters", str(config),
    )

    assert used == ["Qwen/Qwen3-Reranker-0.6B"]
    assert "重排模型：Qwen/Qwen3-Reranker-0.6B" in out
    assert "阈值 0.3：" in out and "阈值 0.01：" in out
    assert "  + 0.9000 produce_events/100100101" in out
