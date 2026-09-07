from __future__ import annotations

import psutil

from sysdoc.core.models import Finding, Severity
from sysdoc.scanners.base import Scanner

CRITICAL_FREE_GB = 5.0
WARNING_USED_PERCENT = 85.0
CRITICAL_USED_PERCENT = 95.0
BYTES_PER_GB = 1024**3


class StorageScanner(Scanner):
    name = "storage"

    def run(self) -> list[Finding]:
        findings: list[Finding] = []
        for partition in psutil.disk_partitions(all=False):
            if "cdrom" in partition.opts or not partition.fstype:
                continue
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except OSError:
                continue
            findings.append(self._evaluate_drive(partition.mountpoint, usage))

        if not findings:
            findings.append(
                Finding(
                    title="No drives detected",
                    severity=Severity.WARNING,
                    detail="Could not enumerate any disk partitions.",
                )
            )
        return findings

    @staticmethod
    def _evaluate_drive(mountpoint: str, usage) -> Finding:
        free_gb = usage.free / BYTES_PER_GB
        total_gb = usage.total / BYTES_PER_GB
        summary = f"{free_gb:.1f}GB free of {total_gb:.1f}GB ({usage.percent:.0f}% used)."

        if usage.percent >= CRITICAL_USED_PERCENT or free_gb < CRITICAL_FREE_GB:
            return Finding(
                title=f"Drive {mountpoint} is nearly full",
                severity=Severity.CRITICAL,
                detail=summary,
                suggested_fix=(
                    "Free up space before installing or updating games — installers and "
                    "patchers commonly fail or corrupt downloads below a few GB free."
                ),
            )

        if usage.percent >= WARNING_USED_PERCENT:
            return Finding(
                title=f"Drive {mountpoint} is getting full",
                severity=Severity.WARNING,
                detail=summary,
                suggested_fix="Uninstall unused games or move large files to another drive.",
            )

        return Finding(
            title=f"Drive {mountpoint} has healthy free space",
            severity=Severity.OK,
            detail=summary,
        )
