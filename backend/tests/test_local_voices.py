"""Local voices (GLOSSARY.md「本地语音」) and reading her messages aloud (GLOSSARY.md「朗读」):
calls and reading each speak with Fish Audio or the deployer's own GPT-SoVITS server, as
the admin console has it, apart; one without a voice there is voiced by Fish Audio where
there is a key. A reading is kept and played again for nothing; another's message is none
of the user's to read. The server holds one pair of weights at a time, switched as the
voice needs."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from eidolon.config import Settings
from eidolon.read_aloud import PER_HOUR
from eidolon.voices import GptSovits, LocalVoicesError, load_local_voices
from tests.browser import Browser
from tests.conftest import ClientFactory
from tests.fakes import DEPLOYER_FISH_KEY, FakeIdentityProvider, FakeLLM, FakeServiceClients, FakeSpeech
from tests.test_live_config import admin_client
from tests.test_reach_outs import record
from tests.test_text_turn import send_turn, start_conversation

TTS = "/api/admin/config/tts"
REPLY = ["<ja>[shyly] おはよう</ja><zh>早上好</zh>"]


async def her_reply(client: httpx.AsyncClient) -> str:
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "早")
    [_, reply] = [m for m in await record(client, conversation) if "role" in m]
    return f"m{reply['id']}"


@pytest.fixture
def admins(settings: Settings) -> Settings:
    return replace(settings, admins=frozenset({("github", "1001")}))


async def test_her_reply_is_read_aloud_once_and_kept(make_client: ClientFactory, speech: FakeSpeech):
    client = make_client(FakeLLM(REPLY))
    key = await her_reply(client)

    first = await client.get(f"/api/voice/{key}")
    again = await client.get(f"/api/voice/{key}")

    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "audio/mpeg"
    assert first.content == again.content == "mp3:[shyly] おはよう".encode()
    assert speech.read == [("fish", "s2.1-pro-free", "mano-voice", "[shyly] おはよう")]
    assert (await client.get("/api/account")).json()["read_aloud"] is True


async def test_anothers_message_and_her_producers_are_not_read(make_client: ClientFactory):
    client = make_client(FakeLLM(REPLY))
    key = await her_reply(client)
    other = make_client(FakeLLM(), user="someone")
    users_own = f"m{int(key[1:]) - 1}"

    assert (await other.get(f"/api/voice/{key}")).status_code == 404
    assert (await client.get(f"/api/voice/{users_own}")).status_code == 404
    assert (await client.get("/api/voice/x1")).status_code == 404


async def test_reading_is_read_with_its_own_engine_falling_back_to_fish_without_her_voice(
    make_client: ClientFactory, speech: FakeSpeech, settings: Settings
):
    speech.local_voices = {"mano"}
    client = make_client(FakeLLM(REPLY, REPLY), settings=replace(
        settings, read_engine="gpt_sovits", gpt_sovits_url="http://127.0.0.1:9880",
    ))
    mano = await her_reply(client)
    kogane = await start_conversation(client, "kogane")
    await send_turn(client, kogane, "早")
    [_, theirs] = [m for m in await record(client, kogane) if "role" in m]

    hers = await client.get(f"/api/voice/{mano}")
    fallback = await client.get(f"/api/voice/m{theirs['id']}")

    assert hers.headers["content-type"] == "audio/wav"
    assert fallback.headers["content-type"] == "audio/mpeg"
    assert [r[0] for r in speech.read] == ["gpt_sovits", "fish"]


async def test_a_user_reads_so_many_an_hour(make_client: ClientFactory, settings: Settings):
    import sqlite3

    client = make_client(FakeLLM(REPLY))
    key = await her_reply(client)
    from tests.test_delete_account import user_id

    user = user_id(settings.database_path, "producer")
    now = "9999-01-01T00:00:00+00:00"  # within the hour, whenever it is
    with sqlite3.connect(settings.database_path) as db:
        db.executemany(
            "INSERT INTO read_aloud (user_id, digest, audio, media_type, created_at) VALUES (?, ?, ?, ?, ?)",
            [(user, f"d{i}", b"x", "audio/mpeg", now) for i in range(PER_HOUR)],
        )

    assert (await client.get(f"/api/voice/{key}")).status_code == 429


async def test_calls_speak_on_gpt_sovits_where_she_has_a_voice_there(
    make_client: ClientFactory, speech: FakeSpeech, browser: Browser, settings: Settings
):
    speech.local_voices = {"mano"}
    client = make_client(FakeLLM(), settings=replace(
        settings, voice_engine="gpt_sovits", gpt_sovits_url="http://192.168.1.5:9880",
    ))

    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.call(client, conversation_id=await start_conversation(client, "kogane"))

    assert [(tts.api_key, tts.model) for tts in speech.ttss] == [
        ("http://192.168.1.5:9880", "gpt-sovits"), (DEPLOYER_FISH_KEY, "s2.1-pro-free"),
    ]


async def test_the_admin_switches_calls_and_reading_apart_and_gpt_sovits_is_tried(
    make_client: ClientFactory, speech: FakeSpeech, admins: Settings,
    github: FakeIdentityProvider,
):
    client = await admin_client(make_client, admins, github, FakeServiceClients())
    speech.gpt_sovits_down = True

    refused = await client.put(TTS, json={"model": "s2.1-pro-free", "read_engine": "gpt_sovits"})
    speech.gpt_sovits_down = False
    kept = await client.put(TTS, json={
        "model": "s2.1-pro-free", "read_engine": "gpt_sovits", "gpt_sovits_url": "http://127.0.0.1:9881",
    })

    assert refused.status_code == 422, refused.text
    assert kept.status_code == 200, kept.text
    shown = kept.json()["tts"]["effective"]
    assert shown["engine"] == "fish" and shown["read_engine"] == "gpt_sovits"
    assert ("gpt_sovits", "http://127.0.0.1:9881") in speech.checked


def test_local_voices_are_read_from_their_file_paths_resolved_beside_it(tmp_path: Path):
    path = tmp_path / "voices.toml"
    path.write_text(
        '[mano]\nref_audio = "mano/ref.wav"\nref_text = "ほわっ"\n'
        'sovits_weights = "C:/w/mano.pth"\n', encoding="utf-8",
    )

    [(who, voice)] = load_local_voices(path).items()

    assert who == "mano" and voice.ref_lang == "ja"
    assert voice.ref_audio == str((tmp_path / "mano" / "ref.wav").resolve())
    assert voice.sovits_weights is not None and voice.gpt_weights is None
    assert load_local_voices(tmp_path / "none.toml") == {}
    path.write_text('[mano]\nref_audio = "a.wav"\n', encoding="utf-8")
    with pytest.raises(LocalVoicesError):
        load_local_voices(path)


def test_in_a_container_paths_are_sent_as_the_server_sees_them(tmp_path: Path):
    path = tmp_path / "voices.toml"
    path.write_text(
        'server_dir = "C:/Users/me/eidolon-sc/local-voices/"\n'
        '[mano]\nref_audio = "mano/ref.wav"\nref_text = "ほわっ"\n'
        'gpt_weights = "D:\\\\GPT-SoVITS\\\\mano.ckpt"\n', encoding="utf-8",
    )

    voice = load_local_voices(path)["mano"]

    assert voice.ref_audio == "C:/Users/me/eidolon-sc/local-voices/mano/ref.wav"
    assert voice.gpt_weights == "D:\\GPT-SoVITS\\mano.ckpt"


async def test_gpt_sovits_switches_weights_only_when_the_voice_needs_others(tmp_path: Path):
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path + ("?" + request.url.query.decode() if request.url.query else ""))
        if request.url.path == "/tts":
            body = json.loads(request.content)
            assert body["text"] == "おはよう" and body["streaming_mode"] is True
            return httpx.Response(200, content=b"RIFF" + b"\0" * 40 + b"\1\2")
        return httpx.Response(200, json={"message": "success"})

    path = tmp_path / "voices.toml"
    path.write_text(
        '[mano]\nref_audio = "/r/mano.wav"\nref_text = "a"\nsovits_weights = "/w/mano.pth"\n'
        '[kogane]\nref_audio = "/r/kogane.wav"\nref_text = "b"\n', encoding="utf-8",
    )
    server = GptSovits(load_local_voices(path), httpx.MockTransport(answer))

    await server.read("http://h:9880", "mano", "[shyly] おはよう")
    await server.read("http://h:9880", "mano", "おはよう")
    await server.read("http://h:9880", "kogane", "おはよう")

    assert asked == [
        "/set_sovits_weights?weights_path=%2Fw%2Fmano.pth", "/tts", "/tts", "/tts",
    ]


async def test_a_call_on_gpt_sovits_announces_each_segment_ahead_of_its_audio(tmp_path: Path):
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        OutputTransportMessageFrame,
        TTSAudioRawFrame,
    )
    from pipecat.tests.utils import SleepFrame, run_test

    from eidolon.call import GptSovitsTTS, SegmentCompleteFrame, SegmentTextFrame

    said: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["text"]
        said.append(text)
        header = b"RIFF" + b"\0" * 20 + (24000).to_bytes(4, "little") + b"\0" * 12
        return httpx.Response(200, content=header + b"\1\0" * 240)

    path = tmp_path / "voices.toml"
    path.write_text('[mano]\nref_audio = "/r.wav"\nref_text = "a"\n', encoding="utf-8")
    server = GptSovits(load_local_voices(path), httpx.MockTransport(answer))
    frames = [
        LLMFullResponseStartFrame(), SegmentTextFrame("[shyly] ほわっ。", 1, 0),
        SegmentCompleteFrame(1, 0), SegmentTextFrame("今日も。", 1, 1),
        SegmentCompleteFrame(1, 1), LLMFullResponseEndFrame(),
    ]
    spaced = [f for frame in frames for f in (frame, SleepFrame(0.05))]

    down, _ = await run_test(
        GptSovitsTTS(server, "http://h:9880", "mano", sample_rate=24000),
        frames_to_send=spaced,
    )

    order = [
        f"playing {f.message['data']['segment']}" if isinstance(f, OutputTransportMessageFrame)
        else "audio" for f in down
        if isinstance(f, OutputTransportMessageFrame | TTSAudioRawFrame)
    ]
    assert said == ["ほわっ。", "今日も。"]
    assert order[0] == "playing 0" and "playing 1" in order
    assert order.index("playing 1") > order.index("audio")


async def test_the_deployer_switches_voices_from_their_own_settings(
    make_client: ClientFactory, speech: FakeSpeech, settings: Settings
):
    speech.local_voices = {"mano"}
    client = make_client(FakeLLM())

    before = (await client.get("/api/voice-settings")).json()
    saved = await client.put("/api/voice-settings", json={
        "engine": "fish", "model": "drama-3", "read_engine": "fish", "read_model": "s2.1-pro",
    })
    elsewhere = make_client(FakeLLM(), settings=replace(settings, dev_login=False))

    assert before["read_engine"] == "same" and before["fish_key"] is True
    assert before["local_voices"] == ["mano"]
    assert saved.status_code == 200, saved.text
    assert saved.json()["read_model"] == "s2.1-pro" and saved.json()["model"] == "drama-3"
    assert (await elsewhere.get("/api/voice-settings")).status_code == 403
