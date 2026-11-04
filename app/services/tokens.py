"""Personal API tokens for non-Telegram clients.

A token is a random secret shown to the user exactly once; only its
SHA-256 hash is stored. Tokens are delivered through the already trusted
Telegram channel, which keeps authentication real without building the
Web login UI yet (the future web flow replaces the delivery mechanism,
not the token storage).
"""

import hashlib
import secrets
from dataclasses import dataclass

from app.domain.srs import utc_now
from app.repositories import ApiTokenRepository

TOKEN_PREFIX = "alex_"
#: Cap so a user cannot mint unbounded tokens; revoke support comes with
#: the Web dashboard stage.
MAX_TOKENS_PER_USER = 5


@dataclass(frozen=True)
class IssuedToken:
    """A freshly created token with its plaintext value."""

    token: str
    id: int


class TokenLimitError(Exception):
    """Raised when the user already holds the maximum token count."""


class ApiTokenService:
    """Issues and resolves personal API tokens."""

    def __init__(self, repository: ApiTokenRepository) -> None:
        self._repository = repository

    async def issue(self, user_id: int) -> IssuedToken:
        """Create a new token and return its plaintext value once."""
        if await self._repository.count_for_user(user_id) >= MAX_TOKENS_PER_USER:
            raise TokenLimitError(
                f"user_id={user_id} already has {MAX_TOKENS_PER_USER} tokens"
            )
        secret = secrets.token_urlsafe(32)
        token = TOKEN_PREFIX + secret
        created = await self._repository.create(user_id, hash_token(token))
        return IssuedToken(token=token, id=created.id)

    async def resolve_user_id(self, token: str) -> int | None:
        """Return the user id a valid, non-revoked token belongs to."""
        row = await self._repository.find_active_by_hash(
            hash_token(token), now=utc_now()
        )
        if row is None:
            return None
        await self._repository.touch(row.id, now=utc_now())
        return row.user_id


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a token — the only stored form."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
