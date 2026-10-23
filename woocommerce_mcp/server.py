"""MCP server that exposes one WooCommerce store as tools and a resource."""

from __future__ import annotations

import functools
import json
import os
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .client import PER_PAGE_MAX, WooCommerceClient, WooCommerceError

REQUIRED_ENV = ("WC_URL", "WC_CONSUMER_KEY", "WC_CONSUMER_SECRET")
TRUE_VALUES = {"1", "true", "yes", "on"}

Json = dict[str, Any]
ToolFn = Callable[..., Awaitable[Json]]


class ConfigError(Exception):
    """The environment is missing something the server needs to start."""


@dataclass(frozen=True)
class Settings:
    url: str
    consumer_key: str
    consumer_secret: str
    allow_writes: bool = False


def settings_from_env(env: Mapping[str, str] = os.environ) -> Settings:
    missing = [name for name in REQUIRED_ENV if not env.get(name, "").strip()]
    if missing:
        raise ConfigError(
            f"missing environment variable(s): {', '.join(missing)}. "
            "Create a REST API key under WooCommerce > Settings > Advanced > REST API and set "
            "WC_URL, WC_CONSUMER_KEY and WC_CONSUMER_SECRET."
        )
    url = env["WC_URL"].strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise ConfigError(f"WC_URL must start with https:// (got {url!r})")
    return Settings(
        url=url,
        consumer_key=env["WC_CONSUMER_KEY"].strip(),
        consumer_secret=env["WC_CONSUMER_SECRET"].strip(),
        allow_writes=env.get("WC_ALLOW_WRITES", "").strip().lower() in TRUE_VALUES,
    )


