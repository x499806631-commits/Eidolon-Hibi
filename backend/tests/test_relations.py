"""The relations table (GLOSSARY.md「关系表」) and word of mouth (GLOSSARY.md「传话」): how the
characters stand with each other, from the source and as the user's story changes it, told
in her prompt; and what happens between the producer and one of them, heard of by those
related to her, with the user's switch for it."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from eidolon.characters import load_characters
from eidolon.config import Settings
from eidolon.relations import canon
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM, context_of, system_of
from tests.test_delete_account import user_id
from tests.test_producer_profile import REPLY
from tests.test_reach_outs import backend_of
from tests.test_text_turn import send_turn, start_conversation

REPO_CHARACTERS = Path(__file__).parents[2] / "characters" / "characters.toml"


def test_how_they_see_each_other_is_read_from_the_sections_of_their_facts_on_each_other():
    seen = canon(load_characters(REPO_CHARACTERS))

    assert "幼驯染" in seen[("madoka", "toru")]
    assert ("mano", "hiori") in seen and ("hiori", "mano") in seen
    assert all(len(said) <= 200 for said in seen.values())
    assert not any(a == b for a, b in seen)


async def test_her_prompt_tells_how_she_sees_the_others_as_set_and_as_the_story_changed_it(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(llm)
    conversation = await start_conversation(client, "mano")
    put = await client.put("/api/relations/mano/kogane", json={"note": "常一起去吃甜点的好朋友。"})
    added = await client.post(
        "/api/relations/kogane/mano/changes", json={"note": "一起给制作人办了生日会"}
    )

    await send_turn(client, conversation, "你好")

    assert (put.status_code, added.status_code) == (204, 204)
    told = system_of(llm.prompts[-1])
    assert "# 你和事务所里其他人的关系" in told
    assert "常一起去吃甜点的好朋友。" in told and "后来：一起给制作人办了生日会" in told
    shown = (await client.get("/api/relations/mano")).json()
    assert [(t["other_id"], t["overridden"]) for t in shown["ties"]] == [("kogane", True)]


async def test_what_happens_with_one_is_heard_of_by_those_related_to_her_only_with_the_switch_on(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY, REPLY)
    client = make_client(llm)
    me = user_id(settings.database_path, "producer")
    relations = backend_of(client).relations
    await relations.change(me, "mano", "kogane", "一起准备过演出", "test")
    now = datetime.now(UTC)
    await relations.tell(me, "kogane", "制作人送了恋钟她喜欢的蛋糕", now - timedelta(hours=2))
    await relations.tell(me, "kogane", "很久以前的事", now - timedelta(days=30))
    conversation = await start_conversation(client, "mano")

    await send_turn(client, conversation, "你好")
    heard = context_of(llm.prompts[-1])
    switched = await client.put("/api/account/features/word_of_mouth", json={"on": False})
    await send_turn(client, conversation, "在吗")
    not_heard = context_of(llm.prompts[-1])

    assert "制作人送了恋钟她喜欢的蛋糕" in heard and "很久以前的事" not in heard
    assert switched.status_code == 204
    assert "制作人送了恋钟她喜欢的蛋糕" not in not_heard


async def test_word_of_mouth_on_she_knows_the_memories_told_to_those_related_to_her_only(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM())
    me = user_id(settings.database_path, "producer")
    relations = backend_of(client).relations
    await relations.change(me, "mano", "kogane", "一起准备过演出", "test")

    on = await relations.shares_with(me, "mano")
    await client.put("/api/account/features/word_of_mouth", json={"on": False})
    off = await relations.shares_with(me, "mano")

    assert on == {"kogane"}
    assert off is None  # 关掉：回到原来的样子，谁说的都知道


async def test_only_the_deployer_reads_and_changes_the_table(
    make_client: ClientFactory, settings: Settings
):
    from dataclasses import replace

    client = make_client(FakeLLM(), settings=replace(settings, dev_login=False, admins=frozenset()))

    assert (await client.get("/api/relations/mano")).status_code in (401, 403)
    assert (await client.put("/api/relations/mano/kogane", json={"note": "x"})).status_code in (401, 403)
