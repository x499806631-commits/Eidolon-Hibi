"""The producer's home (GLOSSARY.md「制作人的家」): the room the user chose and the photo they
framed, the photos sent them, and the sky over there."""

from dataclasses import replace

from eidolon.config import Settings, WeatherSettings
from eidolon.weather import sky_of
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM, FakeImageService
from tests.test_photos import IMAGE
from tests.test_trip_photos import REPLY, SENDS, talked
from tests.test_weather import answering


async def test_a_home_is_an_apartment_with_nothing_framed_until_set_out(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM())

    home = (await client.get("/api/home")).json()
    moved = await client.put("/api/home", json={"room": "mansion"})
    wrong = await client.put("/api/home", json={"room": "castle"})

    assert home == {
        "room": "apartment", "framed_photo": None, "sky": None, "background": None,
        "can_draw": False, "layout": {},
    }
    assert moved.json()["room"] == "mansion"
    assert wrong.status_code == 422
    assert (await client.get("/api/home")).json()["room"] == "mansion"


async def test_a_photo_she_sent_hangs_framed_and_is_in_the_album(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client, settings, SENDS, image=FakeImageService())
    await client.post("/api/trip-photos/send-now", json={"character_id": "mano"})
    other = make_client(FakeLLM(REPLY), user="someone")

    [photo] = (await client.get("/api/album")).json()
    framed = await client.put("/api/home", json={"framed_photo": photo["id"]})
    stolen = await other.put("/api/home", json={"framed_photo": photo["id"]})
    kept_up = await client.put("/api/home", json={"room": "mansion"})
    taken_down = await client.put("/api/home", json={"framed_photo": None})

    assert photo["character_id"] == "mano"
    assert framed.json()["framed_photo"] == photo["id"]
    assert stolen.status_code == 404 and (await other.get("/api/album")).json() == []
    assert kept_up.json()["framed_photo"] == photo["id"]
    assert taken_down.json()["framed_photo"] is None


async def test_the_window_shows_the_sky_where_the_producer_is(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(
        FakeLLM(), settings=replace(settings, weather=WeatherSettings(31.23, 121.47)),
        weather_transport=answering(code=71),
    )

    assert (await client.get("/api/home")).json()["sky"] == "snow"
    assert [sky_of(c) for c in (0, 2, 45, 61, 82, 86, 95, 1234)] == [
        "clear", "cloudy", "fog", "rain", "rain", "snow", "storm", None,
    ]


async def test_a_room_is_drawn_with_no_one_in_it_and_drawn_again_when_asked(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client = make_client(
        FakeLLM(), settings=replace(settings, image=IMAGE), image_transport=image.transport()
    )

    before = (await client.get("/api/home")).json()
    drawn = (await client.post("/api/home/background")).json()
    again = (await client.post("/api/home/background")).json()
    picture = await client.get("/api/home/background/apartment")
    other_room = await client.get("/api/home/background/mansion")

    assert before["can_draw"] is True and before["background"] is None
    assert drawn["background"] == 1 and again["background"] == 2
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/webp"
    assert other_room.status_code == 404
    body = image.requests[0]["body"]
    assert body["input"].startswith("background dataset, no humans, scenery")
    assert "one-room apartment" in body["input"] and "1girl" in body["parameters"]["negative_prompt"]


async def test_nothing_draws_a_room_without_image_generation(make_client: ClientFactory):
    client = make_client(FakeLLM())

    assert (await client.post("/api/home/background")).status_code == 409


async def test_each_room_keeps_where_the_user_set_its_things(make_client: ClientFactory):
    client = make_client(FakeLLM())

    await client.put("/api/home", json={"layout": {"diary": [0.7, 0.6], "sofa": [0.1, 0.1]}})
    set_out = (await client.put("/api/home", json={"layout": {"phone": [1.4, -0.2]}})).json()
    moved = (await client.put("/api/home", json={"room": "mansion"})).json()
    back = (await client.put("/api/home", json={"room": "apartment"})).json()

    assert set_out["layout"] == {"diary": [0.7, 0.6], "phone": [1.0, 0.0]}
    assert moved["layout"] == {}
    assert back["layout"] == set_out["layout"]
