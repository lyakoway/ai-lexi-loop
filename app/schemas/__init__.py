"""Pydantic schemas: DTOs between handlers, services and repositories."""

from app.schemas.profile import ProfileCreate, ProfileUpdate

__all__ = ["ProfileCreate", "ProfileUpdate"]
