from __future__ import annotations

from abc import ABC, abstractmethod

from sysdoc.core.models import Finding


class Scanner (ABC):
    name: str

    @abstractmethod
    def run(self) -> list[Finding]:
        """execute the scan and return a list of findings."""
        raise NotImplementedError
