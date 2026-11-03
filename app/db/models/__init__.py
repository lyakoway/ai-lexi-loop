"""ORM models package. Imported as a whole by Alembic's ``env.py``."""

from app.db.models.user import User

__all__ = ["User"]
