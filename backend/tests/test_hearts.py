"""Her heart (GLOSSARY.md「她的心」): what makes each idol happy and what hurts her, weighed by
the judge model, and how she shows each tier of affinity; for an adult idol, the romance of
the closer tiers, told apart for a male and a female producer; for an idol under age, never
romance, whatever her file says. A broken heart keeps the backend from starting, and the
repository's own hearts load against the repository's characters."""

from dataclasses import replace
from pathlib import Path

import pytest

from eidolon.affinity import affinity_prompt
from eidolon.affinity_judge import LEVEL, QUESTIONS, questions_for
from eidolon.characters import load_characters
from eidolon.config import Settings
from eidolon.hearts import Heart, HeartsError, hearts_path, load_hearts
from tests.conftest import CharacterConfig, ClientFactory
from tests.fakes import level
from tests.test_affinity_prompt import about_now, prompted
from tests.test_affinity_prompt import settings  # noqa: F401  # the fixture: affinity on
from tests.test_retrieval_services import MANO

REPO_CHARACTERS = Path(__file__).parents[2] / "characters" / "characters.toml"

ADULT = Heart(
    19,
    pleased=("被叫名字",),
    hurt=("被说虚伪",),
    tiers={"trust": "平时嘴毒。", "close": "嘴硬地关心。", "intimate": "卸下防备。"},
    romance={
        "close_male": "心动却不承认（男）。",
        "close_female": "心动却不承认（女）。",
        "intimate_male": "承认喜欢（男）。",
        "intimate_female": "承认喜欢（女）。",
    },
)
MINOR = Heart(17, tiers={"close": "撒娇要夸。", "intimate": "说出心事。"})


def test_the_repositorys_hearts_load_and_only_adults_have_romance():
    characters = load_characters(REPO_CHARACTERS, require_sheets=False)

    hearts = load_hearts(hearts_path(REPO_CHARACTERS), characters.values())

    assert set(hearts) >= {"amana", "tenka", "hana", "fuyuko"}
    for id, heart in hearts.items():
        assert heart.pleased and heart.hurt and set(heart.tiers) == {
            "cold", "distant", "trust", "close", "intimate"
        }, id
        assert bool(heart.romance) == heart.adult, id
        if heart.adult:
            assert len(heart.romance) == 4, id


@pytest.mark.parametrize(
    ("file", "said"),
    [
        ('age = 17\n[romance]\nclose_female = "心动"\n', "未满 18 岁"),
        ('age = 19\n[romance]\nnearly = "心动"\n', "不认识"),
        ('age = 19\n[tiers]\nbest = "x"\n', "没有这些档位"),
        ('pleased = ["x"]\n', "age"),
        ('age = 19\npleased = "x"\n', "一列文字"),
        ("age = ", "读不出来"),
    ],
)
def test_a_broken_heart_keeps_the_backend_from_starting(
    characters: CharacterConfig, file: str, said: str
):
    characters.write("hearts/mano.toml", file)

    with pytest.raises(HeartsError, match=said):
        load_hearts(hearts_path(characters.path), [MANO])


def test_a_heart_for_no_character_is_refused(characters: CharacterConfig):
    characters.write("hearts/nobody.toml", "age = 19\n")

    with pytest.raises(HeartsError, match="nobody"):
        load_hearts(hearts_path(characters.path), [MANO])


def test_without_a_heart_the_tiers_are_as_they_were():
    assert affinity_prompt("trust", None) == []
    assert affinity_prompt("close", None) == affinity_prompt("close", None, heart=None)


def test_she_shows_her_tier_as_her_heart_says_trust_too():
    trust = affinity_prompt("trust", None, heart=ADULT)
    cold = affinity_prompt("cold", None, heart=ADULT)  # not in her file: the generic wording

    assert trust[0].startswith("平时嘴毒。")
    assert cold == affinity_prompt("cold", None)


def test_an_adult_idols_closer_tiers_are_romance_by_the_producers_gender():
    close_female = "\n".join(affinity_prompt("close", None, heart=ADULT, female_producer=True))
    intimate_male = "\n".join(affinity_prompt("intimate", None, heart=ADULT))

    assert "心动却不承认（女）。" in close_female and "（男）" not in close_female
    assert "承认喜欢（男）。" in intimate_male and "（女）" not in intimate_male
    for told in (close_female, intimate_male):
        assert "只放宽恋爱这一半" in told and "性方面的要求仍然不接受" in told
        assert "除了恋爱这一处，人设里的硬性规则照旧" in told


def test_an_idol_under_age_is_never_romance_whatever_her_file_says():
    written_anyway = replace(MINOR, romance={"close_female": "心动"})

    for heart in (MINOR, written_anyway):
        told = "\n".join(affinity_prompt("close", None, heart=heart, female_producer=True))
        assert "撒娇要夸。" in told
        assert "心动" not in told and "放宽恋爱" not in told
        assert "不是恋爱" in told
    assert written_anyway.romance_in("intimate", True) is None


def test_the_judge_weighs_her_by_her_heart():
    hearted = replace(MANO, heart=ADULT)

    asked = questions_for(hearted)[LEVEL]["instructions"]

    assert "被叫名字" in asked and "被说虚伪" in asked
    assert asked.startswith(QUESTIONS[LEVEL]["instructions"])
    assert questions_for(MANO) is QUESTIONS  # no heart: as every other


async def test_a_turn_with_her_in_a_close_tier_tells_her_heart(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    characters.write(
        "hearts/mano.toml",
        'age = 16\npleased = ["被夸"]\nhurt = ["被催"]\n[tiers]\nclose = "会小声说喜欢和你聊天。"\n',
    )

    llm, _ = await prompted(make_client, [level(2)], kept=70, settings=settings)

    section = about_now(llm)
    assert "会小声说喜欢和你聊天。" in section and "不是恋爱" in section


def test_the_repositorys_hearts_say_how_each_plays_her_scenes():
    characters = load_characters(REPO_CHARACTERS, require_sheets=False)
    hearts = load_hearts(hearts_path(REPO_CHARACTERS), characters.values())

    for heart in hearts.values():
        assert set(heart.scenes) == {"close", "intimate"}


def test_a_scene_for_no_door_is_refused(characters: CharacterConfig):
    characters.write("hearts/mano.toml", 'age = 16\n[scenes]\ntrust = "x"\n')

    with pytest.raises(HeartsError, match="scenes"):
        load_hearts(hearts_path(characters.path), load_characters(characters.path).values())
