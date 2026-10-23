"""Drafting a character sheet from the story corpus with the background model, checked by
what the model was shown and the sheet file written."""

import re
from pathlib import Path

import pytest

from eidolon.characters import DRAFT_MARKER, Character, load_characters
from eidolon.corpus import Episode, Line, StoryCorpus
from eidolon.sheet_draft import MAX_BATCHES, SECTIONS, NoEpisodes, SheetReviewed, draft_sheet
from eidolon.story_import import import_csv_directory
from tests.conftest import KOGANE_SHEET, Admin, CharacterConfig
from tests.fakes import FakeEmbedder, FakeLLM
from tests.stories import corpus_in

SHEET = """\
## 身份
你是櫻木真乃。
## 自称
わたし
## 对他人的称呼
プロデューサーさん
## 口癖
ほわっ
## 与组合伙伴的关系
灯織ちゃん、めぐるちゃん，一起发光的好朋友。
## 硬性规则
始终以真乃的身份说话。
"""


@pytest.fixture
def characters(tmp_path: Path) -> CharacterConfig:
    """Mano has no sheet yet; Kogane's has been written and reviewed by hand."""
    return (
        CharacterConfig(tmp_path / "characters")
        .add("mano", "櫻木真乃", None, speakers=["真乃"])
        .add("kogane", "月岡恋鐘", KOGANE_SHEET, speakers=["恋鐘"])
    )


@pytest.fixture
async def corpus(tmp_path: Path, characters: CharacterConfig, story: Path):
    async with corpus_in(tmp_path / "eidolon.db", FakeEmbedder()) as corpus:
        await import_csv_directory(story, corpus, drafting(characters).values())
        yield corpus


def drafting(characters: CharacterConfig) -> dict[str, Character]:
    """The characters as the draft command reads them: sheets may not exist yet."""
    return load_characters(characters.path, require_sheets=False)


def material(llm: FakeLLM) -> str:
    return "\n".join(m.content for prompt in llm.prompts for m in prompt)


async def test_the_draft_is_written_only_from_episodes_the_character_is_cast_in(
    corpus: StoryCorpus, characters: CharacterConfig
):
    llm = FakeLLM([SHEET])

    await draft_sheet(drafting(characters)["mano"], corpus, llm)

    shown = material(llm)
    assert "公園で鳩と一緒に歌っていました" in shown  # her W.I.N.G.
    assert "優勝、したんですね" in shown
    assert "長崎のちゃんぽん" in shown  # Kogane's card, where Mano has a line too
    assert "撮影、頑張ります" not in shown  # an event she is not in
    assert "負けちゃいました" not in shown  # a failure ending, never imported


async def test_another_characters_draft_leaves_out_episodes_she_is_not_in(
    corpus: StoryCorpus, characters: CharacterConfig
):
    llm = FakeLLM([SHEET])

    await draft_sheet(drafting(characters)["kogane"], corpus, llm, overwrite=True)

    shown = material(llm)
    assert "長崎のちゃんぽん" in shown
    assert "公園で鳩" not in shown and "優勝" not in shown


async def test_the_draft_has_every_part_of_a_sheet_and_is_marked_as_a_draft(
    corpus: StoryCorpus, characters: CharacterConfig
):
    # The model forgets two sections.
    llm = FakeLLM(["## 身份\n你是櫻木真乃。\n## 自称\nわたし\n## 口癖\nほわっ\n## 硬性规则\n不提到自己是 AI。"])

    await draft_sheet(drafting(characters)["mano"], corpus, llm)

    written = (characters.directory / "sheets/mano.md").read_text(encoding="utf-8")
    assert written.startswith(DRAFT_MARKER)
    for section in SECTIONS:
        assert f"\n## {section}\n" in written
    assert "ほわっ" in written
    # The sheet works as is; the marker stays out of the prompt.
    mano = load_characters(characters.path)["mano"]
    assert DRAFT_MARKER not in mano.sheet and "你是櫻木真乃。" in mano.sheet
    assert "## 与组合伙伴的关系" in mano.sheet


