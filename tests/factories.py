"""Cut-down WooCommerce API payloads. Real responses carry far more fields; the tests also check
that the extra ones are dropped."""

from typing import Any

STORE_URL = "https://shop.example"
API = f"{STORE_URL}/wp-json/wc/v3"

ADDRESS = {
    "first_name": "Ann",
    "last_name": "Lee",
    "company": "",
    "address_1": "1 Main St",
    "address_2": "",
    "city": "Antioch",
    "state": "CA",
    "postcode": "94509",
    "country": "US",
    "email": "ann@example.com",
    "phone": "",
}


def line_item(
    product_id: int, name: str, quantity: int, total: str, sku: str = "", variation_id: int = 0
) -> dict[str, Any]:
    return {
        "id": product_id * 100,
        "product_id": product_id,
        "variation_id": variation_id,
        "name": name,
        "sku": sku,
        "quantity": quantity,
        "subtotal": total,
        "total": total,
        "taxes": [],
        "meta_data": [],
    }


def order(
    id: int = 1,
    total: str = "40.00",
    status: str = "completed",
    items: list[dict[str, Any]] | None = None,
    refunds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if items is None:
        items = [
            line_item(product_id=10, name="Mug", quantity=2, total="30.00", sku="MUG-1"),
            line_item(product_id=11, name="Hat", quantity=1, total="10.00"),
        ]
    return {
        "id": id,
        "number": str(id),
        "status": status,
        "currency": "USD",
        "total": total,
        "date_created": "2026-10-01T10:00:00",
        "date_paid": "2026-10-01T10:01:00",
        "date_completed": None,
        "customer_id": 7,
        "payment_method_title": "Card",
        "shipping_total": "0.00",
        "total_tax": "0.00",
        "discount_total": "0.00",
        "billing": ADDRESS,
        "shipping": {**ADDRESS, "email": "", "address_1": "2 Side St"},
        "line_items": items,
        "shipping_lines": [{"id": 5, "method_title": "Flat rate", "total": "0.00"}],
        "refunds": refunds or [],
        "customer_note": "",
        "meta_data": [{"id": 1, "key": "_internal", "value": "x"}],
        "_links": {"self": [{"href": f"{API}/orders/{id}"}]},
    }


def product(
    id: int = 10,
    name: str = "Mug",
    type: str = "simple",
    stock_quantity: int | None = 12,
    manage_stock: bool = True,
    stock_status: str = "instock",
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "slug": name.lower(),
        "sku": f"{name.upper()}-1",
        "type": type,
        "status": "publish",
        "permalink": f"{STORE_URL}/product/{name.lower()}/",
        "price": "15.00",
        "regular_price": "15.00",
        "sale_price": "",
        "on_sale": False,
        "stock_status": stock_status,
        "manage_stock": manage_stock,
        "stock_quantity": stock_quantity,
        "categories": [{"id": 12, "name": "Drinkware", "slug": "drinkware"}],
        "total_sales": 40,
        "date_modified": "2026-09-30T08:00:00",
        "description": "<p>Long HTML</p>",
        "meta_data": [],
        "_links": {},
    }


def variation(id: int, option: str, stock_quantity: int, stock_status: str = "instock") -> dict[str, Any]:
    return {
        "id": id,
        "sku": f"TEE-{option}",
        "price": "20.00",
        "stock_status": stock_status,
        "manage_stock": True,
        "stock_quantity": stock_quantity,
        "attributes": [{"id": 0, "name": "Size", "option": option}],
        "meta_data": [],
    }


def customer() -> dict[str, Any]:
    return {
        "id": 7,
        "email": "ann@example.com",
        "first_name": "Ann",
        "last_name": "Lee",
        "username": "ann",
        "role": "customer",
        "date_created": "2025-01-05T12:00:00",
        "is_paying_customer": True,
        "billing": {**ADDRESS, "phone": "555-0100"},
        "shipping": ADDRESS,
        "avatar_url": "https://secure.gravatar.com/avatar/x",
        "meta_data": [],
    }


def wc_error(code: str, message: str, status: int) -> dict[str, Any]:
    return {"code": code, "message": message, "data": {"status": status}}
