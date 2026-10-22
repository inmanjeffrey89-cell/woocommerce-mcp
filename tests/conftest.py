import json
from dataclasses import replace

import pytest
import respx

from tests.factories import API, STORE_URL
from woocommerce_mcp.client import WooCommerceClient
from woocommerce_mcp.server import Settings, build_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings():
    return Settings(url=STORE_URL, consumer_key="ck_example", consumer_secret="cs_example")


@pytest.fixture
async def client(settings):
    async with WooCommerceClient(settings.url, settings.consumer_key, settings.consumer_secret) as wc:
        yield wc


@pytest.fixture
def api():
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


@pytest.fixture
def server(settings, client):
    return build_server(settings, client)


@pytest.fixture
def writable_server(settings, client):
    return build_server(replace(settings, allow_writes=True), client)


@pytest.fixture
def call(server):
    return _caller(server)


@pytest.fixture
def call_writable(writable_server):
    return _caller(writable_server)


def _caller(server):
    """Call a tool the way an MCP client would and hand back the JSON it produced."""

    async def _call(name, **arguments):
        result = await server.call_tool(name, arguments)
        assert not result.is_error, result.content
        return json.loads(result.content[0].text)

    return _call
