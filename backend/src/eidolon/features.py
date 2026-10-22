"""Each user's own switches for the fork's features (GLOSSARY.md「玩法开关」): her scenes at
the doors, her diary, her letters, her keepsakes, her schedule and, within it, her sleep and her bath; the pictures drawn for her and, within
them, the photos she sends when asked, those she sends from her trips and her keepsakes'
pictures; the
anniversaries she brings up, the special days she brings up, the promises she keeps track of,
and the weather she knows of. All on by default; a user switches off what they would rather
not have, for all the characters, and only for themselves. What the deployer has off is off
whatever the user's switch says; that is the features' own business, not this one's.

Sleep, the bath and her trips are kept in her schedule, so with her schedule off they are
off too; the photos and pictures are all drawn on image generation, so with it off they are
off too. Her trip photos are within both. Her keepsakes still come with the pictures off, an
emoji standing for each.
"""

import threading
from pathlib import Path
from typing import Literal, get_args

from eidolon.database import connect

Feature = Literal[
    "scenes", "diary", "letters", "keepsakes", "schedules", "sleep", "bath",
    "images", "photos", "trip_photos", "keepsake_images", "anniversaries", "special_days", "promises", "weather",
]
"""A feature a user may switch off."""

FEATURES: tuple[Feature, ...] = get_args(Feature)

WITHIN: dict[Feature, tuple[Feature, ...]] = {
    "sleep": ("schedules",), "bath": ("schedules",),
    "photos": ("images",), "trip_photos": ("images", "schedules"),
    "keepsake_images": ("images",),
}
"""The features that are part of others, off with any of them."""

_SCHEMA = """
-- The features each user has switched off (GLOSSARY.md「玩法开关」); on for all the others.
CREATE TABLE IF NOT EXISTS features_off (
    user_id TEXT NOT NULL REFERENCES users(id),
    feature TEXT NOT NULL,
    PRIMARY KEY (user_id, feature)
);
"""


class Features:
    """Every user's switches, in the database at `path` beside the users they belong to."""

    def __init__(self, path: Path) -> None:
        self._db = connect(path)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def on(self, user_id: str, feature: Feature) -> bool:
        """Whether the user has `feature` on, and the features it is part of, if any."""
        off = self._off(user_id)
        return feature not in off and not any(f in off for f in WITHIN.get(feature, ()))

    def of(self, user_id: str) -> dict[Feature, bool]:
        """The user's own switch for every feature, as they set it (not as `on` reads it)."""
        off = self._off(user_id)
        return {f: f not in off for f in FEATURES}

    def switch(self, user_id: str, feature: Feature, on: bool) -> None:
        """Switch `feature` on or off for the user, for all the characters."""
        with self._lock, self._db:
            if on:
                self._db.execute(
                    "DELETE FROM features_off WHERE user_id = ? AND feature = ?", (user_id, feature)
                )
            else:
                self._db.execute(
                    "INSERT OR IGNORE INTO features_off (user_id, feature) VALUES (?, ?)",
                    (user_id, feature),
                )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _off(self, user_id: str) -> set[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT feature FROM features_off WHERE user_id = ?", (user_id,)
            ).fetchall()
        return {feature for (feature,) in rows}


class AllOn:
    """Every feature on for everyone: where no switches are kept (tests building parts alone)."""

    def on(self, user_id: str, feature: Feature) -> bool:
        return True


Switches = Features | AllOn
"""What the features ask whether a user has them on."""
