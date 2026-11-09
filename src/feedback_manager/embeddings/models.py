"""Embedding-space metadata independent of any vector database."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from feedback_manager.core._types import Identifier


class EmbeddingSpace(BaseModel):
    """The complete identity of compatible document and query embeddings.

    Instructions are part of the identity because asymmetric embedding models
    may use different prompts for documents and queries. Credentials do not
    belong in this model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Identifier
    model: Identifier
    revision: Identifier
    dimensions: int = Field(gt=0)
    normalized: bool = False
    document_instruction: str = ""
    query_instruction: str = ""

    @property
    def space_id(self) -> str:
        """Return a stable identifier derived from the non-secret metadata."""
        encoded = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["EmbeddingSpace"]
