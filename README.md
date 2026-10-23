# woocommerce-mcp

An MCP server that lets Claude look at a WooCommerce store: orders, products, customers, stock and a sales summary. It talks to the WooCommerce REST API v3 with a normal API key, and it is read-only unless you switch writes on.

Works with Claude Desktop, Claude Code and any other MCP client that can start a stdio server.

## What it looks like in use

Once the server is connected you can ask things like:

- "What sold best in the last 30 days?"
- "Which products are below 5 in stock?"
- "Show me the open orders from ann@example.com"
- "Set the stock of SKU MUG-1 to 40" (only with writes enabled)

Claude picks the tool, the server answers with trimmed JSON, Claude explains it.

## Install

Python 3.10 or newer.

```sh
pipx install git+https://github.com/inmanjeffrey89-cell/woocommerce-mcp
```

or

```sh
pip install git+https://github.com/inmanjeffrey89-cell/woocommerce-mcp
```

Either one puts a `woocommerce-mcp` command on your PATH. `python -m woocommerce_mcp` does the same thing.

## Get API keys from WooCommerce

In the WordPress admin go to WooCommerce > Settings > Advanced > REST API and click "Add key". Pick a user who can manage the store, set permissions to Read, and generate. Copy the consumer key and consumer secret right away; WooCommerce shows the secret once.

Read is enough for everything except `update_stock` and `update_order_status`. If you want those, create the key with Read/Write and set `WC_ALLOW_WRITES=true` (see below).

The store needs to be on HTTPS. WooCommerce only accepts key and secret authentication over HTTPS.

## Configure

The server reads four environment variables:

| Variable | Required | Meaning |
|---|---|---|
| `WC_URL` | yes | Store address, the WordPress root, for example `https://shop.example` |
| `WC_CONSUMER_KEY` | yes | Consumer key from the REST API page |
| `WC_CONSUMER_SECRET` | yes | Consumer secret from the REST API page |
| `WC_ALLOW_WRITES` | no | `true` to enable the two write tools. Default: off |

If one of the required variables is missing the server prints which one and exits instead of starting.

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings > Developer > Edit Config):

```json
{
  "mcpServers": {
    "woocommerce": {
      "command": "woocommerce-mcp",
      "env": {
        "WC_URL": "https://shop.example",
        "WC_CONSUMER_KEY": "ck_xxx",
        "WC_CONSUMER_SECRET": "cs_xxx"
      }
    }
  }
}
```

Claude Desktop does not read your shell PATH. If it reports that the server failed to start, replace `"woocommerce-mcp"` with the full path that `which woocommerce-mcp` prints.

### Claude Code

```sh
claude mcp add woocommerce \
  -e WC_URL=https://shop.example \
  -e WC_CONSUMER_KEY=ck_xxx \
  -e WC_CONSUMER_SECRET=cs_xxx \
  -- woocommerce-mcp
```

Add `-e WC_ALLOW_WRITES=true` to enable writes. Check with `claude mcp list`.

## Tools

Read tools (always on):

| Tool | What it does |
|---|---|
| `list_orders` | Recent orders, with filters for status, a date range and a customer id, email or name |
| `get_order` | One order with line items, addresses, totals, shipping, refunds and the customer note |
| `list_products` | Products with price and stock, filtered by search term, SKU, stock status or category |
| `get_product` | One product; variable products come with their variations |
| `list_customers` | Registered customers, searchable by name, email or username |
| `low_stock_report` | Everything at or under a stock threshold, plus items marked out of stock |
| `sales_summary` | Order count, revenue, refunds, average order value and top sellers for a date range |

Write tools (need `WC_ALLOW_WRITES=true`):

| Tool | What it does |
|---|---|
| `update_stock` | Set a product's or variation's stock quantity |
| `update_order_status` | Change an order's status |

With writes off, both tools answer with a short message saying writes are disabled and do nothing else.

There is also one resource, `woocommerce://store`, with the store URL, WooCommerce and WordPress versions, currency, timezone and whether writes are enabled.

Money values are passed through as strings exactly as WooCommerce reports them, with the currency code alongside. Dates go in and come out in the store's timezone.

## About WC_ALLOW_WRITES

`update_order_status` triggers the same side effects as changing the status in the admin: customer emails go out, stock can be released or restocked, and payment gateways may react. Setting an order to `refunded` only changes the label; no money moves. `update_stock` overwrites the quantity and turns on stock management for that product.

Leave writes off unless you need them, and when you turn them on, read what Claude proposes before you approve the tool call.

## Errors

Bad keys, missing orders, a store that is not reachable, rate limits and non-JSON responses all come back as a plain sentence that says what went wrong and, where it helps, what to check. Claude sees the sentence, not a stack trace.

## Limits worth knowing

- `sales_summary` and `low_stock_report` walk the whole result set 100 items at a time. They stop at 5,000 items and set `truncated: true` in the result when that happens.
- `low_stock_report` checks published products. Pass `include_variations: true` to also check each variable product's variations; that costs one request per variable product. With it off, `variable_products_unchecked` says how many variable products were skipped.
- Products and variations with stock management off have no quantity to compare, so they never count as low. They show up in `untracked_count`, with up to 10 of them in `untracked_examples`, so a "nothing is low" answer can say what it couldn't check.
- The `woocommerce://store` resource reads `system_status`, which WooCommerce only shows to users who can manage the store. With a more limited key you still get the URL and the writes flag.

## Development

```sh
git clone https://github.com/inmanjeffrey89-cell/woocommerce-mcp
cd woocommerce-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

The tests mock every HTTP call, so they run without a store. To try the read tools against a real store, export the three variables and run:

```sh
python scripts/smoke_live.py
```

It prints what each read tool returns and never writes anything.

## License

MIT. See [LICENSE](LICENSE).
