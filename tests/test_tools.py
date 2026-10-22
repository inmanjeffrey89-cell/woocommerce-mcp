import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.factories import STORE_URL, customer, line_item, order, product, variation, wc_error

pytestmark = pytest.mark.anyio

ALL_TOOLS = {
    "list_orders",
    "get_order",
    "list_products",
    "get_product",
    "list_customers",
    "low_stock_report",
    "sales_summary",
    "update_stock",
    "update_order_status",
}


async def test_tool_catalog(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == ALL_TOOLS
    for name, tool in tools.items():
        assert tool.description, name
        assert tool.annotations.read_only_hint is not name.startswith("update_"), name
    assert tools["update_order_status"].annotations.destructive_hint is True
    assert tools["update_stock"].annotations.destructive_hint is False


async def test_list_orders_forwards_filters_and_trims_fields(api, call):
    route = api.get("/orders").respond(
        200,
        json=[order(id=1), order(id=2, status="processing", total="12.50")],
        headers={"X-WP-Total": "2", "X-WP-TotalPages": "1"},
    )
    result = await call(
        "list_orders",
        status="completed,processing",
        after="2026-10-01",
        before="2026-10-07",
        customer="7",
        limit=500,
    )
    params = route.calls.last.request.url.params
    assert dict(params) == {
        "per_page": "100",
        "status": "completed,processing",
        "after": "2026-10-01T00:00:00",
        "before": "2026-10-07T23:59:59",
        "customer": "7",
    }
    assert result["count"] == 2
    assert result["total_matching"] == 2
    assert result["orders"][0] == {
        "id": 1,
        "number": "1",
        "status": "completed",
        "date_created": "2026-10-01T10:00:00",
        "currency": "USD",
        "total": "40.00",
        "customer_id": 7,
        "customer_name": "Ann Lee",
        "customer_email": "ann@example.com",
        "item_count": 3,
        "payment_method": "Card",
    }
    assert result["orders"][1]["total"] == "12.50"


async def test_list_orders_searches_when_customer_is_not_an_id(api, call):
    route = api.get("/orders").respond(200, json=[])
    await call("list_orders", customer="ann@example.com", limit=0)
    params = route.calls.last.request.url.params
    assert params["search"] == "ann@example.com"
    assert "customer" not in params
    assert params["per_page"] == "1"


async def test_list_orders_rejects_a_bad_date_before_calling_the_store(api, call):
    route = api.get("/orders").respond(200, json=[])
    with pytest.raises(ToolError, match="Bad date 'last tuesday'"):
        await call("list_orders", after="last tuesday")
    assert not route.called


async def test_get_order_detail(api, call):
    api.get("/orders/1").respond(
        200, json=order(id=1, refunds=[{"id": 9, "reason": "damaged", "total": "-10.00"}])
    )
    result = await call("get_order", order_id=1)
    assert result["customer_name"] == "Ann Lee"
    assert result["line_items"] == [
        {"product_id": 10, "variation_id": 0, "name": "Mug", "sku": "MUG-1", "quantity": 2, "total": "30.00"},
        {"product_id": 11, "variation_id": 0, "name": "Hat", "sku": "", "quantity": 1, "total": "10.00"},
    ]
    assert result["billing_address"] == {
        "address_1": "1 Main St",
        "city": "Antioch",
        "state": "CA",
        "postcode": "94509",
        "country": "US",
    }
    assert result["shipping_address"]["address_1"] == "2 Side St"
    assert result["shipping_method"] == "Flat rate"
    assert result["refunds"] == [{"id": 9, "reason": "damaged", "total": "-10.00"}]
    assert result["customer_note"] is None
    assert result["date_paid"] == "2026-10-01T10:01:00"
    assert "meta_data" not in result
    assert "_links" not in result


async def test_get_order_missing_is_a_readable_error(api, call):
    api.get("/orders/999").respond(
        404, json=wc_error("woocommerce_rest_shop_order_invalid_id", "Invalid ID.", 404)
    )
    with pytest.raises(ToolError, match=r"Not found: /wp-json/wc/v3/orders/999 \(Invalid ID\.\)"):
        await call("get_order", order_id=999)


async def test_bad_keys_surface_through_a_tool(api, call):
    api.get("/products").respond(
        401, json=wc_error("woocommerce_rest_cannot_view", "Sorry, you cannot list resources.", 401)
    )
    with pytest.raises(ToolError, match="rejected the API keys"):
        await call("list_products")


async def test_network_error_surfaces_through_a_tool(api, call):
    api.get("/customers").mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(ToolError, match=r"Could not reach https://shop\.example: connection refused"):
        await call("list_customers")


async def test_argument_validation_is_a_readable_error(call):
    with pytest.raises(ToolError, match="order_id"):
        await call("get_order", order_id="abc")


async def test_list_products_forwards_filters_and_trims_fields(api, call):
    route = api.get("/products").respond(200, json=[product()], headers={"X-WP-Total": "1"})
    result = await call(
        "list_products", search="mug", sku="MUG-1", stock_status="instock", category="12", limit=5
    )
    assert dict(route.calls.last.request.url.params) == {
        "per_page": "5",
        "search": "mug",
        "sku": "MUG-1",
        "stock_status": "instock",
        "category": "12",
    }
    assert result["total_matching"] == 1
    assert result["products"] == [
        {
            "id": 10,
            "name": "Mug",
            "sku": "MUG-1",
            "type": "simple",
            "status": "publish",
            "price": "15.00",
            "regular_price": "15.00",
            "sale_price": "",
            "on_sale": False,
            "stock_status": "instock",
            "manage_stock": True,
            "stock_quantity": 12,
            "categories": ["Drinkware"],
            "total_sales": 40,
            "permalink": "https://shop.example/product/mug/",
            "date_modified": "2026-09-30T08:00:00",
        }
    ]


async def test_list_products_resolves_a_category_name(api, call):
    lookup = api.get("/products/categories").respond(
        200,
        json=[
            {"id": 3, "name": "Drinkware Accessories", "slug": "drinkware-accessories"},
            {"id": 12, "name": "Drinkware", "slug": "drinkware"},
        ],
    )
    route = api.get("/products").respond(200, json=[])
    await call("list_products", category="drinkware")
    assert lookup.calls.last.request.url.params["search"] == "drinkware"
    assert route.calls.last.request.url.params["category"] == "12"


async def test_list_products_unknown_category(api, call):
    api.get("/products/categories").respond(200, json=[])
    with pytest.raises(ToolError, match="No product category matches 'hats'"):
        await call("list_products", category="hats")


async def test_get_product_simple(api, call):
    api.get("/products/10").respond(200, json=product())
    result = await call("get_product", product_id=10)
    assert result["sku"] == "MUG-1"
    assert "variations" not in result
    assert "description" not in result


async def test_get_product_variable_includes_variations(api, call):
    api.get("/products/20").respond(
        200, json=product(id=20, name="Tee", type="variable", manage_stock=False, stock_quantity=None)
    )
    api.get("/products/20/variations").respond(
        200, json=[variation(21, "S", 3), variation(22, "L", 0, stock_status="outofstock")]
    )
    result = await call("get_product", product_id=20)
    assert result["type"] == "variable"
    assert result["variations"] == [
        {
            "id": 21,
            "name": "Tee (S)",
            "sku": "TEE-S",
            "price": "20.00",
            "stock_status": "instock",
            "manage_stock": True,
            "stock_quantity": 3,
        },
        {
            "id": 22,
            "name": "Tee (L)",
            "sku": "TEE-L",
            "price": "20.00",
            "stock_status": "outofstock",
            "manage_stock": True,
            "stock_quantity": 0,
        },
    ]


async def test_list_customers(api, call):
    route = api.get("/customers").respond(200, json=[customer()], headers={"X-WP-Total": "1"})
    result = await call("list_customers", search="ann", limit=5)
    assert dict(route.calls.last.request.url.params) == {"per_page": "5", "search": "ann"}
    assert result["customers"] == [
        {
            "id": 7,
            "email": "ann@example.com",
            "first_name": "Ann",
            "last_name": "Lee",
            "username": "ann",
            "date_created": "2025-01-05T12:00:00",
            "is_paying_customer": True,
            "phone": "555-0100",
            "city": "Antioch",
            "country": "US",
        }
    ]


async def test_low_stock_report_with_variations(api, call):
    products = api.get("/products").respond(
        200,
        json=[
            product(id=1, name="Mug", stock_quantity=2),
            product(id=2, name="Pen", stock_quantity=50),
            product(id=3, name="Cap", manage_stock=False, stock_quantity=None, stock_status="outofstock"),
            product(id=4, name="Tee", type="variable", manage_stock=False, stock_quantity=None),
            product(id=5, name="Card", manage_stock=False, stock_quantity=None),
        ],
    )
    api.get("/products/4/variations").respond(200, json=[variation(41, "S", 1), variation(42, "L", 9)])
    result = await call("low_stock_report", threshold=5, include_variations=True)
    assert products.calls.last.request.url.params["status"] == "publish"
    assert result["products_scanned"] == 5
    assert result["truncated"] is False
    assert result["count"] == 3
    assert [(row["id"], row["name"], row["stock_quantity"]) for row in result["items"]] == [
        (41, "Tee (S)", 1),
        (1, "Mug", 2),
        (3, "Cap", None),
    ]
    assert result["items"][0]["parent_id"] == 4
    assert "parent_id" not in result["items"][1]


async def test_low_stock_report_skips_variations_by_default(api, call):
    api.get("/products").respond(
        200, json=[product(id=4, name="Tee", type="variable", manage_stock=False, stock_quantity=None)]
    )
    variations = api.get("/products/4/variations").respond(200, json=[])
    result = await call("low_stock_report")
    assert result["threshold"] == 5
    assert result["count"] == 0
    assert not variations.called


async def test_sales_summary_walks_every_page(api, call):
    page_one = [order(id=i) for i in range(1, 101)]
    page_two = [
        order(
            id=101,
            total="25.00",
            status="processing",
            items=[line_item(product_id=12, name="Bowl", quantity=5, total="25.00")],
            refunds=[{"id": 5, "reason": "", "total": "-5.00"}],
        )
    ]

    def respond(request):
        page = int(request.url.params["page"])
        return httpx.Response(200, json=page_one if page == 1 else page_two, headers={"X-WP-TotalPages": "2"})

    route = api.get("/orders").mock(side_effect=respond)
    result = await call("sales_summary", after="2026-10-01", before="2026-10-31", top=2)

    assert route.call_count == 2
    first = route.calls[0].request.url.params
    assert dict(first) == {
        "after": "2026-10-01T00:00:00",
        "before": "2026-10-31T23:59:59",
        "status": "completed,processing",
        "per_page": "100",
        "page": "1",
    }
    assert result["order_count"] == 101
    assert result["orders_by_status"] == {"completed": 100, "processing": 1}
    assert result["currency"] == "USD"
    assert result["revenue"] == "4025.00"
    assert result["refunded"] == "5.00"
    assert result["average_order_value"] == "39.85"
    assert result["top_products"] == [
        {"product_id": 10, "name": "Mug", "quantity": 200, "revenue": "3000.00"},
        {"product_id": 11, "name": "Hat", "quantity": 100, "revenue": "1000.00"},
    ]
    assert result["truncated"] is False


async def test_sales_summary_with_no_orders(api, call):
    api.get("/orders").respond(200, json=[], headers={"X-WP-TotalPages": "0"})
    result = await call("sales_summary", after="2026-01-01", before="2026-01-02", status="completed")
    assert result == {
        "after": "2026-01-01T00:00:00",
        "before": "2026-01-02T23:59:59",
        "statuses": "completed",
        "order_count": 0,
        "orders_by_status": {},
        "currency": None,
        "revenue": "0",
        "refunded": "0",
        "average_order_value": "0",
        "top_products": [],
        "truncated": False,
    }


async def test_sales_summary_rejects_a_bad_date(call):
    with pytest.raises(ToolError, match="Bad date '2026-13-01'"):
        await call("sales_summary", after="2026-13-01", before="2026-10-31")


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("update_stock", {"product_id": 10, "quantity": 3}),
        ("update_order_status", {"order_id": 1, "status": "completed"}),
    ],
)
async def test_writes_are_disabled_by_default(api, call, name, arguments):
    put = api.put(path__regex=r".*").respond(200, json={})
    with pytest.raises(
        ToolError,
        match=f"Writes are disabled, so {name} did nothing. Start the server with WC_ALLOW_WRITES=true",
    ):
        await call(name, **arguments)
    assert not put.called


