"""Appeals over HTTP (GLOSSARY.md「我不是这个意思」): how many the user has left today at
`/api/appeals`, appealing her reply at `/api/messages/{id}/appeal`. A refused appeal answers
409 with why (`appeals.Refusal`) as `code`."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eidolon.appeals import AppealRefused, Appeals, NoSuchReply
from eidolon.sessions import UserId


def add_appeal_routes(app: FastAPI, appeals: Appeals) -> None:
    @app.exception_handler(AppealRefused)
    async def refused(_: Request, e: AppealRefused) -> JSONResponse:
        return JSONResponse({"detail": e.reason, "code": e.reason}, status_code=409)

    @app.get("/api/appeals")
    def read_appeals(user_id: UserId) -> dict:
        """How many appeals the user has left today."""
        return {"left": appeals.left(user_id)}

    @app.post("/api/messages/{message_id}/appeal")
    def appeal(message_id: int, user_id: UserId) -> dict:
        """Appeal her reply: her affinity as it left it, and how many are left today."""
        try:
            return appeals.appeal(user_id, message_id).shown()
        except NoSuchReply:
            raise HTTPException(404, "no such reply") from None
