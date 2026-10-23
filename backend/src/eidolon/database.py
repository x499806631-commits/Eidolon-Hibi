"""The database: one SQLite file holding the users and their conversations, memories and
usage, next to the story corpus and the embedded text set.

The data access layer (`data_access`, ADR-0028) is the only way into it, but for the
health check (`readable`), on a read-only connection of its own, and for the backup, which
copies it (`VACUUM INTO`), and restoring one in its place (`restore`), each on a
connection of its own opened by `connect`, the same way as the layer's. Each module that
keeps tables in it creates its own tables; which tables hold a user's data, and which rows
of them are the user's, is listed here (`USER_DATA`), and so is every other table
(`NOT_USER_DATA`), so a table cannot be added without being one or the other. A table of a user's data may instead be kept when the user is deleted, but no
longer say whose it is (`ANONYMIZED_USER_DATA`), as activity records are, so that the
numbers they add up to do not change.

Deleting a user (`delete_user_data`) is for good (ADR-0006): what is deleted is
overwritten, not just freed, and the write-ahead log is then emptied
(`Database.checkpoint`), so none of it is left in the files. Deleting a memory, or clearing them all, or a past conversation
(`delete_conversation_data`), overwrites it in the database too, but earlier frames of the write-ahead log may still hold it until a later checkpoint or
the user is deleted."""

import secrets
import sqlite3
from pathlib import Path

USER_DATA = [
    ("memory_vectors", "user_id = ?"),
    ("memories", "user_id = ?"),
    ("experience_vectors", "user_id = ?"),
    ("experiences", "user_id = ?"),
    ("photos", "conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)"),
    ("messages", "conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)"),
    ("conversation_events",
     "conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)"),
    ("reach_out_photos",
     "conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)"),
    ("reach_outs", "conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)"),
    ("reach_out_schedules",
     "conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)"),
    ("calls", "conversation_id IN (SELECT id FROM conversations WHERE user_id = ?)"),
    ("conversations", "user_id = ?"),
    ("producer_profiles", "user_id = ?"),
    ("affinities", "user_id = ?"),
    ("gift_balances", "user_id = ?"),
    ("gift_backpacks", "user_id = ?"),
    ("gift_rewards", "user_id = ?"),
    ("daily_task_progress", "user_id = ?"),
    ("achievement_progress", "user_id = ?"),
    ("achievement_stages", "user_id = ?"),
    ("usage", "user_id = ?"),
    ("user_limits", "user_id = ?"),
    ("reach_outs_off", "user_id = ?"),
    ("features_off", "user_id = ?"),
    ("keepsakes", "user_id = ?"),
    ("keepsake_due", "user_id = ?"),
    ("trip_photo_due", "user_id = ?"),
    ("gift_tickets", "user_id = ?"),
    ("dates", "user_id = ?"),
    ("favor_ties", "user_id = ?"),
    ("read_aloud", "user_id = ?"),
    ("homes", "user_id = ?"),
    ("relation_changes", "user_id = ?"),
    ("relation_overrides", "user_id = ?"),
    ("relation_news", "user_id = ?"),
    ("home_backgrounds", "user_id = ?"),
    ("favor_wakes", "user_id = ?"),
    ("favor_make_ups", "user_id = ?"),
    ("favor_parties", "user_id = ?"),
    ("letters", "user_id = ?"),
    ("letter_days", "user_id = ?"),
    ("promises", "user_id = ?"),
    ("diary_moments", "user_id = ?"),
    ("affinity_scenes", "user_id = ?"),
    ("schedules", "user_id = ?"),
    ("diary_entries", "user_id = ?"),
    ("diary_tries", "user_id = ?"),
    ("data_package_imports", "user_id = ?"),
    ("sessions", "user_id = ?"),
    ("sign_in_methods", "user_id = ?"),
    ("users", "id = ?"),
]
"""Every table holding a user's data, and which rows are theirs, in an order that deletes
what refers to a row before the row."""

CONVERSATION_DATA = [
    ("photos", "conversation_id = ?"),
    ("messages", "conversation_id = ?"),
    ("conversation_events", "conversation_id = ?"),
    ("reach_out_photos", "conversation_id = ?"),
    ("reach_outs", "conversation_id = ?"),
    ("reach_out_schedules", "conversation_id = ?"),
    ("calls", "conversation_id = ?"),
]
"""Every table holding a conversation's record, or kept under it as the calls in it are and
when she is to reach out in it next, which is deleted with it, and which rows are its."""

FROM_CONVERSATION = [
    ("memories", "conversation_id = NULL", "conversation_id = ?"),
]
"""Every table whose rows say which conversation they came from, kept when it is deleted: how
they are made to say so no longer, and which rows they are."""

