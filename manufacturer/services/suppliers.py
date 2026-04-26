"""External provider integration for the manufacturer app.

Public API
----------
- :func:`list_providers` — read ``config.json`` (or legacy ``provider_config.json``)
- :func:`get_catalog` — ``GET {provider_url}/api/catalog``
- :func:`place_order` — ``POST /api/orders``, persist locally, log event
- :func:`list_purchase_orders` — read-side list of locally tracked outbound POs
- :func:`check_deliveries` — poll provider for each pending outbound PO,
  reconcile delivered ones into local stock, and log events

Error handling rules (from the Week 6 spec)
------------------------------------------
- Network failure (``httpx.ConnectError`` etc.) — log a warning event,
  skip the provider, never crash.
- HTTP 4xx — the provider rejected the request; surface as
  :class:`ProviderHTTPError` so the CLI/API can show the provider's
  ``detail`` message.
- HTTP 5xx — the provider is unhealthy; log a warning, skip delivery
  polling for that provider on this tick.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy.orm import Session

try:
    from manufacturer.database import (
        EventRow,
        FactoryConfigRow,
        OutboundPurchaseOrderRow,
        ProductRow,
    )
except ModuleNotFoundError:
    from database import (  # type: ignore[no-redef]
        EventRow,
        FactoryConfigRow,
        OutboundPurchaseOrderRow,
        ProductRow,
    )


_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & exceptions
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT_SECONDS: float = 8.0

_MANUFACTURER_DIR = Path(__file__).resolve().parent.parent
_CONFIG_NEW = _MANUFACTURER_DIR / "config.json"
_CONFIG_LEGACY = _MANUFACTURER_DIR / "provider_config.json"


class ProviderError(Exception):
    """Base class for any provider-integration failure."""


class ProviderUnreachableError(ProviderError):
    """The provider could not be reached (network / connection error)."""


class ProviderHTTPError(ProviderError):
    """The provider responded with a non-2xx status code.

    ``status_code`` and ``detail`` carry the upstream response so the
    CLI and API layers can surface them verbatim.
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"provider returned HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def list_providers() -> list[dict[str, str]]:
    """Return ``[{name, url}, ...]`` for every configured provider.

    Resolution order:

    1. ``manufacturer/config.json`` (new spec, flat structure).
    2. ``manufacturer/provider_config.json`` (Week 5 legacy with
       ``manufacturer.providers`` nesting).

    Returns ``[]`` if neither file exists.  Always returns a fresh
    list — callers can mutate the result safely.
    """
    if _CONFIG_NEW.exists():
        payload = json.loads(_CONFIG_NEW.read_text())
        providers = payload.get("providers", [])
    elif _CONFIG_LEGACY.exists():
        payload = json.loads(_CONFIG_LEGACY.read_text())
        providers = payload.get("manufacturer", {}).get("providers", [])
    else:
        return []
    return [{"name": p["name"], "url": p["url"].rstrip("/")} for p in providers]


