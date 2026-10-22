"""Thin async client for the WooCommerce REST API v3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from . import __version__

PER_PAGE_MAX = 100
MAX_ITEMS_DEFAULT = 5000


class WooCommerceError(Exception):
    """A request failed. The message is written for a person, not a stack trace."""


@dataclass
class Page:
    items: list[dict[str, Any]]
    total: int | None
    total_pages: int | None


class WooCommerceClient:
    """Talks to one store with consumer key/secret over HTTP basic auth.

    WooCommerce only accepts key/secret basic auth over HTTPS. Plain HTTP works on
    local development stores that have been configured to allow it.
    """

    def __init__(self, url: str, consumer_key: str, consumer_secret: str, timeout: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=f"{self.url}/wp-json/wc/v3/",
            auth=(consumer_key, consumer_secret),
            timeout=timeout,
            headers={"User-Agent": f"woocommerce-mcp/{__version__}", "Accept": "application/json"},
        )

    async def __aenter__(self) -> WooCommerceClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return _parse_json(await self._request("GET", path, params=params))

    async def put(self, path: str, body: dict[str, Any]) -> Any:
        return _parse_json(await self._request("PUT", path, json=body))

    async def get_page(self, path: str, params: dict[str, Any]) -> Page:
        """One page of a list endpoint, with the totals WordPress sends in headers."""
        response = await self._request("GET", path, params=params)
        items = _parse_json(response)
        if not isinstance(items, list):
            raise WooCommerceError(f"Expected a list from {path}, got {type(items).__name__}")
        return Page(
            items=items,
            total=_int_header(response, "X-WP-Total"),
            total_pages=_int_header(response, "X-WP-TotalPages"),
        )

    async def get_all(
        self, path: str, params: dict[str, Any], max_items: int = MAX_ITEMS_DEFAULT
    ) -> tuple[list[dict[str, Any]], bool]:
        """Every item of a list endpoint, following pages until the store runs out.

        Returns the items and whether the walk stopped early at ``max_items``.
        Stops on the X-WP-TotalPages header when present and on a short page otherwise.
        """
        items: list[dict[str, Any]] = []
        page_number = 1
        while True:
            page = await self.get_page(path, {**params, "per_page": PER_PAGE_MAX, "page": page_number})
            items.extend(page.items)
            last_page = page.total_pages is not None and page_number >= page.total_pages
            if last_page or len(page.items) < PER_PAGE_MAX:
                return items, False
            if len(items) >= max_items:
                return items[:max_items], True
            page_number += 1

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise WooCommerceError(f"Timed out talking to {self.url}") from exc
        except httpx.RequestError as exc:
            raise WooCommerceError(f"Could not reach {self.url}: {exc}") from exc
        if response.is_success:
            return response
        raise WooCommerceError(_describe_failure(response))


def _int_header(response: httpx.Response, name: str) -> int | None:
    value = response.headers.get(name)
    return int(value) if value and value.isdigit() else None


def _parse_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise WooCommerceError(
            f"The store returned a non-JSON response (HTTP {response.status_code}) from "
            f"{response.request.url.path}. Check that WC_URL is the WordPress site root and "
            "nothing (a login page, a firewall, a maintenance plugin) is sitting in front of the REST API."
        ) from exc


def _describe_failure(response: httpx.Response) -> str:
    status = response.status_code
    path = response.request.url.path
    code, message = _error_body(response)
    detail = f" ({message})" if message else ""

    if status in (401, 403):
        return (
            f"WooCommerce rejected the API keys (HTTP {status}{detail}). Check WC_CONSUMER_KEY and "
            "WC_CONSUMER_SECRET, that the key belongs to a user who can manage the store, and that it "
            "has write permission if this was a write."
        )
    if status == 404 and (code == "rest_no_route" or message is None):
        return (
            f"No WooCommerce REST API at {path}. Check that WC_URL is the WordPress site root, "
            "WooCommerce is active, and permalinks are not set to Plain."
        )
    if status == 404:
        return f"Not found: {path}{detail}"
    if status == 429:
        return "The store is rate limiting requests (HTTP 429). Wait a moment and try again."
    if status in (301, 302, 307, 308):
        return f"The store redirected to {response.headers.get('location')}. Set WC_URL to that address."
    if status >= 500:
        return f"The store returned a server error (HTTP {status}{detail}) for {path}."
    return f"The store returned HTTP {status}{detail} for {path}."


def _error_body(response: httpx.Response) -> tuple[str | None, str | None]:
    """WooCommerce error code and message, when the body is the usual JSON error object."""
    try:
        body = response.json()
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    message = body.get("message")
    return body.get("code"), str(message) if message else None
