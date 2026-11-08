"""FastAPI dependencies: per-request session, services and auth.

Authentication is real: a personal bearer token whose SHA-256 hash is
stored in ``api_tokens``. Tokens are issued through the trusted
Telegram channel today; the future Web login replaces the delivery
mechanism, not this verification path. A Telegram ID sent by a client
proves nothing and is never used for authentication.
"""

import re
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionFactory, session_scope
from app.integrations.llm.client import StructuredLlmClient
from app.repositories import (
    AnalyticsRepository,
    ApiTokenRepository,
    ExpressionProgressRepository,
    ExpressionRepository,
    ProfileRepository,
    UserGeneratedExpressionRepository,
    UserRepository,
)
from app.services import (
    AnalyticsService,
    ApiTokenService,
    LessonService,
    ProfileService,
    ReviewService,
    StatsService,
    TutorService,
    UserService,
)
from app.services.tutor import ChatService, GenerationService

_bearer = HTTPBearer(auto_error=False)


def get_session_factory(request: Request) -> SessionFactory:
    """Session factory installed by the application lifespan."""
    factory: SessionFactory | None = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application is not ready",
        )
    return factory


async def get_session(
    factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    """One transactional session per request."""
    async with session_scope(factory) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user_id(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> int:
    """Resolve the bearer token to the internal user id, or 401."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token_service = ApiTokenService(ApiTokenRepository(session))
    user_id = await token_service.resolve_user_id(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


CurrentUserId = Annotated[int, Depends(get_current_user_id)]


def analytics_service(session: SessionDep) -> AnalyticsService:
    return AnalyticsService(AnalyticsRepository(session))


def profile_service(session: SessionDep) -> ProfileService:
    return ProfileService(ProfileRepository(session), analytics_service(session))


def lesson_service(session: SessionDep) -> LessonService:
    analytics = analytics_service(session)
    return LessonService(
        ExpressionRepository(session),
        ExpressionProgressRepository(session),
        analytics,
    )


def review_service(session: SessionDep) -> ReviewService:
    analytics = analytics_service(session)
    return ReviewService(
        ExpressionProgressRepository(session),
        ExpressionRepository(session),
        analytics,
    )


def stats_service(session: SessionDep) -> StatsService:
    return StatsService(session, AnalyticsRepository(session))


def get_llm_client(request: Request) -> StructuredLlmClient | None:
    """The application-wide LLM client, or ``None`` when disabled."""
    client: StructuredLlmClient | None = getattr(request.app.state, "llm_client", None)
    return client


LlmDep = Annotated[StructuredLlmClient | None, Depends(get_llm_client)]


def tutor_service(
    session: SessionDep,
    llm_client: Annotated[StructuredLlmClient | None, Depends(get_llm_client)],
) -> TutorService:
    """TutorService with the app-wide LLM client (None = disabled)."""
    from app.services.tutor import TutorService as _TutorService

    return _TutorService(
        profile_service(session),
        ExpressionRepository(session),
        ExpressionProgressRepository(session),
        llm_client,
        analytics_service(session),
    )


def generation_service(
    session: SessionDep,
    llm_client: Annotated[StructuredLlmClient | None, Depends(get_llm_client)],
) -> GenerationService:
    """GenerationService with the app-wide LLM client (None = disabled)."""
    return GenerationService(
        profile_service(session),
        ExpressionRepository(session),
        UserGeneratedExpressionRepository(session),
        llm_client,
        analytics_service(session),
    )


def user_service(session: SessionDep) -> UserService:
    return UserService(UserRepository(session), analytics_service(session))


def chat_service(
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    llm_client: LlmDep,
) -> ChatService:
    """ChatService sharing the app-wide LLM client (None = disabled)."""
    return ChatService(session_factory, llm_client, get_settings())


_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


async def get_idempotency_key(
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> str:
    """Validate the Idempotency-Key header before service/LLM work."""
    if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Idempotency-Key must be 8-64 characters of A-Z, a-z, "
                "digits, underscore or hyphen"
            ),
        )
    return idempotency_key


AnalyticsDep = Annotated[AnalyticsService, Depends(analytics_service)]
ProfileDep = Annotated[ProfileService, Depends(profile_service)]
LessonDep = Annotated[LessonService, Depends(lesson_service)]
ReviewDep = Annotated[ReviewService, Depends(review_service)]
StatsDep = Annotated[StatsService, Depends(stats_service)]
TutorDep = Annotated[TutorService, Depends(tutor_service)]
GenerationDep = Annotated[GenerationService, Depends(generation_service)]
ChatDep = Annotated[ChatService, Depends(chat_service)]
IdempotencyKeyDep = Annotated[str, Depends(get_idempotency_key)]
UserDep = Annotated[UserService, Depends(user_service)]