async def test_update_stock(api, call_writable):
    route = api.put("/products/10").respond(200, json=product(stock_quantity=3))
    result = await call_writable("update_stock", product_id=10, quantity=3)
    assert json.loads(route.calls.last.request.content) == {"manage_stock": True, "stock_quantity": 3}
    assert result == {
        "id": 10,
        "name": "Mug",
        "sku": "MUG-1",
        "stock_status": "instock",
        "manage_stock": True,
        "stock_quantity": 3,
    }


async def test_update_stock_for_a_variation(api, call_writable):
    route = api.put("/products/20/variations/21").respond(200, json=variation(21, "S", 8))
    result = await call_writable("update_stock", product_id=20, quantity=8, variation_id=21)
    assert json.loads(route.calls.last.request.content) == {"manage_stock": True, "stock_quantity": 8}
    assert result == {
        "id": 21,
        "name": "S",
        "sku": "TEE-S",
        "stock_status": "instock",
        "manage_stock": True,
        "stock_quantity": 8,
        "parent_id": 20,
    }


async def test_update_order_status(api, call_writable):
    route = api.put("/orders/1").respond(200, json=order(id=1, status="completed"))
    result = await call_writable("update_order_status", order_id=1, status="completed")
    assert json.loads(route.calls.last.request.content) == {"status": "completed"}
    assert result["id"] == 1
    assert result["status"] == "completed"


