"""Process entrypoint: ``python -m app.main``.

Configures logging and hands control to uvicorn. The application itself is
built by :func:`app.api.app.create_app`, which uvicorn imports as a factory
(so importing this module has no side effects beyond reading settings).
"""

import uvicorn

from app.core.config import get_settings
from app.core.logging import setup_logging


def main() -> None:
    """Run the application: HTTP API plus supervised Telegram polling."""
    settings = get_settings()
    setup_logging(settings)
    uvicorn.run(
        "app.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
