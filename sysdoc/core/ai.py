from __future__ import annotations

import anthropic

from sysdoc.core.models import ScanResult

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = (
    "You are a troubleshooting assistant built into a CLI tool called sysdoc. "
    "You help users fix problems with their PC, network, and games like Roblox and Steam. "
    "You may be given structured scan results alongside the user's question — use them "
    "as ground truth. Give clear, concise, actionable advice. Avoid unnecessary caveats."
)


def ask_ai(question: str, scan_results: list[ScanResult] | None = None) -> str:
    client = anthropic.Anthropic()

    context = ""
    if scan_results:
        context = "Here are the latest scan results:\n\n"
        for result in scan_results:
            context += f"[{result.scanner_name} scan]\n"
            for finding in result.findings:
                context += f"- ({finding.severity.value}) {finding.title}: {finding.detail}\n"
                if finding.suggested_fix:
                    context += f"  Suggested fix: {finding.suggested_fix}\n"
        context += "\n"

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"{context}Question: {question}"}],
    )
    text_blocks = [block.text for block in message.content if block.type == "text"]
    return "\n".join(text_blocks)
