"""Exceptions raised by :mod:`newstar_sdk`."""

from __future__ import annotations

from typing import Optional


class NewStarError(Exception):
    """Base class for every SDK error."""


class ConfigurationError(NewStarError, ValueError):
    """Invalid client configuration."""


class TransportError(NewStarError):
    """The HTTP request failed (network error, timeout, 5xx, ...)."""

    def __init__(self, message: str, *, url: Optional[str] = None, status_code: Optional[int] = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class AuthenticationError(NewStarError):
    """The login request was rejected."""


class SessionExpired(NewStarError):
    """The game session is no longer valid and no automatic login was possible."""


class GameError(NewStarError):
    """The game answered with an error page or rejected an action."""

    def __init__(self, message: str, *, url: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.url = url

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.url:
            return f"{self.message} ({self.url})"
        return self.message


class ParseError(NewStarError):
    """A page could not be parsed in a way the SDK understands."""


class ActionRejected(GameError):
    """The server accepted the HTTP request but did not perform the action.

    ``before_post`` is true when the SDK rejected the action from page data
    before sending any write request.  In that case nothing was queued, so a
    caller may safely fall through to a different action in the same cycle.
    A rejection after the POST is ambiguous and must not be retried blindly.
    """

    def __init__(self, message: str, *, url: Optional[str] = None, before_post: bool = False):
        super().__init__(message, url=url)
        self.before_post = before_post
