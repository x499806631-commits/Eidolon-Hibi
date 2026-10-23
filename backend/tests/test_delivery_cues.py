"""Delivery cues (ADR-0007): only speech synthesis hears them, and only well-formed ones;
the record keeps them as the model wrote them; nobody sees them."""

import re

import pytest

from eidolon.delivery_cues import spoken_by, voiced, without_cues
from eidolon.reply_format import (
    DIRECTED_FORMAT_INSTRUCTIONS,
    FORMAT_INSTRUCTIONS,
    ReplyParser,
    SegmentDelta,
    SpeechDelta,
)
from tests.conftest import ClientFactory, store_in
from tests.fakes import FakeBackgroundLLM, FakeLLM
from tests.test_call_turns import CallUnderTest, spoken
from tests.test_retrieval_services import MANO, judge_answering, message
from tests.test_text_turn import send_turn, start_conversation


def parse(*chunks: str) -> tuple[list[tuple[int, str, str]], list[tuple[int, str]], ReplyParser]:
    """What a reply streamed in `chunks` shows and voices, and the parser that kept it."""
    parser = ReplyParser()
    events = [e for chunk in chunks for e in parser.feed(chunk)] + parser.finish()
    shown = [(e.segment, e.lang, e.text) for e in events if isinstance(e, SegmentDelta)]
    voiced = [(e.segment, e.text) for e in events if isinstance(e, SpeechDelta)]
    return shown, voiced, parser


def joined(shown: list[tuple[int, str, str]], lang: str = "ja") -> str:
    return "".join(text for _, lang_, text in shown if lang_ == lang)


def test_a_well_formed_cue_is_voiced_but_not_shown_and_kept_as_written():
    shown, voiced, parser = parse("<ja>[shyly] えへへ、[laughing nervously]ありがとう</ja><zh>嘿嘿，谢谢</zh>")

    assert joined(shown) == "えへへ、ありがとう"
    assert "".join(text for _, text in voiced) == "[shyly]えへへ、[laughing nervously]ありがとう"
    assert parser.complete[0].ja == "[shyly] えへへ、[laughing nervously]ありがとう"


def test_cues_written_any_other_way_are_neither_voiced_nor_shown():
    reply = "<ja>(happy)えへへ（小声で）ねえ【嬉しそうに】うん[嬉しそうに]よし（笑）</ja><zh>嘿嘿</zh>"

    shown, voiced, parser = parse(reply)

    assert joined(shown) == "えへへねえうんよし"
    assert "".join(text for _, text in voiced) == "えへへねえうんよし"
    assert parser.complete[0].ja.startswith("(happy)")  # the record keeps what was written


def test_a_cue_split_between_chunks_never_shows_a_piece_of_itself():
    shown, voiced, _ = parse("<ja>ほわっ、[sh", "y", "ly] えへ", "へ</ja>")

    assert shown == [(0, "ja", "ほわっ、"), (0, "ja", " えへ"), (0, "ja", "へ")]
    assert "".join(text for _, text in voiced) == "ほわっ、[shyly] えへへ"


def test_a_bracket_that_never_closes_goes_on_as_written():
    shown, voiced, _ = parse("<ja>ほわっ(", "えへへ</ja><zh>哇（嘿", "</zh>")

    assert joined(shown) == "ほわっ(えへへ"
    assert "".join(text for _, text in voiced) == "ほわっ(えへへ"
    assert joined(shown, "zh") == "哇（嘿"


def test_a_long_bracket_is_part_of_the_line_and_does_not_hold_it_back():
    line = "(" + "あ" * 90

    shown, _, _ = parse(f"<ja>{line}", "</ja>")

    assert shown[0] == (0, "ja", line)


def test_paired_tags_are_voiced_around_their_words_and_not_shown():
    shown, voiced, parser = parse(
        "<ja>これ、<whisper>内", "緒</whisp", "er>だよ。<emphasis>すごく</emphasis></ja><zh><soft>保密</soft>哦</zh>"
    )

    assert joined(shown) == "これ、内緒だよ。すごく"
    assert joined(shown, "zh") == "保密哦"
    # A closing tag goes out with the words it closes on, the line's last too.
    assert "".join(text for _, text in voiced) == (
        "これ、<whisper>内緒</whisper>だよ。<emphasis>すごく</emphasis>"
    )
    assert parser.complete[0].ja == "これ、<whisper>内緒</whisper>だよ。<emphasis>すごく</emphasis>"


def test_tags_written_any_other_way_are_neither_voiced_nor_shown():
    line = "<|speaker:0|>うん<|phoneme_start|>a<|phoneme_end|><french>oui</french><Whisper>ね"

    assert without_cues(line) == voiced(line) == "うんaouiね"


def test_a_directors_direction_is_a_cue():
    line = "[barely holding back a giggle, trying to sound serious] うん"

    assert voiced(line) == line.replace("] ", "]") and without_cues(line) == "うん"


def test_only_drama_3_is_given_paired_tags():
    said = "[softly] <whisper>内緒</whisper>だよ"

    assert spoken_by(said, "drama-3") == said
    assert spoken_by(said, "s2.1-pro-free") == spoken_by(said, "s1") == "[softly] 内緒だよ"


