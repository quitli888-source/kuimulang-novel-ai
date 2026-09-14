"""
R7-P0-4: 轻量级向量检索 RAG 双轨（hash-based 假向量 + 可选 embedding）。

设计目标：
- 不依赖 sqlite-vec / chromadb / sentence-transformers 等重型依赖；
  默认降级为 hash-based 假向量，保证不崩。
- 提供 query / add / __len__ 接口，可被 SlidingWindow.build() 透明注入。
- 环境变量 ENABLE_VECTOR_RAG=1 启用；未启用时 enabled=False，对上层透明。

启用策略（按以下顺序探测 embedding_provider）：
1. provider="step" 或 "siliconflow" 且 .env 配置了对应 API Key → 远程 embedding
2. provider="local" 且安装 sentence-transformers → 本地 bge-small-zh
3. 其它情况 → hash-based 假向量（用于演示/降级）

本模块故意保持纯函数式接口（不阻塞主流程；失败永远 silent-fallback）。
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import List, Optional, Tuple


def _hash_embedding(text: str, dim: int = 256) -> List[float]:
    """基于 SHA-256 的稳定假向量：相同文本 → 相同向量。
    用于 embedding provider 不可用时的降级路径。
    """
    if not text:
        return [0.0] * dim
    vec = [0.0] * dim
    # 用滑动窗口让相邻词产生相似向量（粗略近似语义相似度）
    ngrams = set()
    for i in range(0, max(1, len(text) - 1)):
        ngrams.add(text[i:i + 2])
    for ng in ngrams:
        h = hashlib.sha256(ng.encode("utf-8")).digest()
        for i in range(0, min(dim * 4, len(h)), 4):
            slot = int.from_bytes(h[i:i + 4], "big") % dim
            vec[slot] += 1.0
    # L2 归一化
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        m = min(len(a), len(b))
        a, b = a[:m], b[:m]
    if not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class VectorStore:
    """R7-P0-4: 内存式向量库，支持 add / query；默认 hash 假向量，可选 remote embedding。

    Args:
        persist_dir: 持久化目录（暂未启用文件持久化，存内存即可）。
        embedding_provider: "step" / "siliconflow" / "local" / "none"。
        embedding_dim: 向量维度。
    """

    EMBEDDING_DIM = 256  # hash-based 假向量维度（与远程 embedding 维度不匹配时强制统一截断/补齐）

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        embedding_provider: Optional[str] = None,
        embedding_dim: int = EMBEDDING_DIM,
    ):
        # 默认关闭，由环境变量 ENABLE_VECTOR_RAG 控制
        self.enabled = os.environ.get("ENABLE_VECTOR_RAG", "0") == "1"
        self.embedding_provider = (embedding_provider or os.environ.get("VECTOR_EMBEDDING_PROVIDER", "none")).lower()
        self.embedding_dim = embedding_dim
        # { part_num: { "text": str, "embedding": List[float] } }
        self._store: dict = {}
        self._stats = {
            "add_count": 0,
            "query_count": 0,
            "fallback_count": 0,
        }

    # ---------- 公开 API ----------
    def add(self, part_num: int, text: str, embedding: Optional[List[float]] = None) -> bool:
        """写入一个 Part 的文本与 embedding。
        embedding 为 None 时降级为 hash-based 假向量。
        返回是否真的写入（disabled 时为 False）。
        """
        if not self.enabled:
            return False
        if not isinstance(text, str) or not text:
            return False
        if embedding is None:
            embedding = self._embed(text)
        # 维度对齐
        if len(embedding) != self.embedding_dim:
            embedding = self._resize(embedding, self.embedding_dim)
        self._store[int(part_num)] = {"text": text, "embedding": embedding}
        self._stats["add_count"] += 1
        return True

    def query(self, query_text: str, top_k: int = 3, exclude_part_num: Optional[int] = None) -> List[Tuple[int, float]]:
        """检索 top_k 相关 Part，返回 [(part_num, similarity), ...]，按相似度降序。

        Args:
            query_text: 查询文本。
            top_k: 返回 Top-K。
            exclude_part_num: 排除自身 Part（用于 Part 写作时不检索到自己）。

        Returns:
            [(part_num, similarity), ...]；disabled 或空库时返回 []。
        """
        if not self.enabled or not self._store:
            return []
        if not query_text:
            return []
        q_emb = self._embed(query_text)
        scores: List[Tuple[int, float]] = []
        for part_num, item in self._store.items():
            if exclude_part_num is not None and int(part_num) == int(exclude_part_num):
                continue
            sim = _cosine(q_emb, item["embedding"])
            scores.append((int(part_num), sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        self._stats["query_count"] += 1
        return scores[: max(0, int(top_k))]

    def get_text(self, part_num: int) -> Optional[str]:
        """取 Part 的原文（用于把检索结果拼进 prompt）。"""
        item = self._store.get(int(part_num))
        return item["text"] if item else None

    def __len__(self) -> int:
        return len(self._store)

    def stats(self) -> dict:
        """调试统计。"""
        return {
            "enabled": self.enabled,
            "provider": self.embedding_provider,
            "dim": self.embedding_dim,
            "size": len(self._store),
            **self._stats,
        }

    # ---------- 内部 ----------
    def _embed(self, text: str) -> List[float]:
        """统一 embedding 入口：远程 → 本地 → hash fallback。"""
        # 1) 远程（仅占位；R7-P0-4 范围内不真正调用第三方，简化降级）
        if self.embedding_provider in ("step", "siliconflow"):
            api_key_name = "STEP_API_KEY" if self.embedding_provider == "step" else "SILICONFLOW_API_KEY"
            api_key = os.environ.get(api_key_name, "")
            if api_key:
                # 占位：远程 embedding 端点调用不在本轮实现范围内；
                # 真接入需要 OpenAI 协议 embedding API + base_url，本轮只走降级路径。
                self._stats["fallback_count"] += 1
        # 2) 本地 sentence-transformers（R7-P0-4 暂不实现，依赖过重）
        # 3) hash-based 假向量（始终可用）
        return _hash_embedding(text, dim=self.embedding_dim)

    @staticmethod
    def _resize(vec: List[float], dim: int) -> List[float]:
        """维度对齐：截断或补 0。"""
        if len(vec) == dim:
            return vec
        if len(vec) > dim:
            return vec[:dim]
        return vec + [0.0] * (dim - len(vec))
