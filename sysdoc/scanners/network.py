from __future__ import annotations

import socket
import subprocess

from sysdoc.core.models import Finding, Severity
from sysdoc.scanners.base import Scanner

PING_TARGET = "1.1.1.1"
DNS_TARGETS = ["roblox.com", "steamcommunity.com", "google.com"]
REACHABILITY_TARGETS = {
    "Roblox": ("roblox.com", 443),
    "Steam": ("steamcommunity.com", 443),
}


class NetworkScanner(Scanner):
    name = "network"

    def run(self) -> list[Finding]:
        findings: list[Finding] = [self._check_ping()]
        findings.extend(self._check_dns())
        findings.extend(self._check_reachability())
        return findings

    def _check_ping(self) -> Finding:
        try:
            result = subprocess.run(
                ["ping", "-n", "4", PING_TARGET],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            return Finding(
                title="Ping test failed to run",
                severity=Severity.CRITICAL,
                detail=str(exc),
            )

        output = result.stdout

        if "Reply from" not in output:
            return Finding(
                title="No internet connectivity",
                severity=Severity.CRITICAL,
                detail=f"Could not reach {PING_TARGET}. Raw output:\n{output}",
                suggested_fix="Check your router/modem, Wi-Fi connection, or ISP outage status.",
            )

        loss_percent = self._extract_loss_percent(output)
        avg_ms = self._extract_average_ms(output)

        if loss_percent and loss_percent > 0:
            return Finding(
                title="Packet loss detected",
                severity=Severity.WARNING,
                detail=f"{loss_percent:.0f}% packet loss to {PING_TARGET}.",
                suggested_fix="Packet loss causes rubber-banding/disconnects in games. Try a wired connection or restart your router.",
            )

        if avg_ms is None:
            return Finding(
                title="Ping succeeded but couldn't parse latency",
                severity=Severity.INFO,
                detail=output,
            )

        if avg_ms > 150:
            return Finding(
                title="High network latency",
                severity=Severity.WARNING,
                detail=f"Average ping to {PING_TARGET} is {avg_ms:.0f}ms.",
                suggested_fix="High ping can cause lag in online games. Try a wired connection or restart your router.",
            )

        return Finding(
            title="Network latency normal",
            severity=Severity.OK,
            detail=f"Average ping to {PING_TARGET} is {avg_ms:.0f}ms.",
        )

    @staticmethod
    def _extract_average_ms(output: str) -> float | None:
        for line in output.splitlines():
            if "Average" in line:
                try:
                    part = line.split("Average = ")[1]
                    digits = "".join(ch for ch in part if ch.isdigit())
                    return float(digits)
                except (IndexError, ValueError):
                    return None
        return None

    @staticmethod
    def _extract_loss_percent(output: str) -> float | None:
        for line in output.splitlines():
            if "% loss" in line:
                try:
                    part = line.split("(")[1].split("%")[0]
                    return float(part)
                except (IndexError, ValueError):
                    return None
        return None

    def _check_dns(self) -> list[Finding]:
        failures: list[Finding] = []
        for host in DNS_TARGETS:
            try:
                socket.gethostbyname(host)
            except socket.gaierror:
                failures.append(
                    Finding(
                        title=f"DNS resolution failed for {host}",
                        severity=Severity.CRITICAL,
                        detail=f"Could not resolve {host} to an IP address.",
                        suggested_fix="Try switching your DNS server to 1.1.1.1 or 8.8.8.8.",
                    )
                )
        if failures:
            return failures
        return [
            Finding(
                title="DNS resolution working",
                severity=Severity.OK,
                detail=f"Successfully resolved: {', '.join(DNS_TARGETS)}",
            )
        ]

    def _check_reachability(self) -> list[Finding]:
        findings: list[Finding] = []
        for service, (host, port) in REACHABILITY_TARGETS.items():
            try:
                with socket.create_connection((host, port), timeout=3):
                    pass
            except OSError:
                findings.append(
                    Finding(
                        title=f"Cannot reach {service}",
                        severity=Severity.CRITICAL,
                        detail=f"Failed to open a connection to {host}:{port}.",
                        suggested_fix=f"{service} servers may be down, or a firewall/VPN is blocking the connection.",
                    )
                )
            else:
                findings.append(
                    Finding(
                        title=f"{service} reachable",
                        severity=Severity.OK,
                        detail=f"Successfully connected to {host}:{port}.",
                    )
                )
        return findings