async def test_the_draft_asks_for_her_relations_with_her_partners_not_a_member_list(
    corpus: StoryCorpus, characters: CharacterConfig
):
    # Who is in which unit comes from the world setting; a sheet listing them again
    # could disagree with it.
    # Taking notes first, so both kinds of instructions are seen.
    llm = FakeLLM(["- 自称わたし"], ["- 口癖ほわっ"], ["- 喜欢鸽子"], [SHEET])

    await draft_sheet(drafting(characters)["mano"], corpus, llm, budget=180)

    instructions = [prompt[0].content for prompt in llm.prompts]
    draft = instructions[-1]
    assert "## 与组合伙伴的关系" in draft
    assert "组合成员" not in draft
    assert "不要列成员名单" in draft
    assert not any("所属组合和" in i or "其他成员" in i for i in instructions)
    assert all("组合伙伴" in i for i in instructions)
    written = (characters.directory / "sheets/mano.md").read_text(encoding="utf-8")
    assert "## 组合成员" not in written and "（待补充）" not in written


async def test_a_reviewed_sheet_is_not_overwritten_unless_asked(
    corpus: StoryCorpus, characters: CharacterConfig
):
    llm = FakeLLM([SHEET])
    kogane = drafting(characters)["kogane"]

    with pytest.raises(SheetReviewed):
        await draft_sheet(kogane, corpus, llm)

    assert (characters.directory / "sheets/kogane.md").read_text(encoding="utf-8") == KOGANE_SHEET
    assert llm.prompts == []  # nothing was spent on it

    await draft_sheet(kogane, corpus, llm, overwrite=True)

    written = (characters.directory / "sheets/kogane.md").read_text(encoding="utf-8")
    assert written.startswith(DRAFT_MARKER) and "## 口癖" in written


async def test_an_unreviewed_draft_is_replaced_by_a_new_one(
    corpus: StoryCorpus, characters: CharacterConfig
):
    llm = FakeLLM(["## 身份\n第一稿"], ["## 身份\n第二稿"])
    await draft_sheet(drafting(characters)["mano"], corpus, llm)

    await draft_sheet(drafting(characters)["mano"], corpus, llm)

    written = (characters.directory / "sheets/mano.md").read_text(encoding="utf-8")
    assert "第二稿" in written and "第一稿" not in written


async def test_material_beyond_one_request_is_taken_notes_on_in_batches(
    corpus: StoryCorpus, characters: CharacterConfig
):
    # Each of Mano's three episodes fits in a request, but not all of them together.
    llm = FakeLLM(["- 自称わたし"], ["- 口癖ほわっ"], ["- 喜欢鸽子"], [SHEET])

    report = await draft_sheet(drafting(characters)["mano"], corpus, llm, budget=180)

    assert report.requests == len(llm.prompts) == 4
    assert all(len(prompt[1].content) <= 180 for prompt in llm.prompts)
    *notes, draft = llm.prompts
    assert "公園で鳩" in "".join(p[1].content for p in notes)
    assert all(n in draft[1].content for n in ("自称わたし", "口癖ほわっ", "喜欢鸽子"))
    assert "公園で鳩" not in draft[1].content
    assert "## 身份" in (characters.directory / "sheets/mano.md").read_text(encoding="utf-8")


async def test_too_many_episodes_are_sampled_evenly(
    corpus: StoryCorpus, characters: CharacterConfig
):
    many = [
        Episode(f"produce_events/3001001{i:04d}", "S卡剧情", "卡", f"第{i}話",
                frozenset({"mano"}), (Line("真乃", f"エピソード{i:04d}です" + "。" * 80),))
        for i in range(200)
    ]
    await corpus.save(many)
    budget = 400
    llm = FakeLLM(*[["- 要点"]] * 100, [SHEET])

    report = await draft_sheet(drafting(characters)["mano"], corpus, llm, budget=budget)

    assert report.episodes_cast == 203
    assert report.episodes_read < report.episodes_cast
    read = {int(n) for p in llm.prompts for n in re.findall(r"エピソード(\d{4})", p[1].content)}
    # Spread over the whole range, not just its start.
    assert len(read) < 200 and min(read) < 10 and max(read) > 190
    notes_on_episodes = [p for p in llm.prompts if "一部分剧情" in p[0].content]
    assert 1 < len(notes_on_episodes) <= MAX_BATCHES


