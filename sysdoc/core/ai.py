"""Quick troubleshooting answers, without tools, from whichever AI provider is configured."""
from __future__ import annotations

from sysdoc.core import config
from sysdoc.core.models import ScanResult
from sysdoc.providers import PROVIDERS, Provider, ProviderError, create_provider, resolve_provider_name
from sysdoc.providers.base import Message

SYSTEM_PROMPT = (
    "You are a troubleshooting assistant built into a Windows app called Sysdoc. "
    "You help users fix problems with their PC, network, and games like Roblox and Steam. "
    "You may be given structured scan results alongside the user's question; use them "
    "as ground truth. Give clear, concise, actionable advice. Avoid unnecessary caveats."
)


def _format_context(scan_results: list[ScanResult] | None) -> str:
    if not scan_results:
        return ""

    lines = ["Here are the latest scan results:", ""]
    for result in scan_results:
        lines.append(f"[{result.scanner_name} scan]")
        for finding in result.findings:
            lines.append(f"- ({finding.severity.value}) {finding.title}: {finding.detail}")
            if finding.suggested_fix:
                lines.append(f"  Suggested fix: {finding.suggested_fix}")
    lines.append("")
    return "\n".join(lines)


def configured_provider(provider: str | None = None, model: str | None = None) -> Provider:
    """The provider from options, settings, or environment; raises ProviderError when none is usable."""
    settings = config.load_config()
    name = resolve_provider_name(provider) if provider else config.get_provider(settings)
    if name is None:
        raise ProviderError("No AI provider is set up yet. Run 'sysdoc setup' to connect Claude, GPT, or Gemini.")
    key = config.get_api_key(name, settings)
    if not key:
        info = PROVIDERS[name]
        raise ProviderError(f"No {info.label} API key found. Run 'sysdoc setup' or set {info.env_vars[0]}.")
    return create_provider(name, key, model or config.get_model(name, settings))


def ask_ai(question: str, scan_results: list[ScanResult] | None = None, *,
           provider: str | None = None, model: str | None = None) -> str:
    client = configured_provider(provider, model)
    prompt = f"{_format_context(scan_results)}Question: {question}"
    reply = client.complete(SYSTEM_PROMPT, [Message("user", text=prompt)], [])
    return reply.text.strip() or "(no response received)"
