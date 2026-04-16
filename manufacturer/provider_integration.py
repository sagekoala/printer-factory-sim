"""HTTP integration with external provider services."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

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
    from database import EventRow, FactoryConfigRow, OutboundPurchaseOrderRow, ProductRow

_PROVIDER_CONFIG = Path(__file__).parent / "provider_config.json"
_REQUEST_TIMEOUT_SECONDS = 8.0


def list_configured_suppliers() -> list[dict[str, str]]:
    config = _load_provider_config()
    return config["manufacturer"].get("providers", [])


def fetch_supplier_catalog(supplier_name: str) -> list[dict]:
    supplier = _get_supplier_or_raise(supplier_name)
    url = f"{supplier['url'].rstrip('/')}/api/catalog"
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as exc:
        raise RuntimeError(
            f"Provider '{supplier_name}' is unreachable at {supplier['url']}: {exc}"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Provider '{supplier_name}' returned HTTP {exc.response.status_code} for /api/catalog"
        ) from exc


def create_outbound_purchase(
    db: Session,
    supplier_name: str,
    product_id: str,
    quantity: int,
) -> dict:
    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    supplier = _get_supplier_or_raise(supplier_name)
    catalog = fetch_supplier_catalog(supplier_name)
    product = next((p for p in catalog if p.get("id") == product_id), None)
    if product is None:
        raise ValueError(f"Product {product_id!r} not found in supplier catalog for {supplier_name!r}")

    current_day = _current_day(db)
    payload = {
        "buyer": "manufacturer",
        "product_id": product_id,
        "quantity": quantity,
    }

    order_url = f"{supplier['url'].rstrip('/')}/api/orders"
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(order_url, json=payload)
            response.raise_for_status()
            provider_order = response.json()
    except httpx.RequestError as exc:
        message = f"Provider '{supplier_name}' is unreachable at {supplier['url']}: {exc}"
        _log_provider_error(db, current_day, supplier_name, message)
        db.commit()
        raise RuntimeError(message) from exc
    except httpx.HTTPStatusError as exc:
        message = (
            f"Provider '{supplier_name}' returned HTTP {exc.response.status_code} for /api/orders"
        )
        _log_provider_error(db, current_day, supplier_name, message)
        db.commit()
        raise RuntimeError(message) from exc

    outbound = OutboundPurchaseOrderRow(
        id=str(uuid.uuid4()),
        provider_name=supplier_name,
        provider_order_id=provider_order["id"],
        product_name=product.get("name", product_id),
        quantity=int(provider_order["quantity"]),
        placed_day=int(provider_order["placed_day"]),
        expected_delivery_day=int(provider_order["expected_delivery_day"]),
        status=provider_order["status"],
    )
    db.add(outbound)
    db.add(
        EventRow(
            day=current_day,
            event_type="OUTBOUND_PURCHASE_CREATED",
            entity_type="outbound_purchase_order",
            entity_id=outbound.id,
            description=(
                f"Day {current_day}: Outbound purchase created with {supplier_name} "
                f"for {outbound.quantity}x {outbound.product_name}; "
                f"provider order {outbound.provider_order_id}"
            ),
            event_metadata={
                "provider_name": supplier_name,
                "provider_order_id": outbound.provider_order_id,
                "product_id": product_id,
                "product_name": outbound.product_name,
                "quantity": outbound.quantity,
                "expected_delivery_day": outbound.expected_delivery_day,
            },
        )
    )
    db.commit()
    db.refresh(outbound)
    return _serialize_outbound(outbound)


def list_outbound_purchase_orders(db: Session) -> list[dict]:
    rows = (
        db.query(OutboundPurchaseOrderRow)
        .order_by(OutboundPurchaseOrderRow.placed_day, OutboundPurchaseOrderRow.id)
        .all()
    )
    return [_serialize_outbound(row) for row in rows]


def sync_outbound_purchase_orders(db: Session, day: int) -> None:
    rows = (
        db.query(OutboundPurchaseOrderRow)
        .filter(OutboundPurchaseOrderRow.status != "delivered")
        .all()
    )
    if not rows:
        return

    providers = {entry["name"]: entry for entry in list_configured_suppliers()}

    for outbound in rows:
        supplier = providers.get(outbound.provider_name)
        if supplier is None:
            _log_provider_error(
                db,
                day,
                outbound.provider_name,
                f"No configured URL found for provider '{outbound.provider_name}'",
            )
            continue

        order_url = (
            f"{supplier['url'].rstrip('/')}/api/orders/{outbound.provider_order_id}"
        )
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = client.get(order_url)
                response.raise_for_status()
                provider_order = response.json()
        except httpx.RequestError as exc:
            _log_provider_error(
                db,
                day,
                outbound.provider_name,
                (
                    f"Failed polling provider order {outbound.provider_order_id} "
                    f"at {supplier['url']}: {exc}"
                ),
            )
            continue
        except httpx.HTTPStatusError as exc:
            _log_provider_error(
                db,
                day,
                outbound.provider_name,
                (
                    f"Provider returned HTTP {exc.response.status_code} while polling "
                    f"order {outbound.provider_order_id}"
                ),
            )
            continue

        provider_status = provider_order.get("status", outbound.status)
        outbound.status = provider_status

        if provider_status != "delivered":
            continue

        part = db.query(ProductRow).filter(ProductRow.name == outbound.product_name).first()
        if part is None:
            _log_provider_error(
                db,
                day,
                outbound.provider_name,
                (
                    f"Delivered provider order {outbound.provider_order_id} could not be "
                    f"mapped to local product {outbound.product_name!r}"
                ),
            )
            continue

        part.current_stock += outbound.quantity
        db.add(
            EventRow(
                day=day,
                event_type="OUTBOUND_PURCHASE_DELIVERED",
                entity_type="outbound_purchase_order",
                entity_id=outbound.id,
                description=(
                    f"Day {day}: Provider delivery received from {outbound.provider_name} — "
                    f"+{outbound.quantity}x {outbound.product_name}"
                ),
                event_metadata={
                    "provider_name": outbound.provider_name,
                    "provider_order_id": outbound.provider_order_id,
                    "product_name": outbound.product_name,
                    "quantity_received": outbound.quantity,
                    "new_stock": part.current_stock,
                },
            )
        )


def _load_provider_config() -> dict:
    if not _PROVIDER_CONFIG.exists():
        raise RuntimeError(f"Provider config not found: {_PROVIDER_CONFIG}")
    return json.loads(_PROVIDER_CONFIG.read_text())


def _get_supplier_or_raise(supplier_name: str) -> dict[str, str]:
    suppliers = list_configured_suppliers()
    supplier = next((item for item in suppliers if item.get("name") == supplier_name), None)
    if supplier is None:
        raise ValueError(f"Unknown supplier: {supplier_name!r}")
    return supplier


def _current_day(db: Session) -> int:
    row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    return int(row.value) if row else 0


def _serialize_outbound(row: OutboundPurchaseOrderRow) -> dict:
    return {
        "id": row.id,
        "provider_name": row.provider_name,
        "provider_order_id": row.provider_order_id,
        "product_name": row.product_name,
        "quantity": row.quantity,
        "placed_day": row.placed_day,
        "expected_delivery_day": row.expected_delivery_day,
        "status": row.status,
    }


def _log_provider_error(db: Session, day: int, provider_name: str, detail: str) -> None:
    db.add(
        EventRow(
            day=day,
            event_type="PROVIDER_SYNC_ERROR",
            entity_type="provider",
            entity_id=provider_name,
            description=detail,
            event_metadata={"provider_name": provider_name, "detail": detail},
        )
    )
