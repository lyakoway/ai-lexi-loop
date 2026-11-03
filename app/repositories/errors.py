"""Repository-level exceptions.

Callers above the data-access layer (services, bot handlers, API routes)
catch :class:`StorageError` instead of SQLAlchemy exceptions, so storage
internals never leak upwards.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError, SQLAlchemyError


class StorageError(Exception):
    """Raised when a database operation fails.

    ``conflict`` is ``True`` when the failure was caused by a uniqueness
    violation rather than an unavailable database.
    """

    def __init__(self, message: str, *, conflict: bool = False) -> None:
        super().__init__(message)
        self.conflict = conflict


@asynccontextmanager
async def wrap_storage_errors(
    message: str = "database operation failed",
) -> AsyncIterator[None]:
    """Convert SQLAlchemy errors into :class:`StorageError`.

    Uniqueness violations are marked as conflicts; everything else is
    reported as a generic storage failure. The original exception is kept
    as ``__cause__`` for logs while being hidden from callers.
    """
    try:
        yield
    except IntegrityError as error:
        raise StorageError(message, conflict=True) from error
    except SQLAlchemyError as error:
        raise StorageError(message) from error
