"""What the server logs: uvicorn's access log keeps each request's path, not its query,
which may carry what is not to be kept (an OAuth callback's `code`)."""

import asyncio
import logging

import httpx
import pytest
import uvicorn

from eidolon.server_logs import uvicorn_log_config


async def test_the_access_log_keeps_no_query(capsys: pytest.CaptureFixture[str]):
    """Through uvicorn itself, as it logs each request, so as to break should it log them
    otherwise one day."""

    async def app(scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 302, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=uvicorn_log_config())
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        [port] = {s.getsockname()[1] for listening in server.servers for s in listening.sockets}
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.get(f"http://127.0.0.1:{port}/api/auth/github/callback?code=secret&state=s")
    finally:
        server.should_exit = True
        await serving
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):  # on streams captured
            logging.getLogger(name).handlers.clear()

    captured = capsys.readouterr()
    logged = captured.out + captured.err  # uvicorn logs requests to stdout
    assert '"GET /api/auth/github/callback HTTP/1.1" 302' in logged
    assert "secret" not in logged