async def test_write_with_a_read_only_key(api, call_writable):
    api.put("/orders/1").respond(
        401,
        json=wc_error(
            "woocommerce_rest_cannot_edit", "Sorry, you are not allowed to edit this resource.", 401
        ),
    )
    with pytest.raises(ToolError, match="write permission"):
        await call_writable("update_order_status", order_id=1, status="completed")


async def test_store_resource(api, server):
    api.get("/system_status").respond(
        200,
        json={
            "environment": {
                "site_url": STORE_URL,
                "version": "9.4.1",
                "wp_version": "6.7",
                "default_timezone": "America/Los_Angeles",
                "php_version": "8.2.0",
            },
            "settings": {
                "currency": "USD",
                "currency_symbol": "$",
                "number_of_decimals": 2,
                "taxes_enabled": True,
            },
            "active_plugins": [{"plugin": "woocommerce/woocommerce.php"}],
        },
    )
    resources = {str(r.uri): r for r in await server.list_resources()}
    assert resources["woocommerce://store"].mime_type == "application/json"
    contents = list(await server.read_resource("woocommerce://store"))
    assert json.loads(contents[0].content) == {
        "url": STORE_URL,
        "writes_enabled": False,
        "site_url": STORE_URL,
        "woocommerce_version": "9.4.1",
        "wordpress_version": "6.7",
        "timezone": "America/Los_Angeles",
        "currency": "USD",
        "currency_symbol": "$",
        "decimals": 2,
    }


async def test_store_resource_when_system_status_is_off_limits(api, writable_server):
    api.get("/system_status").respond(
        403, json=wc_error("woocommerce_rest_cannot_view", "Sorry, you cannot view this resource.", 403)
    )
    contents = list(await writable_server.read_resource("woocommerce://store"))
    info = json.loads(contents[0].content)
    assert info["url"] == STORE_URL
    assert info["writes_enabled"] is True
    assert "HTTP 403" in info["note"]
