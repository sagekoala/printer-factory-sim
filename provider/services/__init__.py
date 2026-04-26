"""Provider service layer.

All business logic lives in this package.  ``cli.py`` and ``api.py``
must remain thin wrappers that dispatch to these functions and
translate the raised exceptions into their respective transport-layer
errors.
"""

from provider.services.exceptions import (
    InsufficientStockError,
    NotFoundError,
    ProviderServiceError,
)

__all__ = [
    "ProviderServiceError",
    "NotFoundError",
    "InsufficientStockError",
]
