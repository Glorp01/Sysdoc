from typer.testing import CliRunner

from sysdoc import __version__, cli
from sysdoc.core import config
from sysdoc.core.models import Finding, Severity
from sysdoc.providers.base import Message, Provider

runner = CliRunner()


class ReplyingProvider(Provider):
    name = "anthropic"
    label = "Claude"

    def __init__(self, *replies):
        super().__init__("key", "claude-test")
        self.replies = list(replies)
        self.seen = []

    def complete(self, system, messages, tools):
        self.seen.append(messages[-1].text)
        return Message("assistant", text=self.replies.pop(0))

    def list_models(self):
        return ["claude-test"]


def test_version_output_stays_plain():
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"sysdoc {__version__}"


def test_scan_storage_shows_findings(monkeypatch):
    finding = Finding("Drive C: is getting full", Severity.WARNING, "40 GB free", "Remove old games")
    monkeypatch.setattr(cli.StorageScanner, "run", lambda self: [finding])
    result = runner.invoke(cli.app, ["scan", "storage"])
    assert result.exit_code == 0
    assert "Drive C: is getting full" in result.stdout
    assert "Remove old games" in result.stdout


def test_config_lists_providers_without_revealing_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-abcdefghijklmnopqrstuvwxyz")
    result = runner.invoke(cli.app, ["config"])
    assert result.exit_code == 0
    assert "GPT" in result.stdout
    assert "from OPENAI_API_KEY" in result.stdout
    assert "abcdefghijklmnop" not in result.stdout
    assert not config.config_file().exists()


def test_ask_without_a_provider_explains_setup(monkeypatch):
    monkeypatch.setattr(cli, "_all_scanners", lambda: [])
    result = runner.invoke(cli.app, ["ask", "why", "is", "my", "ping", "high"])
    assert result.exit_code == 1
    assert "sysdoc setup" in result.stdout


def test_fix_handles_the_problem_then_follow_ups(monkeypatch):
    provider = ReplyingProvider("Let's take a look.", "Glad that helped.")
    monkeypatch.setattr(cli, "_connect", lambda ui, provider_option, model_option: provider)
    result = runner.invoke(cli.app, ["fix", "my", "wifi", "drops"], input="/help\nthanks\n/exit\n")
    assert result.exit_code == 0, result.stdout
    assert provider.seen == ["my wifi drops", "thanks"]
    assert "Let's take a look." in result.stdout
    assert "Glad that helped." in result.stdout
    assert "/permissions" in result.stdout
    assert "Bye" in result.stdout


def test_model_command_switches_models_and_starts_fresh(monkeypatch):
    provider = ReplyingProvider("Hi.")
    monkeypatch.setattr(cli, "_connect", lambda ui, provider_option, model_option: provider)
    monkeypatch.setattr(cli, "create_provider", lambda name, key, model: ReplyingProvider("Using the new model."))
    result = runner.invoke(cli.app, [], input="hello\n/model claude-sonnet-5\n/exit\n")
    assert result.exit_code == 0, result.stdout
    assert "Now using Claude · claude-test" in result.stdout or "Now using Claude" in result.stdout
    assert config.get_model("anthropic") == "claude-sonnet-5"


def test_setup_checks_and_saves_the_provider_key_and_model(monkeypatch):
    checked = []

    def fake_create(name, key, model):
        checked.append((name, key, model))
        return ReplyingProvider()

    monkeypatch.setattr(cli, "create_provider", fake_create)
    result = runner.invoke(cli.app, ["setup"], input="2\nsk-test-key-1234567890\n2\n")
    assert result.exit_code == 0, result.stdout
    assert checked == [("openai", "sk-test-key-1234567890", "gpt-5.5")]
    assert config.get_provider() == "openai"
    assert config.get_api_key("openai") == "sk-test-key-1234567890"
    assert config.get_model("openai") == "gpt-5.4-mini"