def build_server(settings: Settings, client: WooCommerceClient) -> MCPServer:
    """Wire the tools and the store resource to one client. Does not start anything."""

    @asynccontextmanager
    async def close_client_on_exit(_server: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()

    writes = "enabled" if settings.allow_writes else "disabled"
    mcp = MCPServer(
        "woocommerce",
        version=__version__,
        instructions=(
            f"Tools for the WooCommerce store at {settings.url}. Money values are strings in the store "
            f"currency, exactly as WooCommerce reports them. Dates are in the store's timezone. "
            f"Writes (update_stock, update_order_status) are {writes}."
        ),
        lifespan=close_client_on_exit,
    )
    read_only = ToolAnnotations(read_only_hint=True)

    def require_writes(tool_name: str) -> None:
        if not settings.allow_writes:
            raise ToolError(
                f"Writes are disabled, so {tool_name} did nothing. Start the server with "
                "WC_ALLOW_WRITES=true and a Read/Write API key to enable it."
            )

    async def category_id(category: str) -> int:
        if category.isdigit():
            return int(category)
        matches = await client.get("products/categories", {"search": category, "per_page": 10})
        wanted = category.lower()
        exact = [c for c in matches if c.get("name", "").lower() == wanted or c.get("slug") == wanted]
        chosen = exact or matches
        if not chosen:
            raise ToolError(f"No product category matches {category!r}")
        return int(chosen[0]["id"])

    @mcp.tool(annotations=read_only)
    @_wc_errors_as_tool_errors
    async def list_orders(
        status: str | None = None,
        after: str | None = None,
        before: str | None = None,
        customer: str | None = None,
        limit: int = 20,
    ) -> Json:
        """Recent orders, newest first. Filter by status (pending, processing, on-hold, completed,
        cancelled, refunded, failed, or several comma-separated), a date range (YYYY-MM-DD or ISO
        8601), and a customer (numeric customer id, or an email or name to search for)."""
        params: Json = {"per_page": _per_page(limit)}
        if status:
            params["status"] = status
        if after:
            params["after"] = _iso_datetime(after, end_of_day=False)
        if before:
            params["before"] = _iso_datetime(before, end_of_day=True)
        if customer:
            params["customer" if customer.isdigit() else "search"] = customer
        page = await client.get_page("orders", params)
        return {
            "count": len(page.items),
            "total_matching": page.total,
            "orders": [_order_summary(o) for o in page.items],
        }

    @mcp.tool(annotations=read_only)
    @_wc_errors_as_tool_errors
    async def get_order(order_id: int) -> Json:
        """One order in full: line items, addresses, totals, shipping, refunds and the customer note."""
        return _order_detail(await client.get(f"orders/{order_id}"))

    @mcp.tool(annotations=read_only)
    @_wc_errors_as_tool_errors
    async def list_products(
        search: str | None = None,
        sku: str | None = None,
        stock_status: str | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> Json:
        """Products with price and stock. Filter by a search term, an exact SKU, stock_status
        (instock, outofstock, onbackorder) or a category (id or name)."""
        params: Json = {"per_page": _per_page(limit)}
        if search:
            params["search"] = search
        if sku:
            params["sku"] = sku
        if stock_status:
            params["stock_status"] = stock_status
        if category:
            params["category"] = await category_id(category)
        page = await client.get_page("products", params)
        return {
            "count": len(page.items),
            "total_matching": page.total,
            "products": [_product(p) for p in page.items],
        }

    @mcp.tool(annotations=read_only)
    @_wc_errors_as_tool_errors
    async def get_product(product_id: int) -> Json:
        """One product with price, stock and categories. For a variable product the variations are
        included with their own SKU, price and stock."""
        product = await client.get(f"products/{product_id}")
        result = _product(product)
        if product.get("type") == "variable":
            variations, _ = await client.get_all(f"products/{product_id}/variations", {})
            result["variations"] = [_variation(v, product) for v in variations]
        return result

    @mcp.tool(annotations=read_only)
    @_wc_errors_as_tool_errors
    async def list_customers(search: str | None = None, limit: int = 20) -> Json:
        """Registered customers, optionally matching a name, email or username. Guest checkouts
        are not customers; find those through list_orders."""
        params: Json = {"per_page": _per_page(limit)}
        if search:
            params["search"] = search
        page = await client.get_page("customers", params)
        return {
            "count": len(page.items),
            "total_matching": page.total,
            "customers": [_customer(c) for c in page.items],
        }

    @mcp.tool(annotations=read_only)
    @_wc_errors_as_tool_errors
    async def low_stock_report(threshold: int = 5, include_variations: bool = False) -> Json:
        """Published products whose tracked stock is at or below the threshold, plus anything marked
        out of stock. Walks the whole catalog. Set include_variations to also check each variable
        product's variations, at the cost of one extra request per variable product.

        Items with no stock quantity to compare (stock management off) are never counted as low.
        They are counted in untracked_count instead, with up to 10 of them in untracked_examples.
        variable_products_unchecked is how many variable products had their variations skipped
        because include_variations was off."""
        products, truncated = await client.get_all("products", {"status": "publish"})
        low: list[Json] = []
        untracked: list[Json] = []
        unchecked = 0
        for product in products:
            is_variable = product.get("type") == "variable"
            if _is_low(product, threshold):
                low.append(_stock_row(product))
            elif not is_variable and _is_untracked(product):
                untracked.append(_stock_row(product))
            if not is_variable:
                continue
            if not include_variations:
                unchecked += 1
                continue
            variations, _ = await client.get_all(f"products/{product['id']}/variations", {})
            for v in variations:
                if _is_low(v, threshold):
                    low.append(_stock_row(v, product))
                elif _is_untracked(v):
                    untracked.append(_stock_row(v, product))
        low.sort(key=lambda row: (row["stock_quantity"] is None, row["stock_quantity"] or 0))
        return {
            "threshold": threshold,
            "products_scanned": len(products),
            "truncated": truncated,
            "count": len(low),
            "items": low,
            "untracked_count": len(untracked),
            "untracked_examples": untracked[:10],
            "variable_products_unchecked": unchecked,
        }

    @mcp.tool(annotations=read_only)
    @_wc_errors_as_tool_errors
    async def sales_summary(
        after: str, before: str, status: str = "completed,processing", top: int = 10
    ) -> Json:
        """Order count, revenue, refunds, average order value and the best-selling products between
        two dates (YYYY-MM-DD, inclusive). Counts orders in the given statuses. Walks every matching
        order, so a wide range takes several requests."""
        params = {
            "after": _iso_datetime(after, end_of_day=False),
            "before": _iso_datetime(before, end_of_day=True),
            "status": status,
        }
        orders, truncated = await client.get_all("orders", params)

        revenue = sum((_money(o.get("total")) for o in orders), Decimal(0))
        refunded = sum(
            (abs(_money(r.get("total"))) for o in orders for r in o.get("refunds", [])), Decimal(0)
        )
        quantities: Counter[Any] = Counter()
        product_revenue: defaultdict[Any, Decimal] = defaultdict(Decimal)
        names: dict[Any, str] = {}
        for order in orders:
            for item in order.get("line_items", []):
                key = item.get("product_id")
                quantities[key] += int(item.get("quantity") or 0)
                product_revenue[key] += _money(item.get("total"))
                names.setdefault(key, item.get("name", ""))

        count = len(orders)
        return {
            "after": params["after"],
            "before": params["before"],
            "statuses": status,
            "order_count": count,
            "orders_by_status": dict(Counter(o.get("status") for o in orders)),
            "currency": orders[0].get("currency") if orders else None,
            "revenue": str(revenue),
            "refunded": str(refunded),
            "average_order_value": str((revenue / count).quantize(revenue)) if count else "0",
            "top_products": [
                {"product_id": pid, "name": names[pid], "quantity": qty, "revenue": str(product_revenue[pid])}
                for pid, qty in quantities.most_common(top)
            ],
            "truncated": truncated,
        }

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))
    @_wc_errors_as_tool_errors
    async def update_stock(product_id: int, quantity: int, variation_id: int | None = None) -> Json:
        """Set a product's stock quantity and turn on stock management for it. Pass variation_id to
        set one variation of a variable product. Needs WC_ALLOW_WRITES=true."""
        require_writes("update_stock")
        path = f"products/{product_id}"
        if variation_id is not None:
            path += f"/variations/{variation_id}"
        updated = await client.put(path, {"manage_stock": True, "stock_quantity": quantity})
        return _stock_row(updated, {"id": product_id} if variation_id is not None else None)

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True))
    @_wc_errors_as_tool_errors
    async def update_order_status(order_id: int, status: str) -> Json:
        """Change an order's status (pending, processing, on-hold, completed, cancelled, refunded,
        failed). WooCommerce runs its usual side effects, such as emailing the customer. Setting
        refunded only relabels the order; it does not send money back. Needs WC_ALLOW_WRITES=true."""
        require_writes("update_order_status")
        updated = await client.put(f"orders/{order_id}", {"status": status})
        return _order_summary(updated)

    @mcp.resource(
        "woocommerce://store",
        name="store",
        description="Store overview: versions, currency, timezone and whether writes are enabled.",
        mime_type="application/json",
    )
    async def store() -> str:
        info: Json = {"url": settings.url, "writes_enabled": settings.allow_writes}
        try:
            status = await client.get("system_status")
        except WooCommerceError as exc:
            info["note"] = f"system_status is not available for this key: {exc}"
            return json.dumps(info, indent=2)
        environment = status.get("environment") or {}
        store_settings = status.get("settings") or {}
        info.update(
            {
                "site_url": environment.get("site_url"),
                "woocommerce_version": environment.get("version"),
                "wordpress_version": environment.get("wp_version"),
                "timezone": environment.get("default_timezone"),
                "currency": store_settings.get("currency"),
                "currency_symbol": store_settings.get("currency_symbol"),
                "decimals": store_settings.get("number_of_decimals"),
            }
        )
        return json.dumps(info, indent=2)

    return mcp


