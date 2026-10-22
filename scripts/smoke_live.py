"""Run the read-only tools against a real store and print what comes back.

Needs WC_URL, WC_CONSUMER_KEY and WC_CONSUMER_SECRET. Never writes, whatever WC_ALLOW_WRITES says.
"""

import asyncio
import json
import sys
from dataclasses import replace
from datetime import date, timedelta

from woocommerce_mcp.client import WooCommerceClient
from woocommerce_mcp.server import ConfigError, build_server, settings_from_env

PREVIEW_CHARS = 1500


async def run() -> int:
    try:
        settings = replace(settings_from_env(), allow_writes=False)
    except ConfigError as exc:
        print(f"Not running: {exc}", file=sys.stderr)
        return 2

    today = date.today()
    month_ago = today - timedelta(days=30)
    async with WooCommerceClient(settings.url, settings.consumer_key, settings.consumer_secret) as client:
        server = build_server(settings, client)

        contents = list(await server.read_resource("woocommerce://store"))
        print("== woocommerce://store")
        print(contents[0].content)

        orders = await show(server, "list_orders", {"limit": 3})
        if orders["orders"]:
            await show(server, "get_order", {"order_id": orders["orders"][0]["id"]})

        products = await show(server, "list_products", {"limit": 3})
        if products["products"]:
            await show(server, "get_product", {"product_id": products["products"][0]["id"]})

        await show(server, "list_customers", {"limit": 3})
        await show(server, "low_stock_report", {"threshold": 5})
        await show(server, "sales_summary", {"after": month_ago.isoformat(), "before": today.isoformat()})
    return 0


async def show(server, name: str, arguments: dict) -> dict:
    result = await server.call_tool(name, arguments)
    text = result.content[0].text
    print(f"\n== {name} {json.dumps(arguments)}")
    print(text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "\n... (trimmed)")
    return json.loads(text)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
