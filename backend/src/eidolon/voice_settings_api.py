"""The deployment's speech synthesis in the user's own settings (GLOSSARY.md「朗读」「本地语音」):
what speaks in calls and what reads her messages aloud, the Fish Audio models, and where the
deployer's GPT-SoVITS server is; the same as the admin console's「语音合成」, without the key.

Only for whoever may change the deployment (`may_change`): an admin, or anyone where the
development sign-in is on, which is never a public deployment and is the deployer's own
computer. `GET /api/voice-settings` is what is in effect, whether there is a Fish Audio key and
who has a local voice; `PUT` changes it as the admin console would, tried first (422 with why
it was not kept). 403 for anyone else."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from eidolon.accounts import is_admin
from eidolon.assembly import Backend
from eidolon.config import FishAudioModel, ReadEngine, ReadModel, VoiceEngine
from eidolon.live_config import TTS, ConfigInvalid, ConfigRejected
from eidolon.sessions import UserId
from eidolon.voices import DEFAULT_GPT_SOVITS_URL


class VoiceSettings(BaseModel):
    engine: VoiceEngine = "fish"
    model: FishAudioModel
    read_engine: ReadEngine = "same"
    read_model: ReadModel = "same"
    gpt_sovits_url: str = Field(
        DEFAULT_GPT_SOVITS_URL, max_length=200, pattern=r"^https?://[\x21-\x7e]+$"
    )


async def may_change(backend: Backend, user_id: str) -> bool:
    """Whether the user may change the deployment's speech synthesis from their settings."""
    return backend.settings.dev_login or await is_admin(backend.store, backend.settings, user_id)


def add_voice_settings_routes(app: FastAPI, backend: Backend) -> None:
    config = backend.config

    def shown() -> dict:
        tts = config.effective(TTS)
        return {
            "engine": tts.get("engine") or "fish",
            "model": tts["model"],
            "read_engine": tts.get("read_engine") or "same",
            "read_model": tts.get("read_model") or "same",
            "gpt_sovits_url": tts.get("gpt_sovits_url") or DEFAULT_GPT_SOVITS_URL,
            "fish_key": bool(tts["api_key"]),
            "local_voices": sorted(
                c for c in backend.characters if backend.read_aloud.has_local_voice(c)
            ),
        }

    async def allowed(user_id: str) -> None:
        if not await may_change(backend, user_id):
            raise HTTPException(403, "只有部署者能改语音合成")

    @app.get("/api/voice-settings")
    async def read(user_id: UserId) -> dict:
        await allowed(user_id)
        return shown()

    @app.put("/api/voice-settings")
    async def change(user_id: UserId, body: VoiceSettings) -> dict:
        await allowed(user_id)
        try:
            await config.save(TTS, body.model_dump())
        except ConfigInvalid as e:
            raise HTTPException(422, str(e)) from None
        except ConfigRejected as e:
            raise HTTPException(422, f"试过了，没有保存：{e}") from None
        return shown()
