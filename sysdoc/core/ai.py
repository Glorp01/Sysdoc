from __future__ import annotations

import os

from google import genai
from google.genai import types

from sysdoc.core.config import load_api_key
from sysdoc.core.models import ScanResult

MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = (
    "You are a troubleshooting assistant built into a CLI tool called sysdoc. "
    "You help users fix problems with their PC, network, and games like Roblox and Steam. "
    "You may be given structured scan results alongside the user's question — use them "
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


def ask_ai(question: str, scan_results: list[ScanResult] | None = None) -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or load_api_key()
    if not api_key:
        raise RuntimeError("No API key found. Run 'sysdoc configure' first.")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=MODEL,
        contents=f"{_format_context(scan_results)}Question: {question}",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=1024,
        ),
    )
    return response.text or "(no response received)"
