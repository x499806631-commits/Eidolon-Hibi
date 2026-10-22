"""The weather where the producer is (GLOSSARY.md「天气」): the line she is told, from the sky
code and the temperature Open-Meteo answers; the service asked once in `FRESH` however many
turns ask; the last line said when it cannot be asked, and nothing where it never said one.
Told to her in a turn where `.env` gives the producer's place and not otherwise, the turn
going on all the same where the service fails; and told to the judge model that picks the
ambience of a call."""

from dataclasses import replace
from datetime import UTC, datetime

import httpx

from eidolon.ambience import AmbienceMoment, moment_state
from eidolon.config import Settings, WeatherSettings
from eidolon.weather import FRESH, RETRY, Weather, say

from tests.conftest import ClientFactory
from tests.fakes import FakeLLM
from tests.test_producer_profile import REPLY, system_prompt
from tests.test_retrieval_services import MANO

NOW = datetime(2026, 9, 25, 12, 30, tzinfo=UTC)  # a Friday
SAID = "制作人那边现在下着小雨，12°C。"


def answering(code: int = 61, celsius: float = 11.6, asked: list[httpx.Request] | None = None):
    """A transport answering Open-Meteo with `code` and `celsius`, noting each request."""

    def handle(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append(request)
        return httpx.Response(200, json={"current": {"weather_code": code, "temperature_2m": celsius}})

    return httpx.MockTransport(handle)


def down(status: int = 503) -> httpx.MockTransport:
    """A transport whose service is down, as Open-Meteo is now and then."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    return httpx.MockTransport(handle)


def test_the_line_she_is_told_says_the_sky_and_the_temperature():
    for code, celsius, said in (
        (0, 12.4, "制作人那边现在晴，12°C。"),
        (61, 11.6, "制作人那边现在下着小雨，12°C。"),
        (95, 20.0, "制作人那边现在有雷雨，20°C。"),
    ):
        assert say(code, celsius) == said, code
    assert say(1234, 20.0) is None  # a code no one here knows: not worth telling her wrongly


async def test_the_service_is_asked_once_in_thirty_minutes():
    asked: list[httpx.Request] = []
    clock = [NOW]
    weather = Weather(31.23, 121.47, lambda: clock[0], answering(asked=asked))

    assert await weather.now() == SAID
    assert await weather.now() == SAID  # a second turn in the same half hour asks nothing
    assert len(asked) == 1
    assert asked[0].url.params["latitude"] == "31.23"

    clock[0] = NOW + FRESH
    assert await weather.now() == SAID
    assert len(asked) == 2  # the half hour gone, it asks again

    await weather.aclose()


async def test_a_failed_ask_says_the_last_line_it_had():
    down_now = [False]

    def handle(request: httpx.Request) -> httpx.Response:
        if down_now[0]:
            return httpx.Response(503)
        return httpx.Response(200, json={"current": {"weather_code": 61, "temperature_2m": 11.6}})

    clock = [NOW]
    weather = Weather(31.23, 121.47, lambda: clock[0], httpx.MockTransport(handle))
    assert await weather.now() == SAID

    down_now[0] = True
    clock[0] = NOW + FRESH
    assert await weather.now() == SAID  # the last line said again, rather than nothing

    never = Weather(31.23, 121.47, lambda: clock[0], down())
    assert await never.now() is None  # nothing said before it failed, so nothing to say

    await weather.aclose()
    await never.aclose()


async def test_a_turn_is_told_the_weather_where_the_producer_is(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, clock=lambda: NOW,
        settings=replace(settings, weather=WeatherSettings(31.23, 121.47)),
        weather_transport=answering(),
    )

    prompt = await system_prompt(client, llm)

    assert SAID in prompt


async def test_a_turn_is_told_no_weather_without_a_place(make_client: ClientFactory):
    llm = FakeLLM(REPLY)
    client = make_client(llm, clock=lambda: NOW)

    prompt = await system_prompt(client, llm)

    assert "制作人那边" not in prompt


async def test_a_turn_goes_on_when_the_weather_cannot_be_told(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, clock=lambda: NOW,
        settings=replace(settings, weather=WeatherSettings(31.23, 121.47)),
        weather_transport=down(),
    )

    prompt = await system_prompt(client, llm)

    assert "制作人那边" not in prompt  # told nothing of it
    assert "和你对话的是你的制作人" in prompt  # and the turn was taken all the same


def test_the_judge_picking_the_ambience_is_told_the_weather():
    told = moment_state(AmbienceMoment(MANO, NOW, False, [], SAID))
    untold = moment_state(AmbienceMoment(MANO, NOW, False, []))

    assert told["天气"] == SAID
    assert "天气" not in untold


async def test_once_it_fails_the_service_is_let_be_for_a_while():
    asked: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(503)

    clock = [NOW]
    weather = Weather(31.23, 121.47, lambda: clock[0], httpx.MockTransport(handle))

    assert await weather.now() is None
    assert await weather.now() is None  # the next turn does not wait on it again
    assert len(asked) == 1

    clock[0] = NOW + RETRY
    assert await weather.now() is None
    assert len(asked) == 2  # tried again once the while is up

    await weather.aclose()
