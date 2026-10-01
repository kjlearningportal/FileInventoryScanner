"""Business-topic scoring from folder path, file name and content (rules/topics.yaml)."""
import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List

import yaml


@dataclass
class TopicRules:
    topics: Dict[str, List[str]]
    threshold: float
    path_weight: float
    content_weight: float
    max_hits: int
    regex: re.Pattern
    keyword_topics: Dict[str, List[str]]

    @classmethod
    def load(cls, path: str) -> "TopicRules":
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        topics = {name: [str(k).lower() for k in kws] for name, kws in raw["topics"].items()}
        kw_topics: Dict[str, List[str]] = {}
        for name, kws in topics.items():
            for k in kws:
                kw_topics.setdefault(k, []).append(name)
        # Longest first so "general ledger" wins over "ledger"; whole words only.
        alts = sorted(kw_topics, key=len, reverse=True)
        regex = re.compile(r"(?<![a-z0-9])(" + "|".join(
            re.escape(k).replace(r"\ ", r"[\s_\-]+") for k in alts) + r")(?![a-z0-9])", re.I)
        return cls(topics, float(raw.get("threshold", 5)), float(raw.get("path_weight", 3)),
                   float(raw.get("content_weight", 1)), int(raw.get("max_hits_per_keyword", 3)),
                   regex, kw_topics)

    def keyword(self, match: str) -> str:
        return re.sub(r"[\s_\-]+", " ", match.lower())


class TopicScorer:
    def __init__(self, rules: TopicRules, path: str):
        self.rules = rules
        tokens = re.sub(r"[\\/._]+", " ", path)
        self.path_kws = {rules.keyword(m.group(1)) for m in rules.regex.finditer(tokens)}
        self.hits: Counter = Counter()

    def add_text(self, text: str):
        cap = self.rules.max_hits
        for m in self.rules.regex.finditer(text):
            k = self.rules.keyword(m.group(1))
            if self.hits[k] < cap:
                self.hits[k] += 1

    def result(self):
        r = self.rules
        scores: Dict[str, float] = {}
        for k in self.path_kws:
            for t in r.keyword_topics.get(k, []):
                scores[t] = scores.get(t, 0) + r.path_weight
        for k, n in self.hits.items():
            for t in r.keyword_topics.get(k, []):
                scores[t] = scores.get(t, 0) + r.content_weight * n
        scores = {t: round(s, 1) for t, s in scores.items() if s > 0}
        chosen = sorted((t for t, s in scores.items() if s >= r.threshold), key=lambda t: -scores[t])
        return (chosen or ["unclassified"]), scores
