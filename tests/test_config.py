import pytest

from woocommerce_mcp.server import ConfigError, settings_from_env

COMPLETE = {
    "WC_URL": "https://shop.example",
    "WC_CONSUMER_KEY": "ck_example",
    "WC_CONSUMER_SECRET": "cs_example",
}


def test_missing_variables_are_all_named():
    with pytest.raises(
        ConfigError, match=r"missing environment variable\(s\): WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET"
    ):
        settings_from_env({})


def test_blank_value_counts_as_missing():
    with pytest.raises(ConfigError, match=r"missing environment variable\(s\): WC_CONSUMER_SECRET\."):
        settings_from_env({**COMPLETE, "WC_CONSUMER_SECRET": "   "})


def test_reads_values_and_defaults_to_read_only():
    settings = settings_from_env({**COMPLETE, "WC_URL": " https://shop.example/ "})
    assert settings.url == "https://shop.example"
    assert settings.consumer_key == "ck_example"
    assert settings.consumer_secret == "cs_example"
    assert settings.allow_writes is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("", False),
    ],
)
def test_allow_writes_parsing(value, expected):
    assert settings_from_env({**COMPLETE, "WC_ALLOW_WRITES": value}).allow_writes is expected


def test_url_needs_a_scheme():
    with pytest.raises(ConfigError, match="WC_URL must start with https://"):
        settings_from_env({**COMPLETE, "WC_URL": "shop.example"})
