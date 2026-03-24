"""语义记忆 — 向量化对话索引 (ChromaDB)"""

import logging
import uuid

logger = logging.getLogger("companion_bot.semantic_memory")

TOP_K = 5


class SemanticMemory:
    """语义记忆管理器 — ChromaDB 向量存储与 RAG 检索"""

    def __init__(self, persist_dir: str):
        self.persist_dir = persist_dir
        self.client = None
        self.collection = None

    async def initialize(self):
        """启动时初始化 ChromaDB 连接"""
        self._ensure_collection()

    def _ensure_collection(self):
        """确保 ChromaDB 集合可用"""
        if self.collection is not None:
            return

        try:
            import chromadb
            self.client = chromadb.PersistentClient(path=self.persist_dir)
            self.collection = self.client.get_or_create_collection(
                name="conversation_memory",
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("ChromaDB 语义记忆初始化完成")
        except Exception as e:
            logger.warning(f"ChromaDB 初始化失败: {e}")

    async def add(
        self, person_id: str, text: str, metadata: dict | None = None
    ):
        """
        存储对话摘要到向量数据库。
        ChromaDB 内置 embedding 函数，自动向量化。
        """
        self._ensure_collection()
        if self.collection is None:
            return

        doc_id = str(uuid.uuid4())
        meta = {"person_id": person_id}
        if metadata:
            meta.update(metadata)

        try:
            self.collection.add(
                documents=[text],
                metadatas=[meta],
                ids=[doc_id],
            )
            logger.debug(f"语义记忆存入: person={person_id}, text={text[:50]}")
        except Exception as e:
            logger.error(f"语义记忆存储失败: {e}")

    async def search(
        self, query: str, person_id: str | None = None, top_k: int = TOP_K
    ) -> list[dict]:
        """
        检索与查询最相关的历史记忆。
        输出: [{"text": str, "person_id": str, "score": float}, ...]
        """
        self._ensure_collection()
        if self.collection is None:
            return []

        where_filter = None
        if person_id:
            where_filter = {"person_id": person_id}

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_filter,
            )

            memories = []
            if results and results["documents"]:
                docs = results["documents"][0]
                metas = results["metadatas"][0] if results["metadatas"] else [{}] * len(docs)
                distances = results["distances"][0] if results["distances"] else [0.0] * len(docs)

                for doc, meta, dist in zip(docs, metas, distances):
                    memories.append({
                        "text": doc,
                        "person_id": meta.get("person_id", "unknown"),
                        "score": 1.0 - dist,  # ChromaDB 返回距离，转换为相似度
                    })

            return memories
        except Exception as e:
            logger.error(f"语义记忆检索失败: {e}")
            return []
