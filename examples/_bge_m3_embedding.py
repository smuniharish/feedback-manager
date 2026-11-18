"""FlagEmbedding adapter implementing RetrievalLoom's native embedding contract."""

from __future__ import annotations

import os
from collections.abc import Sequence
from functools import partial
from pathlib import Path
from typing import Any

from retrievalloom.contracts.component import ComponentContext, ComponentInfo, make_info
from retrievalloom.contracts.models import EmbeddingModelABC
from retrievalloom.runtime.offloader import WorkloadKind


class LocalBgeM3Embedding(EmbeddingModelABC):
    """Run the local BGE-M3 dense encoder without blocking the event loop."""

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._model: Any | None = None
        self._context: ComponentContext | None = None
        self._info = make_info(
            component="embedding",
            implementation="feedback-manager.local-bge-m3",
            version="1",
            config={"model": "BAAI/bge-m3", "use_fp16": False},
            model_identity="BAAI/bge-m3",
            dimensions=1024,
        )

    @property
    def info(self) -> ComponentInfo:
        return self._info

    @property
    def dimensions(self) -> int:
        return 1024

    @property
    def max_batch_size(self) -> int:
        return 8

    async def initialize(self, context: ComponentContext) -> None:
        if self._model is not None:
            return
        if (
            not (self.model_path / "config.json").is_file()
            or not (self.model_path / "pytorch_model.bin").is_file()
        ):
            raise FileNotFoundError(f"Local BGE-M3 checkpoint is incomplete: {self.model_path}")

        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from FlagEmbedding import BGEM3FlagModel

        model_factory = partial(BGEM3FlagModel, str(self.model_path), use_fp16=False)
        self._context = context
        self._model = await context.offloader.run(WorkloadKind.EMBEDDING, model_factory)

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return ()
        context = self._require_context()
        model = self._require_model()
        encode = partial(
            model.encode,
            list(texts),
            batch_size=min(len(texts), self.max_batch_size),
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        output = await context.offloader.run(WorkloadKind.EMBEDDING, encode)
        return tuple(tuple(float(value) for value in row) for row in output["dense_vecs"])

    async def embed_query(self, text: str) -> Sequence[float]:
        (vector,) = await self.embed_documents((text,))
        return vector

    async def close(self) -> None:
        self._model = None
        self._context = None

    def _require_context(self) -> ComponentContext:
        if self._context is None:
            raise RuntimeError("BGE-M3 embedding component is not initialized")
        return self._context

    def _require_model(self) -> Any:
        if self._model is None:
            raise RuntimeError("BGE-M3 model is not loaded")
        return self._model
