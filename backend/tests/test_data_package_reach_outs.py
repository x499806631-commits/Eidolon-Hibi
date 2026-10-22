"""Reach-outs in data packages (GLOSSARY.md「搭话」「数据包」): what she said first in a
conversation goes into a package with it, as the model wrote it, delivery cues and all, and
comes out of it into another deployment as it was, seen. Driven through the API, reach-outs
made as `test_reach_outs.py` makes them."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from eidolon import data_package
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM, FakeRandom
from tests.test_data_package import export, import_package, unzipped
from tests.test_data_package_conversations import records
from tests.test_past_conversations import hers
from tests.test_reach_outs import (
    MIDDLE,
    REACH_OUT,
    backend_of,
    last_contact,
    reach_outs,
    record,
    settings,  # noqa: F401  reach-outs on in `.env`
    talked,
)
from tests.test_unseen_reach_outs import unseen

PACKAGED_REACH_OUT = {
    "ja": "[happily] プロデューサーさん！レッスン終わったよ",
    "zh": "制作人！练习结束了哦",
    "segments": [
        {"ja": "[happily] プロデューサーさん！", "zh": "制作人！"},
        {"ja": "レッスン終わったよ", "zh": "练习结束了哦"},
    ],
}
"""`REACH_OUT` as a package keeps it, but for its time."""


async def test_her_reach_outs_move_to_another_deployment_as_they_were_and_seen(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    here, _, clock, conversation = await talked(
        make_client, REACH_OUT, random=FakeRandom(MIDDLE)
    )
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=28))
    await backend_of(here).reach_outs.run_due()
    assert await unseen(here) == {"mano": True}
    there = make_client(
        FakeLLM(), settings=replace(settings, database_path=tmp_path / "there.db")
    )

    package = await export(here, "conversations")
    _, manifest, data = unzipped(package)
    assert manifest["format"] == data_package.FORMAT
    [packaged] = data["conversations"][0]["reach_outs"]
    assert packaged == PACKAGED_REACH_OUT | {"created_at": packaged["created_at"]}
    res = await import_package(there, package, "conversations")

    assert res.status_code == 200, res.text
    assert res.json()["conversations"] == 1
    assert await unseen(there) == {"mano": False}
    assert await records(there) == await records(here)
    assert len(reach_outs(await record(there, (await hers(there))[0]["id"]))) == 1
    _, _, again = unzipped(await export(there, "conversations"))
    assert again["conversations"][0]["reach_outs"] == [packaged]

