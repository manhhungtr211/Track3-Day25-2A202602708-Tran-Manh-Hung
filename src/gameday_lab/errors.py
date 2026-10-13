"""Part A1 — Error classification.

The single most important decision in a reliability layer is *what kind of
failure* you are looking at, because each kind needs a different reaction.

Tests: tests/test_errors_retry.py
"""

from __future__ import annotations

from enum import Enum

from gameday_lab.sim.contracts import ProviderError


class ErrorKind(str, Enum):
    RATE_LIMITED = "rate_limited"  # 429 + retry-after: our tenant is over its limit
    QUOTA_EXHAUSTED = "quota_exhausted"  # 429 insufficient_quota: no point retrying
    OVERLOADED = "overloaded"  # 529 / 503: provider is shedding load or down
    SERVER_ERROR = "server_error"  # 500 / 502 / 504
    TIMEOUT = "timeout"  # we gave up waiting (status 0, code client_timeout)
    CLIENT_ERROR = "client_error"  # 400 / 401 / 403 / 404 / 422: our bug or config


def classify(err: ProviderError) -> ErrorKind:
    """Map a provider error to an ErrorKind.

    Rules:
    * status 0 (client gave up) or 408                  -> TIMEOUT
    * 429 with code "insufficient_quota"                -> QUOTA_EXHAUSTED
    * any other 429                                     -> RATE_LIMITED
    * 503, 529                                          -> OVERLOADED
    * other 5xx                                         -> SERVER_ERROR
    * everything else (4xx)                             -> CLIENT_ERROR
    Look at ``err.status`` and ``err.code``.
    """
    if err.status == 0 or err.status == 408:
        return ErrorKind.TIMEOUT
    if err.status == 429:
        if err.code == "insufficient_quota":
            return ErrorKind.QUOTA_EXHAUSTED
        return ErrorKind.RATE_LIMITED
    if err.status in (503, 529):
        return ErrorKind.OVERLOADED
    if 500 <= err.status < 600:
        return ErrorKind.SERVER_ERROR
    return ErrorKind.CLIENT_ERROR


def is_retryable(kind: ErrorKind) -> bool:
    """May we try *the same provider* again for this call (after a delay)?

    Retryable: RATE_LIMITED (after retry-after), OVERLOADED,
    SERVER_ERROR, TIMEOUT. Not retryable: QUOTA_EXHAUSTED, CLIENT_ERROR.
    """
    return kind in {
        ErrorKind.RATE_LIMITED,
        ErrorKind.OVERLOADED,
        ErrorKind.SERVER_ERROR,
        ErrorKind.TIMEOUT,
    }


def counts_toward_breaker(kind: ErrorKind) -> bool:
    """Does this error say something about the *health* of the provider?

    Yes: OVERLOADED, SERVER_ERROR, TIMEOUT.
    No:  RATE_LIMITED (our quota, handled by a cooldown), QUOTA_EXHAUSTED (billing,
         handled by a cooldown + alert), CLIENT_ERROR (our bug — every provider
         would reject it, so it must never open a breaker).
    """
    return kind in {
        ErrorKind.OVERLOADED,
        ErrorKind.SERVER_ERROR,
        ErrorKind.TIMEOUT,
    }


def failover_helps(kind: ErrorKind) -> bool:
    """Would sending the same request to *another* provider help?

    Everything except CLIENT_ERROR: a malformed request is
    malformed everywhere.
    """
    return kind is not ErrorKind.CLIENT_ERROR

