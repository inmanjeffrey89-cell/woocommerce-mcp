"""Entry point for ``python -m woocommerce_mcp`` and the ``woocommerce-mcp`` script."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .client import WooCommerceClient
from .server import ConfigError, build_server, settings_from_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="woocommerce-mcp",
        description=(
            "MCP server for a WooCommerce store, speaking MCP over stdio. Configure it with the "
            "environment variables WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET and, to allow "
            "stock and order status changes, WC_ALLOW_WRITES=true."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)

    try:
        settings = settings_from_env()
    except ConfigError as exc:
        print(f"woocommerce-mcp: {exc}", file=sys.stderr)
        return 2

    client = WooCommerceClient(settings.url, settings.consumer_key, settings.consumer_secret)
    build_server(settings, client).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