ANONYMIZED_USER_DATA = [
    ("activity_records", "user_id = NULL, anonymous_id = :anonymous", "user_id = :user"),
    ("user_rollups", "who = :anonymous", "who = :user"),
]
"""Every table holding a user's data that is kept when the user is deleted: how its rows
of theirs are made no one's, and which rows they are. `:anonymous` is an id made up anew
for each deleted user, the same for all their rows, so that they still count as one."""

NOT_USER_DATA = frozenset(
    {
        "episodes", "episode_cast", "episode_lines", "episode_passages",
        "story_corpus_version", "embedded_texts",
        "service_calls", "config_overrides", "price_overrides", "price_catalog",
        "price_catalog_thinking", "price_catalog_hosts", "price_catalog_providers",
        "price_catalog_offers", "price_catalog_state", "banned_identities", "announcements",
        "sponsor", "gifts_opened", "reset_card_grants", "backup_runs", "record_rollups",
        "taken_rollups", "call_rollups", "spread_rollups", "achievements_launched", "outfit_tags",
    }
)
"""Every other table: the story corpus, the embedded text set, the calls to outside
services, which say whose turn they were made in only through its activity record, the
configuration and the prices the admin console overrides, the price catalogs fetched
online, the ban list, hashes of the identities banned users since deleted signed in
with, which say whose they were to no one, the announcements admins write, which say
nothing of who wrote them, the sponsor code admins upload, when gifts last opened, which
is no one's, and the reset cards admins have given every user, which say nothing of who
gave them or to whom, the record of each backup run, which says nothing of anyone's, and
when the achievements launched, which is no one's."""


HEALTH_READ_TIMEOUT = 2.0
"""How long, in seconds, the health check waits to read the database."""


def connect(path: Path) -> sqlite3.Connection:
    """A connection to the database at `path`, making the directory it is in if need be,
    usable from any thread and waiting up to 30 seconds for another writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False, timeout=30)
    try:
        # Readers need not wait for a writer: the data access layer's readers, the health
        # check and the backup, and the command line in another process, each have a
        # connection to this database.
        db.execute("PRAGMA journal_mode = WAL")
        # In WAL mode a commit need not wait for the disk: losing power may lose the last
        # commits, never corrupt the database.
        db.execute("PRAGMA synchronous = NORMAL")
        # What is deleted is overwritten, not just freed: deleting a user is for good.
        db.execute("PRAGMA secure_delete = ON")
        db.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error:  # not a database: closed, so that the file can be deleted
        db.close()
        raise
    return db


def readable(path: Path) -> bool:
    """Whether the database at `path` can be read now, on a read-only connection of its
    own, outside the data access layer so as to wait on no write task, nor make a
    database where there is none; for the health check."""
    try:
        db = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=HEALTH_READ_TIMEOUT
        )
        try:
            db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return False
    return True


def delete_user_data(db: sqlite3.Connection, user_id: str) -> None:
    """Delete the user and everything of theirs, for good, but what is only made no one's,
    through `db`, in the caller's transaction; the write-ahead log still holds the pages as
    they were until it is checkpointed. A table not created yet (by a module this process
    has not opened) is passed over."""
    present = tables(db)
    names = {"user": user_id, "anonymous": secrets.token_hex(16)}
    for table, anonymize, where in ANONYMIZED_USER_DATA:
        if table in present:
            db.execute(f"UPDATE {table} SET {anonymize} WHERE {where}", names)
    for table, where in USER_DATA:
        if table in present:
            db.execute(f"DELETE FROM {table} WHERE {where}", (user_id,))


def delete_conversation_data(db: sqlite3.Connection, conversation_id: str) -> None:
    """Delete the conversation and its record, for good, through `db`, in the caller's
    transaction; what only says it came from it is kept, saying so no longer. A table not
    created yet (by a module this process has not opened) is passed over."""
    present = tables(db)
    for table, unlink, where in FROM_CONVERSATION:
        if table in present:
            db.execute(f"UPDATE {table} SET {unlink} WHERE {where}", (conversation_id,))
    for table, where in CONVERSATION_DATA:
        if table in present:
            db.execute(f"DELETE FROM {table} WHERE {where}", (conversation_id,))
    db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def by_name(db: sqlite3.Connection) -> sqlite3.Cursor:
    """A cursor on `db` whose rows are read by name, leaving the connection's, which other
    modules share, as they are."""
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    return cursor


def tables(db: sqlite3.Connection) -> set[str]:
    """The tables in the database, whichever module made them."""
    return {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