def test_a_cue_with_no_words_after_it_is_not_voiced():
    shown, voiced, parser = parse("<ja>[sighing]</ja><zh>……</zh><ja>うん[laughing]</ja><zh>嗯</zh>")

    assert [(s, lang) for s, lang, _ in shown] == [(0, "zh"), (1, "ja"), (1, "zh")]
    assert voiced == [(1, "うん")]
    assert len(parser.complete) == 2  # a segment all the same


def test_cues_in_the_subtitle_are_not_shown():
    shown, voiced, _ = parse("<ja>うん</ja><zh>[happily] 嗯（开心）</zh>")

    assert joined(shown, "zh") == "嗯"
    assert voiced == [(0, "うん")]


REPLY = ["<ja>[shyly] えへへ、(happy)ありがとう</ja><zh>[happily] 嘿嘿，谢谢</zh>"]


async def test_a_text_turn_shows_no_cues_but_keeps_them_for_the_next_turn(
    make_client: ClientFactory, settings
):
    llm = FakeLLM(REPLY, ["<ja>うん</ja><zh>嗯</zh>"])
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "谢谢你")
    await send_turn(client, conversation_id, "嗯")

    assert [e["type"] for e in events] == ["user", "delta", "delta", "done"]
    assert [e["text"] for e in events if e["type"] == "delta"] == ["えへへ、ありがとう", "嘿嘿，谢谢"]
    done = events[-1]["message"]
    assert (done["ja"], done["zh"], done["segments"]) == (
        "えへへ、ありがとう", "嘿嘿，谢谢", [{"ja": "えへへ、ありがとう", "zh": "嘿嘿，谢谢"}],
    )
    res = await client.get(f"/api/conversations/{conversation_id}/messages")
    assert res.json()[1]["ja"] == "えへへ、ありがとう"
    assert res.json()[1]["segments"] == [{"ja": "えへへ、ありがとう", "zh": "嘿嘿，谢谢"}]
    # The model reads its reply as it wrote it.
    assert [m.content for m in llm.prompts[1] if m.role == "assistant"] == [REPLY[0]]
    async with store_in(settings.database_path) as store:
        assert (await store.list_messages(conversation_id))[1].ja == "[shyly] えへへ、(happy)ありがとう"


async def test_the_conversation_list_shows_the_last_reply_without_cues(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(REPLY))
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "谢谢你")

    res = await client.get("/api/conversations/latest")

    [mano] = [c for c in res.json() if c["conversation_id"] == conversation_id]
    assert (mano["last_message"]["ja"], mano["last_message"]["zh"]) == ("えへへ、ありがとう", "嘿嘿，谢谢")


async def test_a_call_voices_well_formed_cues_and_shows_and_remembers_none(
    make_client: ClientFactory, settings
):
    client = make_client(FakeLLM())
    mano_call = await CallUnderTest.of(settings, client, await start_conversation(client, "mano"))
    memory = FakeBackgroundLLM()
    spoken_text, messages = await mano_call.run(FakeLLM(REPLY), [spoken("谢谢你")], memory=memory)

    assert "".join(spoken_text) == "[shyly]えへへ、ありがとう"
    deltas = [m["text"] for m in messages if m["type"] == "delta"]
    assert deltas == ["えへへ、ありがとう", "嘿嘿，谢谢"]
    [done] = [m for m in messages if m["type"] == "done"]
    assert done["message"]["ja"] == "えへへ、ありがとう"
    assert all("speech-delta" != m["type"] for m in messages)
    [prompt] = memory.prompts
    exchange = "\n".join(m.content for m in prompt)
    assert "嘿嘿，谢谢" in exchange and "[happily]" not in exchange


async def test_jev_reads_earlier_replies_without_cues():
    requests: list[dict] = []

    await judge_answering(requests, 0.1).judge(MANO, [message("character", "[shyly] ほわっ")], "嗯")

    assert requests[0]["state"]["上文"] == ["櫻木真乃：ほわっ"]


@pytest.mark.parametrize(
    ("instructions", "least"),
    [(FORMAT_INSTRUCTIONS, 15), (DIRECTED_FORMAT_INSTRUCTIONS, 14)],
    ids=["plain", "directed"],
)
def test_every_cue_the_format_instructions_show_the_model_is_voiced_and_not_shown(
    instructions: str, least: int
):
    cues = re.findall(r"\[[^\]]*\]", instructions)  # well-formed or not
    tags = re.findall(r"<(?!ja>|zh>)(\w+)>[^<]*</\1>", instructions)
    cues += [f"<{tag}>{{}}</{tag}>" for tag in tags]  # around the words they are for

    assert len(cues) >= least  # the recommended ones at least
    for cue in cues:
        said = cue.format("えへへ") if "{}" in cue else f"{cue}えへへ"
        shown, voiced, _ = parse(f"<ja>{said}</ja><zh>嘿嘿</zh>")
        assert joined(shown) == "えへへ"
        assert "".join(text for _, text in voiced) == said
