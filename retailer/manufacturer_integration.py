"""HTTP integration layer for calls from the retailer to the manufacturer."""
from __future__ import annotations

import httpx

_TIMEOUT = 8.0


def fetch_manufacturer_catalog(manufacturer_url: str) -> list[dict]:
    resp = httpx.get(f"{manufacturer_url}/api/catalog", timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def place_manufacturer_order(
    manufacturer_url: str,
    retailer_name: str,
    model: str,
    quantity: int,
) -> dict:
    resp = httpx.post(
        f"{manufacturer_url}/api/orders",
        json={"retailer_name": retailer_name, "model": model, "quantity": quantity},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def poll_manufacturer_order(manufacturer_url: str, order_id: str) -> dict:
    resp = httpx.get(f"{manufacturer_url}/api/orders/{order_id}", timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()
