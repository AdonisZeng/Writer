"""RAG 知识库检索（P2，sqlite-vec / 方案 二·技术选型 + 5.3 with_rag_context）。

- 知识源：章节正文（定稿/落盘时索引）+ settings.md 世界观
- 嵌入：统一 OpenAI 协议 /v1/embeddings（LM Studio 加载 embedding 模型）
- 存储：state.db 内 rag_chunks(vec0) + rag_meta 双表；维度变更自动重建
- 检索：按本章细纲+作者指导为查询，top-k 片段注入 {{rag_block}}
sqlite-vec 未安装时整模块静默降级（rag_ready()=False）。
"""
import asyncio
import struct
from typing import Callable, Optional

from core import config, db


def rag_ready() -> bool:
    return db.vec_ready()


def _chunk_text(text: str, chunk_chars: int = 500) -> list[str]:
    """按段落边界聚合分块（绝不拦腰截断句子段落）。"""
    paragraphs = [p.strip() for p in (text or "").split("\n") if p.strip()]
    chunks, buf = [], ""
    for p in paragraphs:
        if len(p) > chunk_chars:      # 超长段落先收尾当前块，再整段成块
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p)
            continue
        if len(buf) + len(p) + 1 > chunk_chars and buf:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


def _to_vec_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


async def embed_texts(texts: list[str], model: str = "") -> list[list[float]]:
    """统一 OpenAI 协议 embeddings；空输入直接返回。"""
    if not texts:
        return []
    from core import ai_service
    model = model or config.get("rag_model", "")
    if not model:
        raise ValueError("未配置嵌入模型（rag_model）")
    t0 = __import__("time").time()
    resp = await ai_service._get_client().embeddings.create(
        model=model, input=texts)
    await ai_service._log_call(
        "rag", model, getattr(resp, "usage", None), None,
        __import__("time").time() - t0, True)
    data = sorted(resp.data, key=lambda d: d.index)
    return [d.embedding for d in data]


async def _insert_chunks(meta_rows: list[dict], vectors: list[list[float]],
                         dim: int) -> None:
    def w(conn):
        conn.execute("DELETE FROM rag_chunks")
        for m in meta_rows:
            conn.execute("DELETE FROM rag_meta WHERE source=? AND ref_id=?",
                         (m["source"], m["ref_id"]))
        for m, vec in zip(meta_rows, vectors):
            cur = conn.execute(
                "INSERT INTO rag_meta (source, ref_id, chunk_index, content, "
                "model) VALUES (?,?,?,?,?)",
                (m["source"], m["ref_id"], m["chunk_index"], m["content"],
                 m["model"]))
            conn.execute(
                "INSERT INTO rag_chunks (chunk_id, embedding) VALUES (?,?)",
                (cur.lastrowid, _to_vec_bytes(vec)))
    await db.write(w)


async def _ensure_vec_table(dim: int) -> bool:
    """经单写者队列确保 vec0 虚表存在（维度变更自动重建）。"""
    def w(conn):
        return db.ensure_vec_table(dim)
    return await db.write(w)


async def index_chapter(project: str, chapter_id: str, content: str,
                        model: str = "", embed_fn: Optional[Callable] = None
                        ) -> int:
    """索引/刷新单个章节（落盘与定稿后调用）。返回块数。"""
    if not rag_ready():
        return 0
    chunks = _chunk_text(content, config.get("rag_chunk_chars", 500))
    if not chunks:
        return 0
    model = model or config.get("rag_model", "")
    embed = embed_fn or embed_texts
    vectors = await embed(chunks, model)
    if not vectors:
        return 0
    if not await _ensure_vec_table(len(vectors[0])):
        return 0
    meta = [{"source": "chapter", "ref_id": chapter_id, "chunk_index": i,
             "content": c, "model": model}
            for i, c in enumerate(chunks)]
    await _insert_chunks(meta, vectors, len(vectors[0]))
    return len(meta)


async def index_settings(project: str, text: str, model: str = "",
                         embed_fn: Optional[Callable] = None) -> int:
    if not rag_ready():
        return 0
    chunks = _chunk_text(text, config.get("rag_chunk_chars", 500))
    if not chunks:
        return 0
    model = model or config.get("rag_model", "")
    embed = embed_fn or embed_texts
    vectors = await embed(chunks, model)
    if not vectors:
        return 0
    if not await _ensure_vec_table(len(vectors[0])):
        return 0
    meta = [{"source": "settings", "ref_id": "settings", "chunk_index": i,
             "content": c, "model": model} for i, c in enumerate(chunks)]
    await _insert_chunks(meta, vectors, len(vectors[0]))
    return len(meta)


async def retrieve(query: str, k: Optional[int] = None,
                   model: str = "",
                   embed_fn: Optional[Callable] = None) -> list[dict]:
    """top-k 相似片段（含来源章节号信息由调用方补齐）。"""
    if not rag_ready() or not query.strip():
        return []
    k = k or config.get("rag_top_k", 4)
    model = model or config.get("rag_model", "")
    embed = embed_fn or embed_texts
    qvec = (await embed([query], model))
    if not qvec:
        return []

    def q(conn):
        rows = conn.execute(
            "SELECT chunk_id, distance FROM rag_chunks "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (_to_vec_bytes(qvec[0]), k)).fetchall()
        if not rows:
            return []
        dist = {r[0]: r[1] for r in rows}
        ids = tuple(dist.keys())
        marks = ",".join("?" for _ in ids)
        metas = conn.execute(
            f"SELECT chunk_id, source, ref_id, chunk_index, content "
            f"FROM rag_meta WHERE chunk_id IN ({marks})", ids).fetchall()
        by_id = {m[0]: m for m in metas}
        return [{"source": by_id[i][1], "ref_id": by_id[i][2],
                 "chunk_index": by_id[i][3], "content": by_id[i][4],
                 "distance": dist[i]}
                for i in ids if i in by_id]
    return await db.read(q)


async def rebuild_index(project: str, model: str = "",
                        embed_fn: Optional[Callable] = None) -> dict:
    """全量重建（章节正文 + settings.md）。"""
    from core import file_manager
    if not rag_ready():
        return {"ok": False, "reason": "sqlite-vec 不可用"}
    model = model or config.get("rag_model", "")
    if not model:
        return {"ok": False, "reason": "未配置嵌入模型（rag_model）"}
    n = 0
    chapters = await db.list_chapters()
    for ch in chapters:
        path = file_manager.find_chapter_file(project, ch["number"],
                                              ch["title"])
        text = await asyncio.to_thread(file_manager.read_text, path)
        if text.strip():
            n += await index_chapter(project, ch["id"], text, model,
                                     embed_fn=embed_fn)
    n += await index_settings(
        project, await asyncio.to_thread(file_manager.read_settings_md,
                                         project), model, embed_fn=embed_fn)
    return {"ok": True, "chunks": n}


async def index_stats() -> dict:
    if not rag_ready():
        return {"ready": False}
    def q(conn):
        try:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(model),'') FROM rag_meta"
            ).fetchone()
            return {"ready": True, "chunks": row[0], "model": row[1]}
        except Exception:
            return {"ready": True, "chunks": 0, "model": ""}
    return await db.read(q)
