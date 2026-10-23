"""What the server logs, and how: everything logged through `logging` at INFO and up,
each line with its time and which logger, but for libraries that log every request or ICE
check at INFO; and uvicorn's access log, with the time too, each request's path without its
query, which may carry what is not to be kept (an OAuth callback's `code`)."""

import copy
import logging
from typing import Any

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

QUIET = ("httpx", "httpcore", "aioice", "aiortc", "websockets")
"""Libraries logging at INFO what is too much to keep: every request they make, with its URL,
every ICE check; their warnings are kept."""


def configure() -> None:
    """Log through the root logger as `FORMAT` says, to stderr, at INFO and up."""
    logging.basicConfig(level=logging.INFO, format=FORMAT, force=True)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)


class WithoutQuery(logging.Filter):
    """Drops the query from the path an access log record logs, keeping the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(path := args[2], str):
            record.args = (*args[:2], path.split("?", 1)[0], *args[3:])
        return True


def uvicorn_log_config() -> dict[str, Any]:
    """Uvicorn's own logging configuration, each line with its time, the access log through
    `WithoutQuery`."""
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    formatters = config["formatters"]
    formatters["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    formatters["access"]["fmt"] = (
        '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    )
    config["filters"] = {"without_query": {"()": WithoutQuery}}
    config["handlers"]["access"]["filters"] = ["without_query"]
    return config
