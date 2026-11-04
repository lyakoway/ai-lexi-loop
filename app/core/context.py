"""Request-scoped context shared across the process.

The request id is a ContextVar so log records and (later) any background
task can attach it without passing it through every signature.
"""

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def current_request_id() -> str:
    """The id of the in-flight request, or ``-`` outside one."""
    return request_id_var.get()


def set_request_id(value: str) -> None:
    """Bind a request id for the current task."""
    request_id_var.set(value)