def _provider_by_name(name: str) -> dict[str, str]:
    """Look up a configured provider by name; raises ``ValueError`` if unknown."""
    for entry in list_providers():
        if entry["name"] == name:
            return entry
    raise ValueError(f"Unknown supplier: {name!r}")


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def get_catalog(provider_url: str) -> list[dict]:
    """Fetch ``GET {provider_url}/api/catalog`` and return the parsed JSON.

    Raises:
        ProviderUnreachableError: network/connection failure.
        ProviderHTTPError: the provider returned a non-2xx status code.
    """
    url = f"{provider_url.rstrip('/')}/api/catalog"
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = client.get(url)
    except httpx.RequestError as exc:
        raise ProviderUnreachableError(
            f"Provider {provider_url} unreachable: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise ProviderHTTPError(response.status_code, _extract_detail(response))
    return response.json()


# ---------------------------------------------------------------------------
# Place order
# ---------------------------------------------------------------------------


def place_order(
    db: Session,
    provider_url: str,
    supplier_name: str,
    product_id: str,
    quantity: int,
    current_day: int,
) -> dict:
    """Place an outbound order with a provider and record it locally.

    Steps:
        1. ``POST {provider_url}/api/orders`` with body
           ``{"buyer": "manufacturer", "product_id": ..., "quantity": ...}``.
        2. Insert a row into ``outbound_purchase_orders`` mirroring the
           provider's response (id, prices, days, status).
        3. Append a ``purchase_order_placed`` row to the local ``events``
           table.
        4. Return a dict representation of the local row.

    Raises:
        ValueError: ``quantity`` is not positive.
        ProviderUnreachableError: provider is offline or refusing connections.
        ProviderHTTPError: provider returned 4xx/5xx; the upstream
            ``detail`` is preserved so the operator sees the real reason.
    """
    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    payload = {
        "buyer": "manufacturer",
        "product_id": product_id,
        "quantity": quantity,
    }
    order_url = f"{provider_url.rstrip('/')}/api/orders"

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(order_url, json=payload)
    except httpx.RequestError as exc:
        message = f"Provider {provider_url} unreachable: {exc}"
        _log_event(
            db,
            day=current_day,
            event_type="provider_sync_error",
            entity_type="provider",
            entity_id=supplier_name,
            description=message,
            metadata={"provider_url": provider_url, "supplier": supplier_name},
        )
        db.commit()
        raise ProviderUnreachableError(message) from exc

    if response.status_code >= 400:
        detail = _extract_detail(response)
        _log_event(
            db,
            day=current_day,
            event_type="provider_sync_error",
            entity_type="provider",
            entity_id=supplier_name,
            description=(
                f"Provider {supplier_name!r} returned HTTP "
                f"{response.status_code} for /api/orders: {detail}"
            ),
            metadata={
                "provider_url": provider_url,
                "supplier": supplier_name,
                "status_code": response.status_code,
                "detail": detail,
            },
        )
        db.commit()
        raise ProviderHTTPError(response.status_code, detail)

    provider_order = response.json()

    # Best-effort lookup of the human-readable product name for events.
    product_name = product_id
    try:
        catalog = get_catalog(provider_url)
        match = next((p for p in catalog if p.get("id") == product_id), None)
        if match is not None:
            product_name = match.get("name", product_id)
    except ProviderError:
        # Catalog fetch is just a UX nicety — never fail the order
        # over a follow-up GET that flaked.
        pass

    outbound = OutboundPurchaseOrderRow(
        id=str(uuid.uuid4()),
        provider_name=supplier_name,
        provider_order_id=str(provider_order["id"]),
        product_name=product_name,
        quantity=int(provider_order["quantity"]),
        unit_price=float(provider_order.get("unit_price", 0.0) or 0.0),
        total_price=float(provider_order.get("total_price", 0.0) or 0.0),
        placed_day=int(provider_order["placed_day"]),
        expected_delivery_day=int(provider_order["expected_delivery_day"]),
        delivered_day=None,
        status=provider_order.get("status", "pending"),
    )
    db.add(outbound)
    _log_event(
        db,
        day=current_day,
        event_type="purchase_order_placed",
        entity_type="outbound_purchase_order",
        entity_id=outbound.id,
        description=(
            f"Day {current_day}: Placed PO with {supplier_name} for "
            f"{outbound.quantity}x {product_name} @ {outbound.unit_price}/unit "
            f"(total {outbound.total_price}); provider order "
            f"{outbound.provider_order_id}, expected delivery day "
            f"{outbound.expected_delivery_day}"
        ),
        metadata={
            "provider_name": supplier_name,
            "provider_order_id": outbound.provider_order_id,
            "product_id": product_id,
            "product_name": product_name,
            "quantity": outbound.quantity,
            "unit_price": outbound.unit_price,
            "total_price": outbound.total_price,
            "expected_delivery_day": outbound.expected_delivery_day,
        },
    )
    db.commit()
    db.refresh(outbound)
    return _serialize(outbound)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def list_purchase_orders(db: Session, status: Optional[str] = None) -> list[dict]:
    """Return all locally-tracked outbound POs, optionally filtered by status."""
    query = db.query(OutboundPurchaseOrderRow)
    if status is not None:
        query = query.filter(OutboundPurchaseOrderRow.status == status)
    rows = query.order_by(
        OutboundPurchaseOrderRow.placed_day,
        OutboundPurchaseOrderRow.id,
    ).all()
    return [_serialize(row) for row in rows]


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def check_deliveries(db: Session) -> list[dict]:
    """Poll the provider for each pending outbound PO and reconcile deliveries.

    Behaviour:

    - For every locally tracked PO whose status is not yet
      ``"delivered"``, ``GET {provider_url}/api/orders/{provider_order_id}``.
    - Mirror the provider's status onto the local row.
    - When the provider reports ``delivered`` (and we hadn't recorded it
      yet), increment the matching :class:`ProductRow`'s ``current_stock``
      and write a ``purchase_order_delivered`` event.
    - On network errors or HTTP 5xx: log a warning event and skip the
      provider for this tick.
    - On HTTP 4xx (e.g. 404): the provider has lost the order; log a
      warning event with the provider's detail and skip.

    Returns a list of dicts (one per *newly* delivered order this call)
    so the caller can produce summary log lines.
    """
    rows = (
        db.query(OutboundPurchaseOrderRow)
        .filter(OutboundPurchaseOrderRow.status != "delivered")
        .all()
    )
    if not rows:
        return []

    providers = {entry["name"]: entry["url"] for entry in list_providers()}
    current_day = _read_current_day(db)
    delivered: list[dict] = []

    for outbound in rows:
        provider_url = providers.get(outbound.provider_name)
        if provider_url is None:
            _warn(
                db,
                current_day,
                outbound.provider_name,
                f"No configured URL for provider {outbound.provider_name!r}; skipping",
            )
            continue

        url = f"{provider_url}/api/orders/{outbound.provider_order_id}"
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = client.get(url)
        except httpx.RequestError as exc:
            _warn(
                db,
                current_day,
                outbound.provider_name,
                f"Provider {provider_url} unreachable, skipping: {exc}",
            )
            continue

        if response.status_code >= 500:
            _warn(
                db,
                current_day,
                outbound.provider_name,
                f"Provider {provider_url} returned 5xx, skipping: {response.status_code}",
            )
            continue
        if response.status_code >= 400:
            _warn(
                db,
                current_day,
                outbound.provider_name,
                (
                    f"Provider returned {response.status_code} polling order "
                    f"{outbound.provider_order_id}: {_extract_detail(response)}"
                ),
            )
            continue

        provider_order = response.json()
        provider_status = provider_order.get("status", outbound.status)
        outbound.status = provider_status

        if provider_status != "delivered":
            continue

        # Newly delivered: bump local inventory and record event.
        part = (
            db.query(ProductRow)
            .filter(ProductRow.name == outbound.product_name)
            .first()
        )
        if part is None:
            _warn(
                db,
                current_day,
                outbound.provider_name,
                (
                    f"Delivered PO {outbound.provider_order_id} could not be "
                    f"mapped to local product {outbound.product_name!r}; "
                    f"stock NOT incremented"
                ),
            )
            continue

        part.current_stock += outbound.quantity
        outbound.delivered_day = int(provider_order.get("delivered_day", current_day))

        _log_event(
            db,
            day=current_day,
            event_type="purchase_order_delivered",
            entity_type="outbound_purchase_order",
            entity_id=outbound.id,
            description=(
                f"Day {current_day}: Received {outbound.quantity}x "
                f"{outbound.product_name} from {outbound.provider_name} "
                f"(stock now {part.current_stock})"
            ),
            metadata={
                "provider_name": outbound.provider_name,
                "provider_order_id": outbound.provider_order_id,
                "product_name": outbound.product_name,
                "quantity_received": outbound.quantity,
                "new_stock": part.current_stock,
                "delivered_day": outbound.delivered_day,
            },
        )
        delivered.append(_serialize(outbound))

    db.commit()
    return delivered


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _serialize(row: OutboundPurchaseOrderRow) -> dict:
    """Render an outbound PO row as a plain dict for CLI/API output."""
    return {
        "id": row.id,
        "supplier_name": row.provider_name,
        "provider_order_id": row.provider_order_id,
        "product_name": row.product_name,
        "quantity": row.quantity,
        "unit_price": float(row.unit_price or 0.0),
        "total_price": float(row.total_price or 0.0),
        "placed_day": row.placed_day,
        "expected_delivery_day": row.expected_delivery_day,
        "delivered_day": row.delivered_day,
        "status": row.status,
    }


def _read_current_day(db: Session) -> int:
    row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    return int(row.value) if row else 0


def _log_event(
    db: Session,
    *,
    day: int,
    event_type: str,
    entity_type: str,
    entity_id: str,
    description: str,
    metadata: Optional[dict] = None,
) -> None:
    db.add(
        EventRow(
            id=str(uuid.uuid4()),
            day=day,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            description=description,
            event_metadata=metadata or {},
        )
    )


def _warn(db: Session, day: int, supplier: str, message: str) -> None:
    """Log a non-fatal provider issue both to stdlib logging and to the events table."""
    _log.warning(message)
    _log_event(
        db,
        day=day,
        event_type="provider_sync_error",
        entity_type="provider",
        entity_id=supplier,
        description=message,
        metadata={"provider": supplier, "detail": message},
    )


def _extract_detail(response: httpx.Response) -> str:
    """Pull the ``detail`` string out of a provider error response.

    Falls back to the raw text if the response isn't valid JSON.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        return detail if isinstance(detail, str) else json.dumps(detail)
    return response.text or response.reason_phrase
