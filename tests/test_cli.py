"""Configuration and the command line, end to end with the stand-ins."""

from __future__ import annotations

import base64

import pytest
from conftest import FakeClaude, message, text, tool_use
from fake_wfm import KEY, SECRET, FakeWfm

from wfm_agent import cli
from wfm_agent.config import DEFAULT_MCP_URL, DEFAULT_MODEL, ConfigError, load_config, load_dotenv
from wfm_agent.mcp_client import WfmMcp


def test_config_reads_credentials_and_defaults() -> None:
    config = load_config({"WFM_API_KEY": KEY, "WFM_API_SECRET": SECRET})
    assert (config.mcp_url, config.model) == (DEFAULT_MCP_URL, DEFAULT_MODEL)
    assert SECRET not in repr(config)


@pytest.mark.parametrize(
    "env, missing",
    [
        ({}, "WFM_API_KEY and WFM_API_SECRET are"),
        ({"WFM_API_KEY": KEY}, "WFM_API_SECRET is"),
        (
            {"WFM_API_KEY": "your-api-key", "WFM_API_SECRET": "your-api-secret"},
            "WFM_API_KEY and WFM_API_SECRET",
        ),
    ],
)
def test_missing_credentials_say_which_and_where_to_get_them(env: dict[str, str], missing: str) -> None:
    with pytest.raises(ConfigError, match=missing) as raised:
        load_config(env)
    assert "worldfeedmonitor.com/register" in str(raised.value)


def test_dotenv_fills_gaps_without_overriding_or_loading_placeholders(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WFM_API_KEY=from-file\nWFM_API_SECRET=your-api-secret\n# comment\nWFM_MCP_URL='https://x/mcp'\n"
    )
    monkeypatch.setenv("WFM_API_KEY", "from-shell")
    monkeypatch.delenv("WFM_API_SECRET", raising=False)
    monkeypatch.delenv("WFM_MCP_URL", raising=False)

    load_dotenv(env_file)

    import os

    assert os.environ["WFM_API_KEY"] == "from-shell"
    assert "WFM_API_SECRET" not in os.environ
    assert os.environ["WFM_MCP_URL"] == "https://x/mcp"


def test_missing_configuration_exits_with_a_message(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    for name in ("WFM_API_KEY", "WFM_API_SECRET"):
        monkeypatch.delenv(name, raising=False)

    assert cli.main(["anything"]) == cli.EXIT_CONFIG
    assert "WFM_API_KEY and WFM_API_SECRET are not set" in capsys.readouterr().err


def run_cli(monkeypatch, tmp_path, fake: FakeWfm, claude: FakeClaude, *args: str) -> int:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WFM_API_KEY", KEY)
    monkeypatch.setenv("WFM_API_SECRET", SECRET)
    monkeypatch.setattr(cli.anthropic, "AsyncAnthropic", lambda: claude)
    real_init = WfmMcp.__init__

    def with_fake_transport(self, *a, **kw):
        real_init(self, *a, **kw)
        self.transport = fake.transport()

    monkeypatch.setattr(WfmMcp, "__init__", with_fake_transport)
    return cli.main(list(args))


def test_the_answer_lists_its_sources_with_their_original_links(monkeypatch, tmp_path, capsys) -> None:
    fake = FakeWfm()
    claude = FakeClaude(
        message("tool_use", tool_use("search_news", {"country": ["JP"]})),
        message("end_turn", text("A lab released a model [A2].")),
    )

    assert run_cli(monkeypatch, tmp_path, fake, claude, "--verbose", "AI", "in", "Japan") == 0

    out, err = capsys.readouterr()
    assert claude.requests[0]["messages"][0]["content"] == "AI in Japan"
    assert "[A2] Article 2" in out and fake.articles[1]["url"] in out
    assert fake.articles[0]["url"] not in out  # returned but not cited, so not listed
    assert '→ search_news {"country": ["JP"]}' in err

    # The credential appears nowhere, raw or encoded, even with --verbose.
    encoded = base64.b64encode(f"{KEY}:{SECRET}".encode()).decode()
    for secret in (SECRET, encoded):
        assert secret not in out and secret not in err


def test_wrong_credentials_exit_with_an_auth_error(monkeypatch, tmp_path, capsys) -> None:
    code = run_cli(monkeypatch, tmp_path, FakeWfm(status=401), FakeClaude(), "q")

    assert code == cli.EXIT_AUTH
    assert "did not accept WFM_API_KEY" in capsys.readouterr().err


def test_a_spent_allowance_exits_with_when_to_retry(monkeypatch, tmp_path, capsys) -> None:
    claude = FakeClaude(message("tool_use", tool_use("search_news", {})))
    code = run_cli(monkeypatch, tmp_path, FakeWfm(rate_limited=True), claude, "q")

    assert code == cli.EXIT_RATE_LIMIT
    assert "Try again in 24 seconds" in capsys.readouterr().err
