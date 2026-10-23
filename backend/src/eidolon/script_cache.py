"""The mirror's scripts as the story sync keeps them: one place that decides whether a
script is fetched, and where it is kept (ADR-0003: never in the repository).

Scripts are laid out as the game does (`produce_events/200100401.json`), each with its
ETag beside it. The file is the truth: a script whose file is there is kept, one whose
file is not is fetched again, so an admin who deletes a script has it downloaded again
at the next sync. A script the mirror did not have is marked `.missing` beside where
it would be, so a caller can choose not to ask for it again; deleting the mark has it
asked for again.

Scripts are never fetched for an episode the corpus has the translation of, nor for a
failure ending, which is never imported.
"""

import os
from pathlib import Path
from typing import Literal

from eidolon.corpus import StoryCorpus
from eidolon.mirror import Mirror
from eidolon.story_import import is_failure_ending

Fetched = Literal["translated", "failure", "kept", "downloaded", "unchanged", "missing", "known_missing"]
"""What `fetch` did:

- `translated`: the corpus has its translation, so it was not asked for;
- `failure`: a failure ending, never imported, so not asked for;
- `kept`: its file is here and it was not asked for again;
- `downloaded`: the mirror sent it and it is kept now;
- `unchanged`: asked for again on its ETag, and the copy kept is current;
- `missing`: the mirror does not have it;
- `known_missing`: the mirror did not have it before, and it was not asked for again.
"""


class ScriptCache:
    def __init__(self, directory: Path, mirror: Mirror, corpus: StoryCorpus) -> None:
        self._directory = directory
        self._mirror = mirror
        self._corpus = corpus

    async def fetch(self, script: str, *, recheck: bool = False, retry_missing: bool = True) -> Fetched:
        """Make sure the script is kept here, if the corpus needs it and the mirror has it.

        A script kept here is not asked for again unless `recheck`, and then on its
        ETag. One the mirror did not have is asked for again only if `retry_missing`.
        Raises MirrorError if the mirror cannot be reached."""
        if await self._corpus.is_translated(script):
            return "translated"
        if is_failure_ending(script):
            return "failure"
        path = self._path(script)
        etag_path, missing_path = path.with_suffix(".etag"), path.with_suffix(".missing")
        if path.exists():
            if not recheck:
                return "kept"
            etag = etag_path.read_text(encoding="utf-8") if etag_path.exists() else None
        elif not retry_missing and missing_path.exists():
            return "known_missing"
        else:
            etag = None
        answer = await self._mirror.script(script, etag)
        if answer == "unchanged":
            return "unchanged"
        path.parent.mkdir(parents=True, exist_ok=True)
        if answer == "missing":
            missing_path.touch()
            return "missing"
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(answer.content)
        os.replace(partial, path)  # never a half-written script, even if interrupted
        if answer.etag:
            etag_path.write_text(answer.etag, encoding="utf-8")
        else:
            etag_path.unlink(missing_ok=True)
        missing_path.unlink(missing_ok=True)
        return "downloaded"

    def path(self, script: str) -> Path | None:
        """Where the script is kept, if it is."""
        path = self._path(script)
        return path if path.exists() else None

    def kept(self, kind: str) -> dict[str, Path]:
        """The scripts of that kind (`game_event_communications`) kept here, in order,
        and where."""
        return {f"{kind}/{path.stem}": path for path in sorted((self._directory / kind).glob("*.json"))}

    def _path(self, script: str) -> Path:
        return self._directory / f"{script}.json"

