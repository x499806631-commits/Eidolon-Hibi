"""Users and the identities they sign in with.

Anyone may sign in with Google or GitHub (ADR-0006): the first time an identity signs in,
it becomes a new user; after that it is always that user. Two identities are never taken
for one user on their own, not even with the same email address; a signed-in user links
another identity by hand. The only gate is against throwaway accounts: a GitHub account
must be a year old, and a Google account's email address verified. A banned user's
identities cannot sign in.

Users can delete themselves, and admins can delete them, and with them everything of
theirs, for good: signing in with the same identity afterwards makes a new user. Unless
they were banned: deleting is not unbanning, so the identities a banned user signed in
with are kept on a ban list, only as keyed hashes (`identity_hash`), and can neither sign
in nor be linked again.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from eidolon.call import Calls
from eidolon.clock import Clock
from eidolon.config import Settings
from eidolon.identity import Identity
from eidolon.memory import Memories
from eidolon.store import IdentityTaken, SignInMethod, Store, User

async def is_admin(store: Store, settings: Settings, user_id: str) -> bool:
    """Whether any identity the user signs in with is one the deployer lists as an admin's."""
    return bool(settings.admins) and signs_in_as_admin(
        await store.sign_in_methods(user_id), settings
    )


def signs_in_as_admin(methods: list[SignInMethod], settings: Settings) -> bool:
    """Whether any of a user's sign-in methods, `methods`, is one the deployer lists as an
    admin's."""
    return any((m.provider, m.subject) in settings.admins for m in methods)


MIN_GITHUB_ACCOUNT_AGE = timedelta(days=365)
"""How old a GitHub account must be to sign in with."""

Refusal = Literal["too_new", "email_unverified", "banned", "taken", "already_linked"]
"""Why an identity may not sign in, or be linked:

- `too_new`: a GitHub account less than a year old
- `email_unverified`: a Google account whose email address is not verified
- `banned`: it belongs to a banned user, or did to one since deleted
- `taken`: (linking) it belongs to another user
- `already_linked`: (linking) the user already signs in with another identity there
"""


class SignInRefused(Exception):
    def __init__(self, reason: Refusal) -> None:
        super().__init__(reason)
        self.reason: Refusal = reason


@dataclass
class Accounts:
    store: Store
    clock: Clock
    memories: Memories
    calls: Calls
    secret_key: str | None = None
    """The deployment's, from which the key the ban list is hashed with is derived."""
    _ban_key: bytes | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._ban_key = _ban_list_key(self.secret_key) if self.secret_key else None

    async def sign_in(self, identity: Identity) -> User:
        """The user this identity signs in as, made the first time. Raises SignInRefused."""
        await self._check(identity)
        user_id = await self.store.user_signing_in_with(identity.provider, identity.subject)
        if user_id is None:
            try:
                return await self.store.create_user_signing_in_with(
                    identity.display_name, self._method(identity)
                )
            except IdentityTaken:  # signed in twice at once; the other made the user
                user_id = await self.store.user_signing_in_with(
                    identity.provider, identity.subject
                )
                assert user_id is not None
        user = await self.store.get_user(user_id)
        if user.banned_at is not None:
            raise SignInRefused("banned")
        await self.store.update_sign_in_method(
            identity.provider, identity.subject, identity.display_name, identity.email
        )
        return user

    async def link(self, user_id: str, identity: Identity) -> None:
        """Let the user sign in with this identity too; nothing happens if they already do.
        Raises SignInRefused."""
        await self._check(identity)
        owner = await self.store.user_signing_in_with(identity.provider, identity.subject)
        if owner == user_id:
            return
        if owner is not None:
            raise SignInRefused("taken")
        if any(
            m.provider == identity.provider for m in await self.store.sign_in_methods(user_id)
        ):
            raise SignInRefused("already_linked")
        try:
            await self.store.add_sign_in_method(user_id, self._method(identity))
        except IdentityTaken:
            raise SignInRefused("taken") from None

    async def delete(self, user_id: str) -> None:
        """Delete the user and all their data (see `database.delete_user_data`), signing them out
        everywhere and hanging up their calls. A banned user's identities go on the ban
        list."""
        await self.calls.hang_up(user_id)
        await self.memories.forget(user_id)
        await self.store.delete_user(user_id, [
            identity_hash(self._ban_key, m.provider, m.subject)
            for m in await self.store.sign_in_methods(user_id)
        ])
        await self.calls.hang_up(user_id)  # any placed while memory was settling

    async def _check(self, identity: Identity) -> None:
        if await self.store.is_banned_identity(
            identity_hash(self._ban_key, identity.provider, identity.subject)
        ):
            raise SignInRefused("banned")
        if identity.provider == "github" and (
            identity.created_at is None
            or self.clock() - identity.created_at < MIN_GITHUB_ACCOUNT_AGE
        ):
            raise SignInRefused("too_new")
        if identity.provider == "google" and not identity.email_verified:
            raise SignInRefused("email_unverified")

    def _method(self, identity: Identity) -> SignInMethod:
        return SignInMethod(
            identity.provider,
            identity.subject,
            identity.display_name,
            identity.email,
            self.clock().isoformat(),
        )


def identity_hash(key: bytes | None, provider: str, subject: str) -> str:
    """What the ban list keeps of an identity: an HMAC with `key`, derived from the
    deployment's secret key, or, without one, a plain SHA-256."""
    identity = f"{provider}:{subject}".encode()
    if key is None:
        return hashlib.sha256(identity).hexdigest()
    return hmac.new(key, identity, hashlib.sha256).hexdigest()


def _ban_list_key(secret_key: str) -> bytes:
    """The key the ban list is hashed with, derived from the deployment's secret key apart
    from the one for the configuration's overrides."""
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"eidolon ban list"
    ).derive(secret_key.encode())
