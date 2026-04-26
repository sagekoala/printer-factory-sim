"""Service-layer exceptions for the provider app.

These are raised by ``provider.services.*`` and translated to HTTP status
codes by the API layer (``provider.api``):

- ``NotFoundError`` -> ``404 Not Found``
- ``InsufficientStockError`` -> ``409 Conflict``
- generic ``ValueError`` -> ``400 Bad Request``

Keeping the exception hierarchy in one place lets the CLI surface clear
error messages and the API translate them mechanically.
"""

from __future__ import annotations


class ProviderServiceError(Exception):
    """Base class for any service-layer failure.

    Subclass this when adding new domain errors so the API layer can
    map them to HTTP responses uniformly.
    """


class NotFoundError(ProviderServiceError, LookupError):
    """A referenced product, order, or pricing tier does not exist.

    Inherits from :class:`LookupError` so callers that already handle
    ``KeyError``/``LookupError`` keep working.
    """


class InsufficientStockError(ProviderServiceError, ValueError):
    """An order cannot be fulfilled from on-hand stock.

    Inherits from :class:`ValueError` for backwards compatibility with
    earlier code paths that broadly caught ``ValueError`` for create
    failures, while still letting the API layer single it out for a
    ``409 Conflict`` response.
    """


__all__ = [
    "ProviderServiceError",
    "NotFoundError",
    "InsufficientStockError",
]
