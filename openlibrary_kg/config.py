"""Configuration loader for the KG construction toolset.

Supports three layers (each overrides the previous):
1. Hardcoded defaults
2. YAML config file
3. Environment variables
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints

import yaml


# ---------------------------------------------------------------------------
# Nested config classes
# ---------------------------------------------------------------------------

@dataclass
class CodebaseConfig:
    root: str = ""
    include_patterns: list[str] = field(default_factory=lambda: ["**/*.py"])
    exclude_patterns: list[str] = field(default_factory=lambda: [
        "**/tests/**", "**/vendor/**", "**/mocks/**", "**/conftest.py"
    ])
    python_version: tuple[int, int] = (3, 12)


@dataclass
class ExtractionConfig:
    min_identifier_length: int = 2
    context_lines: int = 3
    stop_words: list[str] = field(default_factory=lambda: [
        "self", "cls", "args", "kwargs", "tmp", "temp", "item", "value",
        "result", "data", "info", "ctx", "req", "res", "obj", "ptr",
        "key", "val", "ret", "err", "msg", "idx", "num", "cnt",
        "src", "dst", "buf",
    ])
    keep_abbreviations: list[str] = field(default_factory=lambda: [
        "ISBN", "OLID", "OCLC", "LCCN", "DDC", "MARC", "IA", "OL",
        "URL", "API", "JSON", "HTML", "XML", "CSV", "SQL", "HTTP",
    ])


@dataclass
class LLMRateLimitConfig:
    requests_per_second: float = 10.0
    max_concurrent: int = 5


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str | None = None           # 直接填写 API Key（优先于环境变量）
    api_key_env: str = "OPENAI_API_KEY"  # 环境变量名（api_key 为空时使用）
    api_base: str | None = None
    temperature: float = 0.3
    max_tokens: int = 150
    max_retries: int = 3
    cache_dir: str = ".llm_cache"
    rate_limit: LLMRateLimitConfig = field(default_factory=LLMRateLimitConfig)


@dataclass
class EmbeddingConfig:
    provider: str = "sentence-transformers"
    model: str = "all-MiniLM-L6-v2"
    batch_size: int = 64


@dataclass
class SynonymConfig:
    similarity_threshold: float = 0.70
    naming_variant_threshold: float = 0.85
    llm_judge_low: float = 0.70
    llm_judge_high: float = 0.85
    top_k: int = 20
    llm_validation: bool = True
    llm_batch_size: int = 20


@dataclass
class PolysemyConfig:
    min_occurrences_for_polysemy: int = 5
    min_files_for_polysemy: int = 3
    embedding_distance_threshold: float = 0.35


@dataclass
class CooccurrenceConfig:
    min_count: int = 3
    normalization: str = "jaccard"
    threshold: float = 0.05
    use_subdomain_partition: bool = True
    cross_subdomain_factor: float = 0.3
    drop_module_level_context: bool = True


@dataclass
class RelationshipConfig:
    synonyms: SynonymConfig = field(default_factory=SynonymConfig)
    polysemy: PolysemyConfig = field(default_factory=PolysemyConfig)
    cooccurrence: CooccurrenceConfig = field(default_factory=CooccurrenceConfig)


@dataclass
class OutputConfig:
    directory: str = "./output"
    formats: list[str] = field(default_factory=lambda: ["json"])
    pretty_print: bool = True


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "kg_construction.log"


@dataclass
class Neo4jConfig:
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "password"
    database: str = "neo4j"
    clear_existing: bool = False
    batch_size: int = 500


@dataclass
class Config:
    codebase: CodebaseConfig = field(default_factory=CodebaseConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    relationships: RelationshipConfig = field(default_factory=RelationshipConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _deep_update(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _dict_to_dataclass(cls: type, data: dict) -> Any:
    """Recursively convert a dict to the given dataclass."""
    resolved_types = get_type_hints(cls)
    field_names = {f.name for f in __import__("dataclasses").fields(cls)}
    kwargs = {}
    for key, value in data.items():
        if key not in field_names:
            continue
        ftype = resolved_types.get(key)
        if ftype is None:
            kwargs[key] = value
        elif hasattr(ftype, "__dataclass_fields__"):
            kwargs[key] = _dict_to_dataclass(ftype, value)
        elif hasattr(ftype, "__origin__") and ftype.__origin__ is tuple:
            kwargs[key] = tuple(value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def load_config(config_path: str | None = None) -> Config:
    """Load configuration from YAML with env var overrides.

    config_path: Path to YAML file. If None, checks OPENLIBRARY_KG_CONFIG
                 env var, then defaults to config.yaml in cwd.
    """
    if config_path is None:
        config_path = os.environ.get("OPENLIBRARY_KG_CONFIG", "config.yaml")

    # Start with defaults
    config_dict: dict[str, Any] = {}

    # Layer 2: YAML file
    yaml_path = Path(config_path)
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}
        _deep_update(config_dict, yaml_data)

    # Layer 3: Environment variable overrides for key settings
    _apply_env_overrides(config_dict)

    return _dict_to_dataclass(Config, config_dict)


def _apply_env_overrides(config_dict: dict) -> None:
    """Apply environment variable overrides to config dict."""
    env_map = {
        "OPENLIBRARY_KG_CODEBASE_ROOT": ("codebase", "root"),
        "OPENLIBRARY_KG_LLM_PROVIDER": ("llm", "provider"),
        "OPENLIBRARY_KG_LLM_MODEL": ("llm", "model"),
        "OPENLIBRARY_KG_LLM_API_BASE": ("llm", "api_base"),
        "DEEPSEEK_API_KEY": ("llm", "api_key_env"),
        "OPENLIBRARY_KG_EMBEDDING_PROVIDER": ("embedding", "provider"),
        "OPENLIBRARY_KG_EMBEDDING_MODEL": ("embedding", "model"),
        "OPENLIBRARY_KG_OUTPUT_DIR": ("output", "directory"),
        "NEO4J_URI": ("neo4j", "uri"),
        "NEO4J_USER": ("neo4j", "user"),
        "NEO4J_PASSWORD": ("neo4j", "password"),
        "NEO4J_DATABASE": ("neo4j", "database"),
    }
    for env_var, (section, key) in env_map.items():
        value = os.environ.get(env_var)
        if value:
            config_dict.setdefault(section, {})[key] = value