def _wc_errors_as_tool_errors(fn: ToolFn) -> ToolFn:
    """Turn a client failure into the readable error MCP clients expect, instead of a crash."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Json:
        try:
            return await fn(*args, **kwargs)
        except WooCommerceError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _per_page(limit: int) -> int:
    return max(1, min(limit, PER_PAGE_MAX))


def _iso_datetime(value: str, *, end_of_day: bool) -> str:
    text = value.strip()
    if len(text) == 10:
        text += "T23:59:59" if end_of_day else "T00:00:00"
    try:
        datetime.fromisoformat(text)
    except ValueError:
        raise ToolError(f"Bad date {value!r}: use YYYY-MM-DD or an ISO 8601 datetime") from None
    return text


def _money(value: Any) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal(0)


def _full_name(address: Json) -> str:
    return " ".join(part for part in (address.get("first_name"), address.get("last_name")) if part)


def _address(address: Json) -> Json:
    keys = ("company", "address_1", "address_2", "city", "state", "postcode", "country", "phone")
    return {key: address[key] for key in keys if address.get(key)}


def _order_summary(order: Json) -> Json:
    billing = order.get("billing") or {}
    return {
        "id": order.get("id"),
        "number": order.get("number"),
        "status": order.get("status"),
        "date_created": order.get("date_created"),
        "currency": order.get("currency"),
        "total": order.get("total"),
        "customer_id": order.get("customer_id"),
        "customer_name": _full_name(billing),
        "customer_email": billing.get("email"),
        "item_count": sum(int(item.get("quantity") or 0) for item in order.get("line_items", [])),
        "payment_method": order.get("payment_method_title"),
    }


def _order_detail(order: Json) -> Json:
    detail = _order_summary(order)
    detail.update(
        {
            "date_paid": order.get("date_paid"),
            "date_completed": order.get("date_completed"),
            "shipping_total": order.get("shipping_total"),
            "total_tax": order.get("total_tax"),
            "discount_total": order.get("discount_total"),
            "shipping_method": ", ".join(
                line.get("method_title", "") for line in order.get("shipping_lines", [])
            ),
            "billing_address": _address(order.get("billing") or {}),
            "shipping_address": _address(order.get("shipping") or {}),
            "line_items": [
                {
                    "product_id": item.get("product_id"),
                    "variation_id": item.get("variation_id"),
                    "name": item.get("name"),
                    "sku": item.get("sku"),
                    "quantity": item.get("quantity"),
                    "total": item.get("total"),
                }
                for item in order.get("line_items", [])
            ],
            "refunds": [
                {"id": r.get("id"), "reason": r.get("reason"), "total": r.get("total")}
                for r in order.get("refunds", [])
            ],
            "customer_note": order.get("customer_note") or None,
        }
    )
    return detail


def _customer(customer: Json) -> Json:
    billing = customer.get("billing") or {}
    return {
        "id": customer.get("id"),
        "email": customer.get("email"),
        "first_name": customer.get("first_name"),
        "last_name": customer.get("last_name"),
        "username": customer.get("username"),
        "date_created": customer.get("date_created"),
        "is_paying_customer": customer.get("is_paying_customer"),
        "phone": billing.get("phone"),
        "city": billing.get("city"),
        "country": billing.get("country"),
    }


def _product(product: Json) -> Json:
    return {
        "id": product.get("id"),
        "name": product.get("name"),
        "sku": product.get("sku"),
        "type": product.get("type"),
        "status": product.get("status"),
        "price": product.get("price"),
        "regular_price": product.get("regular_price"),
        "sale_price": product.get("sale_price"),
        "on_sale": product.get("on_sale"),
        "stock_status": product.get("stock_status"),
        "manage_stock": product.get("manage_stock"),
        "stock_quantity": product.get("stock_quantity"),
        "categories": [c.get("name") for c in product.get("categories", [])],
        "total_sales": product.get("total_sales"),
        "permalink": product.get("permalink"),
        "date_modified": product.get("date_modified"),
    }


def _variation_name(variation: Json, parent: Json) -> str:
    options = ", ".join(str(a.get("option")) for a in variation.get("attributes", []) if a.get("option"))
    parent_name = parent.get("name") or ""
    if parent_name and options:
        return f"{parent_name} ({options})"
    return parent_name or variation.get("name") or options


def _variation(variation: Json, parent: Json) -> Json:
    return {
        "id": variation.get("id"),
        "name": _variation_name(variation, parent),
        "sku": variation.get("sku"),
        "price": variation.get("price"),
        "stock_status": variation.get("stock_status"),
        "manage_stock": variation.get("manage_stock"),
        "stock_quantity": variation.get("stock_quantity"),
    }


def _is_low(item: Json, threshold: int) -> bool:
    if item.get("stock_status") == "outofstock":
        return True
    quantity = item.get("stock_quantity")
    return bool(item.get("manage_stock")) and quantity is not None and quantity <= threshold


def _is_untracked(item: Json) -> bool:
    """In stock as far as WooCommerce says, but with no quantity the threshold can be checked against."""
    if item.get("stock_status") == "outofstock":
        return False
    return not item.get("manage_stock") or item.get("stock_quantity") is None


def _stock_row(item: Json, parent: Json | None = None) -> Json:
    """Stock fields for a product, or for a variation when its parent product is given."""
    row = {
        "id": item.get("id"),
        "name": _variation_name(item, parent) if parent is not None else item.get("name"),
        "sku": item.get("sku"),
        "stock_status": item.get("stock_status"),
        "manage_stock": item.get("manage_stock"),
        "stock_quantity": item.get("stock_quantity"),
    }
    if parent is not None:
        row["parent_id"] = parent.get("id")
    return row
