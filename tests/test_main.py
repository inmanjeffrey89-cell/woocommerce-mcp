import subprocess
import sys

import pytest

from woocommerce_mcp import __version__
from woocommerce_mcp.__main__ import main

ENV_VARS = ("WC_URL", "WC_CONSUMER_KEY", "WC_CONSUMER_SECRET", "WC_ALLOW_WRITES")


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_help_names_the_env_vars(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "WC_URL" in out
    assert "WC_ALLOW_WRITES" in out


def test_version(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.strip() == f"woocommerce-mcp {__version__}"


def test_missing_config_exits_before_touching_stdio(clean_env, capsys):
    assert main([]) == 2
    err = capsys.readouterr().err
    assert err.startswith(
        "woocommerce-mcp: missing environment variable(s): WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET"
    )


def test_module_runs_as_a_script():
    completed = subprocess.run(
        [sys.executable, "-m", "woocommerce_mcp", "--help"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert "usage: woocommerce-mcp" in completed.stdout
