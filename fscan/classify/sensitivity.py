"""File sensitivity level from distinct-value counts, using rules/sensitivity.yaml."""
from dataclasses import dataclass
from typing import Dict, List

import yaml


@dataclass
class Level:
    name: str
    any: set
    at_least: Dict[str, int]


class SensitivityRules:
    def __init__(self, levels: List[Level]):
        self.levels = levels

    @classmethod
    def load(cls, path: str) -> "SensitivityRules":
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return cls([Level(name, set(spec.get("any") or []), dict(spec.get("at_least") or {}))
                    for name, spec in raw["levels"].items()])

    def level(self, counts: Dict[str, int], unreadable: bool = False) -> str:
        for lv in self.levels:
            if any(counts.get(t, 0) > 0 for t in lv.any):
                return lv.name
            if any(counts.get(t, 0) >= n for t, n in lv.at_least.items()):
                return lv.name
        return "UNKNOWN" if unreadable else "NONE"