async def test_a_character_in_no_episode_has_nothing_to_draft_from(
    corpus: StoryCorpus, characters: CharacterConfig
):
    characters.add("hiori", "風野灯織", None)
    llm = FakeLLM()

    with pytest.raises(NoEpisodes):
        await draft_sheet(drafting(characters)["hiori"], corpus, llm)

    assert not (characters.directory / "sheets/hiori.md").exists()


def test_the_command_will_not_overwrite_a_reviewed_sheet(
    admin: Admin, characters: CharacterConfig
):
    err = admin.fails("character", "draft-sheet", "kogane", "--characters", str(characters.path))

    assert "--force" in err
    assert (characters.directory / "sheets/kogane.md").read_text(encoding="utf-8") == KOGANE_SHEET


def test_the_command_reports_an_unknown_character(admin: Admin, characters: CharacterConfig):
    err = admin.fails("character", "draft-sheet", "hiori", "--characters", str(characters.path))

    assert "hiori" in err


def test_the_command_needs_the_background_model(
    admin: Admin, characters: CharacterConfig, monkeypatch: pytest.MonkeyPatch
):
    for name in ("CHAT_LLM_BASE_URL", "CHAT_LLM_API_KEY", "CHAT_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.replace("CHAT", "BACKGROUND"), raising=False)

    err = admin.fails("character", "draft-sheet", "mano", "--characters", str(characters.path))

    assert "BACKGROUND_LLM" in err


async def test_the_draft_can_go_elsewhere_leaving_the_sheet_alone(
    corpus: StoryCorpus, characters: CharacterConfig, tmp_path: Path
):
    # As in the container, where the character configuration is read-only.
    llm = FakeLLM([SHEET])
    output = tmp_path / "drafts/kogane.md"

    await draft_sheet(drafting(characters)["kogane"], corpus, llm, output=output)

    assert output.read_text(encoding="utf-8").startswith(DRAFT_MARKER)
    assert (characters.directory / "sheets/kogane.md").read_text(encoding="utf-8") == KOGANE_SHEET


async def test_a_long_episode_is_split_rather_than_cut_short(
    corpus: StoryCorpus, characters: CharacterConfig
):
    lines = tuple(Line("真乃", f"第{i:03d}句のセリフです。") for i in range(100))
    await corpus.save([Episode("produce_events/300100101", "S卡剧情", "长卡", "第1話",
                               frozenset({"mano"}), lines)])
    llm = FakeLLM(*[["- 要点"]] * 30, [SHEET])

    await draft_sheet(drafting(characters)["mano"], corpus, llm, budget=300)

    read = "".join(p[1].content for p in llm.prompts)
    assert "第000句" in read and "第099句" in read
    assert all(len(p[1].content) <= 300 for p in llm.prompts)


async def test_sections_the_model_titled_a_little_differently_are_not_repeated(
    corpus: StoryCorpus, characters: CharacterConfig
):
    llm = FakeLLM([SHEET.replace("## 身份", "## 身份：").replace("## 口癖", "### 口癖")])

    await draft_sheet(drafting(characters)["mano"], corpus, llm)

    written = (characters.directory / "sheets/mano.md").read_text(encoding="utf-8")
    assert "（待补充）" not in written


def test_the_command_will_not_spend_anything_on_a_draft_it_cannot_write(
    admin: Admin, characters: CharacterConfig, tmp_path: Path
):
    blocked = tmp_path / "a-file"
    blocked.write_text("")

    err = admin.fails(
        "character", "draft-sheet", "mano", "--characters", str(characters.path),
        "--output", str(blocked / "mano.md"),
    )

    assert "无法写入" in err
