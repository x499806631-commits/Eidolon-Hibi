"""Reading her messages aloud over HTTP (GLOSSARY.md「朗读」): `GET /api/voice/<key>` is her
message `key` (`m<id>`, `r<id>`, as the conversation's record has them) said in her voice,
mp3 or WAV, kept by the browser too. 404 for no such message of hers, 409 where there is no
speech synthesis, 429 past the hour's readings, 502 where the engine did not read it."""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from eidolon.assembly import Backend
from eidolon.read_aloud import CannotRead, NothingToRead, ReadingTooOften
from eidolon.sessions import UserId

log = logging.getLogger(__name__)


def add_read_aloud_routes(app: FastAPI, backend: Backend) -> None:
    reading = backend.read_aloud

    @app.get("/api/voice/{key}")
    async def read_aloud(key: str, user_id: UserId) -> Response:
        try:
            said = await reading.read(user_id, key)
        except NothingToRead:
            raise HTTPException(404, "没有这条消息") from None
        except CannotRead as e:
            raise HTTPException(409, str(e)) from None
        except ReadingTooOften as e:
            raise HTTPException(429, str(e)) from None
        except Exception as e:
            # Not with what was raised: a service's error may repeat what it was asked.
            log.warning("reading a message aloud failed: %s", type(e).__name__)
            raise HTTPException(502, "没能念出来，稍后再试") from None
        return Response(
            said.audio, media_type=said.media_type,
            headers={"Cache-Control": "private, max-age=86400"},
        )
