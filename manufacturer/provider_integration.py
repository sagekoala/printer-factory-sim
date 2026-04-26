"""Backward-compat shim for the Week 5/early-Week-6 provider integration.

All real logic now lives in :mod:`manufacturer.services.suppliers`.
This module preserves the historical entry points so the FastAPI
endpoints in :mod:`manufacturer.main` and any other callers continue
to work without modification:

- :func:`list_configured_suppliers` -> :func:`services.list_providers`
- :func:`fetch_supplier_catalog` -> :func:`services.get_catalog` (by name)
- :func:`create_outbound_purchase` -> :func:`services.place_order`
- :func:`list_outbound_purchase_orders` -> :func:`services.list_purchase_orders`
- :func:`sync_outbound_purchase_orders` -> :func:`services.check_deliveries`

New code should import directly from
``manufacturer.services.suppliers``; this shim exists only so we don't
break Week 5 endpoints by renaming functions.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

try:
    from manufacturer.database import FactoryConfigRow
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
except ModuleNotFoundError:  # standalone execution from inside ``manufacturer/``
    from database import FactoryConfigRow  # type: ignore[no-redef]
    from services.suppliers import (  # type: ignore[no-redef]
        ProviderError,
        ProviderHTTPError,
        ProviderUnreachableError,
        check_deliveries,
        get_catalog,
        list_providers,
        list_purchase_orders,
        place_order,
    )


def list_configured_suppliers() -> list[dict[str, str]]:
    """Legacy alias for :func:`services.suppliers.list_providers`."""
    return list_providers()


def fetch_supplier_catalog(supplier_name: str) -> list[dict]:
    """Legacy alias: look up the provider URL by name, then fetch catalog.

    Raises ``ValueError`` for unknown supplier and ``RuntimeError`` for
    transport failures (matching the original signature so the existing
    FastAPI handlers translate them to 404/502 unchanged).
    """
    supplier = _supplier_or_raise(supplier_name)
    try:
        return get_catalog(supplier["url"])
    except ProviderError as exc:
        raise RuntimeError(str(exc)) from exc


def create_outbound_purchase(
    db: Session,
    supplier_name: str,
    product_id: str,
    quantity: int,
) -> dict:
    """Legacy alias for :func:`services.suppliers.place_order`."""
    supplier = _supplier_or_raise(supplier_name)
    current_day = _read_current_day(db)
    try:
        return place_order(
            db,
            provider_url=supplier["url"],
            supplier_name=supplier_name,
            product_id=product_id,
            quantity=quantity,
            current_day=current_day,
        )
    except ProviderError as exc:
        raise RuntimeError(str(exc)) from exc


def list_outbound_purchase_orders(db: Session) -> list[dict]:
    """Legacy alias returning rows in the old field shape.

    The Week 5 API expects ``provider_name`` (not ``supplier_name``) on
    each row.  We translate here so the old response model in
    ``main.py`` keeps validating.
    """
    rows = list_purchase_orders(db)
    return [_legacy_shape(row) for row in rows]


def sync_outbound_purchase_orders(db: Session, day: int) -> None:
    """Legacy alias: delegate to :func:`check_deliveries`.

    The old signature accepted a ``day`` argument but the new service
    derives it from ``factory_config`` itself, so the parameter is
    accepted and ignored.
    """
    del day  # kept for source-compatibility with old call sites
    check_deliveries(db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _supplier_or_raise(name: str) -> dict[str, str]:
    for entry in list_providers():
        if entry["name"] == name:
            return entry
    raise ValueError(f"Unknown supplier: {name!r}")


def _read_current_day(db: Session) -> int:
    row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    return int(row.value) if row else 0


def _legacy_shape(row: dict) -> dict:
    """Translate ``services.suppliers``' shape to the Week 5 API response shape."""
    return {
        "id": row["id"],
        "provider_name": row["supplier_name"],
        "provider_order_id": row["provider_order_id"],
        "product_name": row["product_name"],
        "quantity": row["quantity"],
        "placed_day": row["placed_day"],
        "expected_delivery_day": row["expected_delivery_day"],
        "status": row["status"],
    }


__all__ = [
    "ProviderError",
    "ProviderHTTPError",
    "ProviderUnreachableError",
    "create_outbound_purchase",
    "fetch_supplier_catalog",
    "list_configured_suppliers",
    "list_outbound_purchase_orders",
    "sync_outbound_purchase_orders",
]
