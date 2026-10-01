"""Load and validate config.yaml."""
import os
from dataclasses import dataclass, field
from typing import List, Optional

import yaml


class ConfigError(ValueError):
    pass


@dataclass
class SourceConfig:
    name: str
    root: str


PROJECT_RULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules")


@dataclass
class ContentConfig:
    enabled: bool = True
    workers: int = 4                  # parallel processes reading file content
    ocr: bool = True
    tesseract_cmd: Optional[str] = None   # None = find it automatically
    ocr_lang: str = "eng"
    ocr_timeout: int = 180            # seconds per page/image
    max_depth: int = 3                # attachments / archives inside archives
    sample_size: int = 200            # files in the content speed test
    rules_dir: str = PROJECT_RULES    # sensitivity.yaml + topics.yaml


@dataclass
class Config:
    sources: List[SourceConfig]
    output_dir: str
    content: ContentConfig = field(default_factory=ContentConfig)
    exclude_dirs: List[str] = field(default_factory=list)
    exclude_files: List[str] = field(default_factory=list)
    workers: int = 8
    list_workers: int = 8
    max_read_mb_per_sec: float = 50
    chunk_mb: float = 1
    max_attempts: int = 3
    batch_size: int = 500
    batch_seconds: float = 5
    scan_hours: Optional[str] = None
    sample_size: int = 500

    @property
    def chunk_bytes(self) -> int:
        return max(1, int(self.chunk_mb * 1024 * 1024))

    @property
    def state_db(self) -> str:
        return os.path.join(self.output_dir, "state.sqlite")

    @property
    def progress_json(self) -> str:
        return os.path.join(self.output_dir, "progress.json")

    @property
    def log_file(self) -> str:
        return os.path.join(self.output_dir, "logs", "run.log")


def load_config(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    base = os.path.dirname(os.path.abspath(path))
    return build_config(raw, base)


def build_config(raw: dict, base: str = ".") -> Config:
    srcs = raw.pop("sources", None)
    if not srcs:
        raise ConfigError("config needs at least one entry under 'sources'")
    sources = [SourceConfig(name=s["name"], root=s["root"]) for s in srcs]
    if len({s.name for s in sources}) != len(sources):
        raise ConfigError("source names must be unique")

    out = raw.pop("output_dir", "output")
    out = out if os.path.isabs(out) else os.path.join(base, out)
    exclude = raw.pop("exclude", {}) or {}
    content_raw = dict(raw.pop("content", {}) or {})
    known = set(Config.__dataclass_fields__) - {"sources", "output_dir", "exclude_dirs", "exclude_files", "content"}
    unknown = set(raw) - known
    unknown |= {f"content.{k}" for k in set(content_raw) - set(ContentConfig.__dataclass_fields__)}
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown))}")
    rules = content_raw.get("rules_dir")
    if rules and not os.path.isabs(rules):
        content_raw["rules_dir"] = os.path.abspath(os.path.join(base, rules))

    cfg = Config(sources=sources, output_dir=os.path.abspath(out),
                 exclude_dirs=exclude.get("dirs", []) or [],
                 exclude_files=exclude.get("files", []) or [],
                 content=ContentConfig(**content_raw), **raw)
    for name in ("sensitivity.yaml", "topics.yaml"):
        if cfg.content.enabled and not os.path.isfile(os.path.join(cfg.content.rules_dir, name)):
            raise ConfigError(f"rules file missing: {os.path.join(cfg.content.rules_dir, name)}")

    # Guardrail 8: results never go onto the share being scanned.
    for s in sources:
        if _inside(cfg.output_dir, s.root):
            raise ConfigError(f"output_dir {cfg.output_dir} is inside source '{s.name}' ({s.root}); "
                              "results must be stored outside the scanned area")
    return cfg


def _inside(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.abspath(child))
    p = os.path.normcase(os.path.abspath(parent))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives
        return False
