"""Core data models for the Openlibrary Knowledge Graph."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class CodeContext(BaseModel):
    """Where and how a concept was found in the codebase."""

    file_path: str
    function_name: str | None = None
    class_name: str | None = None
    line_number: int
    code_snippet: str = ""
    block_type: str = "module"


class ConceptOccurrence(BaseModel):
    """A single occurrence of a concept at one code location."""

    occurrence_id: str = Field(default_factory=lambda: str(uuid4()))
    raw_identifier: str
    split_name: str
    identifier_type: str
    context: CodeContext
    definition: str | None = None


class DefinitionCluster(BaseModel):
    """A group of occurrences sharing the same meaning (for polysemy detection)."""

    cluster_id: str = Field(default_factory=lambda: str(uuid4()))
    canonical_definition: str
    occurrence_ids: list[str] = Field(default_factory=list)
    distinctiveness: float = 0.0


class Concept(BaseModel):
    """A unique concept aggregated across all its occurrences."""

    concept_id: str = Field(default_factory=lambda: str(uuid4()))
    canonical_name: str
    split_terms: list[str] = Field(default_factory=list)
    all_raw_identifiers: list[str] = Field(default_factory=list)
    occurrences: list[ConceptOccurrence] = Field(default_factory=list)
    frequency: int = 0
    definition_clusters: list[DefinitionCluster] = Field(default_factory=list)


class Relationship(BaseModel):
    """A typed, weighted edge between two concepts."""

    relationship_id: str = Field(default_factory=lambda: str(uuid4()))
    source_concept_id: str
    target_concept_id: str
    relationship_type: str  # "synonym" | "polysemy" | "co-occurrence"
    weight: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraph(BaseModel):
    """The complete knowledge graph."""

    metadata: dict[str, Any] = Field(default_factory=lambda: {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "codebase": "openlibrary",
        "source_path": "",
    })
    concepts: list[Concept] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    concept_index: dict[str, int] = Field(default_factory=dict)
