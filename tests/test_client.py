import base64

import httpx
import pytest

from tests.factories import wc_error
from woocommerce_mcp.client import WooCommerceError

pytestmark = pytest.mark.anyio


async def test_sends_basic_auth_and_identifies_itself(api, client):
    route = api.get("/orders/1").respond(200, json={"id": 1})
    assert await client.get("orders/1") == {"id": 1}
    request = route.calls.last.request
    expected = base64.b64encode(b"ck_example:cs_example").decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
    assert request.headers["User-Agent"].startswith("woocommerce-mcp/")


@pytest.mark.parametrize("status", [401, 403])
async def test_bad_keys(api, client, status):
    api.get("/orders").respond(
        status, json=wc_error("woocommerce_rest_cannot_view", "Sorry, you cannot list resources.", status)
    )
    with pytest.raises(WooCommerceError) as info:
        await client.get("orders")
    text = str(info.value)
    assert f"HTTP {status}" in text
    assert "WC_CONSUMER_KEY" in text
    assert "cannot list resources" in text


async def test_not_found_names_the_path_and_the_store_message(api, client):
    api.get("/orders/999").respond(
        404, json=wc_error("woocommerce_rest_shop_order_invalid_id", "Invalid ID.", 404)
    )
    with pytest.raises(WooCommerceError, match=r"^Not found: /wp-json/wc/v3/orders/999 \(Invalid ID\.\)$"):
        await client.get("orders/999")


async def test_html_404_means_no_rest_api(api, client):
    api.get("/orders").respond(404, html="<html><body>Not Found</body></html>")
    with pytest.raises(WooCommerceError, match="No WooCommerce REST API at /wp-json/wc/v3/orders"):
        await client.get("orders")


async def test_rest_no_route_means_no_rest_api(api, client):
    api.get("/orders").respond(
        404, json=wc_error("rest_no_route", "No route was found matching the URL and request method.", 404)
    )
    with pytest.raises(WooCommerceError, match="No WooCommerce REST API"):
        await client.get("orders")


async def test_rate_limited(api, client):
    api.get("/orders").respond(429, text="Too Many Requests")
    with pytest.raises(WooCommerceError, match="rate limiting requests \\(HTTP 429\\)"):
        await client.get("orders")


async def test_server_error(api, client):
    api.get("/orders").respond(502, html="<h1>Bad Gateway</h1>")
    with pytest.raises(WooCommerceError, match=r"server error \(HTTP 502\) for /wp-json/wc/v3/orders"):
        await client.get("orders")


async def test_redirect_is_reported_with_the_target(api, client):
    api.get("/orders").respond(301, headers={"location": "https://www.shop.example/wp-json/wc/v3/orders"})
    with pytest.raises(WooCommerceError, match=r"redirected to https://www\.shop\.example"):
        await client.get("orders")


async def test_network_error(api, client):
    api.get("/orders").mock(side_effect=httpx.ConnectError("nodename nor servname provided"))
    with pytest.raises(WooCommerceError, match=r"Could not reach https://shop\.example: nodename"):
        await client.get("orders")


async def test_timeout(api, client):
    api.get("/orders").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(WooCommerceError, match=r"Timed out talking to https://shop\.example"):
        await client.get("orders")


async def test_non_json_success_body(api, client):
    api.get("/orders").respond(200, html="<html><body>Please log in</body></html>")
    with pytest.raises(WooCommerceError, match=r"non-JSON response \(HTTP 200\) from /wp-json/wc/v3/orders"):
        await client.get("orders")


async def test_get_page_reads_totals_from_headers(api, client):
    api.get("/orders").respond(200, json=[{"id": 1}], headers={"X-WP-Total": "57", "X-WP-TotalPages": "3"})
    page = await client.get_page("orders", {"per_page": 20})
    assert (page.items, page.total, page.total_pages) == ([{"id": 1}], 57, 3)


async def test_get_page_rejects_an_object(api, client):
    api.get("/orders").respond(200, json={"id": 1})
    with pytest.raises(WooCommerceError, match="Expected a list from orders, got dict"):
        await client.get_page("orders", {})


async def test_get_all_follows_the_total_pages_header(api, client):
    def respond(request):
        assert request.url.params["per_page"] == "100"
        assert request.url.params["status"] == "completed"
        page = int(request.url.params["page"])
        size = 100 if page == 1 else 7
        items = [{"id": (page - 1) * 100 + i} for i in range(size)]
        return httpx.Response(200, json=items, headers={"X-WP-TotalPages": "2"})

    route = api.get("/orders").mock(side_effect=respond)
    items, truncated = await client.get_all("orders", {"status": "completed"})
    assert len(items) == 107
    assert items[-1] == {"id": 106}
    assert truncated is False
    assert route.call_count == 2


async def test_get_all_without_headers_stops_on_a_short_page(api, client):
    route = api.get("/products").mock(
        side_effect=[
            httpx.Response(200, json=[{"id": i} for i in range(100)]),
            httpx.Response(200, json=[{"id": 100}]),
        ]
    )
    items, truncated = await client.get_all("products", {})
    assert len(items) == 101
    assert truncated is False
    assert route.call_count == 2


async def test_get_all_stops_at_max_items(api, client):
    full_page = httpx.Response(200, json=[{"id": i} for i in range(100)], headers={"X-WP-TotalPages": "99"})
    route = api.get("/orders").mock(return_value=full_page)
    items, truncated = await client.get_all("orders", {}, max_items=250)
    assert len(items) == 250
    assert truncated is True
    assert route.call_count == 3


async def test_put_sends_json(api, client):
    route = api.put("/products/10").respond(200, json={"id": 10, "stock_quantity": 3})
    assert await client.put("products/10", {"stock_quantity": 3}) == {"id": 10, "stock_quantity": 3}
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == b'{"stock_quantity":3}'
