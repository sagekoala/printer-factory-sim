"""Manufacturer service layer.

All business logic lives here.  The CLI (``manufacturer.cli``), the API
(``manufacturer.main``), and the simulation engine
(``manufacturer.simulation``) depend on these functions and translate
their exceptions into transport-appropriate errors.
"""

from manufacturer.services.suppliers import (
    ProviderError,
    ProviderHTTPError,
    ProviderUnreachableError,
    check_deliveries,
    get_catalog,
    list_providers,
    list_purchase_orders,
    place_order,
)

__all__ = [
    "ProviderError",
    "ProviderHTTPError",
    "ProviderUnreachableError",
    "check_deliveries",
    "get_catalog",
    "list_providers",
    "list_purchase_orders",
    "place_order",
]
