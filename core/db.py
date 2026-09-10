"""SQLite 连接管理（state.db）：WAL + 单写者队列 + 线程安全读。

设计要点（对应方案 5.1 / 风险表）：
- 写操作统一投递 asyncio.Queue，由单写者协程串行消费，杜绝多线程共享连接的锁竞争；
- 读操作经 asyncio.to_thread 直连只读连接（WAL 并发读安全），连接内以 RLock 串行化；
- 全部业务表建表语句与方案第四章一致，首次打开即建全（含 P1/P2 才使用的表）。
"""
import asyncio
import sqlite3
import threading
from typing import Any, Callable, Optional

_SCHEMA = """
-- ============ 1. 项目主台账 ============
CREATE TABLE IF NOT EXISTS project_core (
    id              TEXT PRIMARY KEY DEFAULT 'main',
    title           TEXT DEFAULT '',
    genre           TEXT DEFAULT '',
    total_chapters  INTEGER DEFAULT 100,
    words_per_chapter INTEGER DEFAULT 3000,
    writing_style   TEXT DEFAULT '',
    global_guidance TEXT DEFAULT '',
    premise         TEXT DEFAULT '',
    theme           TEXT DEFAULT '',
    synopsis        TEXT DEFAULT '',
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- ============ 2. 章节 ============
CREATE TABLE IF NOT EXISTS chapters (
    id          TEXT PRIMARY KEY,
    order_index REAL NOT NULL,
    number      INTEGER DEFAULT 0,
    title       TEXT DEFAULT '',
    role        TEXT DEFAULT '',
    purpose     TEXT DEFAULT '',
    key_events  TEXT DEFAULT '',
    characters  TEXT DEFAULT '[]',
    pov         TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    beats       TEXT DEFAULT '[]',
    status      TEXT DEFAULT 'outlined',
    word_count  INTEGER DEFAULT 0
);

-- ============ 3. 角色卡（含跨章动态状态）============
CREATE TABLE IF NOT EXISTS characters (
    name        TEXT PRIMARY KEY,
    aliases     TEXT DEFAULT '[]',
    role        TEXT DEFAULT 'supporting',
    personality TEXT DEFAULT '',
    background  TEXT DEFAULT '',
    abilities   TEXT DEFAULT '',
    cs_location TEXT DEFAULT '',
    cs_level    TEXT DEFAULT '',
    cs_physical TEXT DEFAULT '',
    cs_mental   TEXT DEFAULT '',
    cs_items    TEXT DEFAULT '',
    cs_knowledge TEXT DEFAULT '[]',
    cs_chapter_id TEXT DEFAULT '',
    created_at_chapter_id TEXT DEFAULT ''
);

-- ============ 4. 草稿版本 ============
CREATE TABLE IF NOT EXISTS drafts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id   TEXT NOT NULL,
    version      INTEGER NOT NULL,
    status       TEXT DEFAULT 'draft',
    source       TEXT DEFAULT 'write',
    file_path    TEXT NOT NULL,
    word_count   INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(chapter_id, version)
);

-- ============ 5. Canon：时间线事件 ============
CREATE TABLE IF NOT EXISTS canon_timeline (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    location    TEXT DEFAULT '',
    summary     TEXT DEFAULT '',
    impact      TEXT DEFAULT ''
);

-- ============ 6. Canon：角色状态历史 ============
CREATE TABLE IF NOT EXISTS canon_char_state (
    character   TEXT NOT NULL,
    chapter_id  TEXT NOT NULL,
    location    TEXT DEFAULT '',
    level       TEXT DEFAULT '',
    state       TEXT DEFAULT '',
    items       TEXT DEFAULT '',
    PRIMARY KEY (character, chapter_id)
);

-- ============ 7. Canon：未结剧情线（伏笔）============
CREATE TABLE IF NOT EXISTS canon_plot_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    status      TEXT DEFAULT 'active',
    started_at  TEXT DEFAULT '',
    last_advanced TEXT DEFAULT '',
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS canon_plot_line_snapshots (
    plot_line_id INTEGER NOT NULL,
    chapter_id   TEXT NOT NULL,
    status       TEXT DEFAULT 'active',
    last_advanced TEXT DEFAULT '',
    PRIMARY KEY (plot_line_id, chapter_id)
);

-- ============ 8. Canon：客观事实 ============
CREATE TABLE IF NOT EXISTS canon_facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    statement   TEXT NOT NULL,
    since_chapter_id TEXT DEFAULT ''
);

-- ============ 9. Canon：章节摘要 ============
CREATE TABLE IF NOT EXISTS canon_summaries (
    chapter_id  TEXT PRIMARY KEY,
    summary     TEXT DEFAULT ''
);

-- ============ 10. LLM 调用统计 ============
CREATE TABLE IF NOT EXISTS llm_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT DEFAULT '',
    model       TEXT NOT NULL,
    purpose     TEXT DEFAULT '',
    prompt_tokens     INTEGER DEFAULT 0,
    cached_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    ttft_ms     INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    cost        REAL DEFAULT 0,
    success     INTEGER DEFAULT 1,
    error_msg   TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now'))
);

-- ============ 11. 定稿后处理步骤状态 ============
CREATE TABLE IF NOT EXISTS post_process_steps (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id  TEXT NOT NULL,
    step_key  TEXT NOT NULL,
    ok        INTEGER DEFAULT 0,
    critical  INTEGER DEFAULT 0,
    error_msg TEXT DEFAULT '',
    attempts  INTEGER DEFAULT 0,
    UNIQUE(chapter_id, step_key)
);

CREATE INDEX IF NOT EXISTS idx_chapters_order ON chapters(order_index);
CREATE INDEX IF NOT EXISTS idx_drafts_chapter ON drafts(chapter_id);
CREATE INDEX IF NOT EXISTS idx_timeline_chapter ON canon_timeline(chapter_id);
CREATE INDEX IF NOT EXISTS idx_llm_created ON llm_calls(created_at);

-- ============ 12. 诊断忽略记录（补充表：作者点「忽略」的语义诊断指纹，跨会话持久）============
CREATE TABLE IF NOT EXISTS diagnostics_dismissed (
    chapter_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (chapter_id, fingerprint)
);

-- ============ 13. 设计域：多轮对话历史（每项目独立库，天然按项目隔离）============
CREATE TABLE IF NOT EXISTS design_chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    role        TEXT NOT NULL,          -- user / assistant
    content     TEXT DEFAULT '',
    mode        TEXT DEFAULT 'free',    -- free=自由对话 / guide=分步引导
    step        TEXT DEFAULT '',        -- 引导步骤 key（自由对话为空）
    created_at  TEXT DEFAULT (datetime('now'))
);

-- ============ 14. 设计域：结构化世界观分节（DB 为编辑源，settings.md 为投影）============
CREATE TABLE IF NOT EXISTS world_sections (
    section_key TEXT PRIMARY KEY,
    label       TEXT DEFAULT '',
    content     TEXT DEFAULT '',
    order_index REAL NOT NULL DEFAULT 0
);

-- ============ 15. 设计域：起步引导进度（单一主行，可续做）============
CREATE TABLE IF NOT EXISTS design_guide (
    id           TEXT PRIMARY KEY DEFAULT 'main',
    idea         TEXT DEFAULT '',
    current_step TEXT DEFAULT 'core',
    steps_done   TEXT DEFAULT '[]',
    updated_at   TEXT DEFAULT (datetime('now'))
);
"""

_conn_w: Optional[sqlite3.Connection] = None   # 写连接：仅单写者协程使用（事件循环线程）
_conn_r: Optional[sqlite3.Connection] = None   # 读连接：to_thread 工作线程使用
_lock_r = threading.RLock()
_queue: Optional[asyncio.Queue] = None
_writer_task: Optional[asyncio.Task] = None
_db_path: str = ""
_vec_loaded: bool = False
_vec_dim: int = 0


def _load_vec_extension(conn: sqlite3.Connection) -> bool:
    """加载 sqlite-vec 扩展（P2 RAG）；未安装时静默降级。"""
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as e:
        print(f"sqlite-vec 加载失败（RAG 功能不可用）：{e}")
        return False


def ensure_vec_table(dim: int) -> bool:
    """确保 rag_chunks 虚表存在；维度变更时重建（需重新索引）。同步方法，
    在 rag 索引流程内经 db.write 外显式调用时机由 rag 模块管理。"""
    global _vec_dim
    assert _conn_w is not None
    if not _vec_loaded:
        return False
    cur = _conn_w.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='rag_chunks'")
    exists = cur.fetchone() is not None
    if exists and _vec_dim and _vec_dim != dim:
        _conn_w.execute("DROP TABLE rag_chunks")
        exists = False
    if not exists:
        _conn_w.execute(
            f"CREATE VIRTUAL TABLE rag_chunks USING vec0("
            f"chunk_id INTEGER PRIMARY KEY, embedding float[{dim}])")
        _conn_w.execute(
            "CREATE TABLE IF NOT EXISTS rag_meta ("
            "chunk_id INTEGER PRIMARY KEY, source TEXT DEFAULT '', "
            "ref_id TEXT DEFAULT '', chunk_index INTEGER DEFAULT 0, "
            "content TEXT DEFAULT '', model TEXT DEFAULT '')")
        _vec_dim = dim
    _conn_w.commit()
    return True


def vec_ready() -> bool:
    return _vec_loaded

STOP = object()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：清理已废弃列（失败静默，绝不阻断启动）。

    `project_core.worldbuilding` 已废弃（世界观改由结构化分节 + settings.md
    投影承载，生成时读文件），历史库 best-effort 删除；旧版 SQLite 不支持
    DROP COLUMN 时留列无害——代码已不再读写它。
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(project_core)")}
        if "worldbuilding" in cols:
            conn.execute("ALTER TABLE project_core DROP COLUMN worldbuilding")
        if "theme" not in cols:
            conn.execute(
                "ALTER TABLE project_core ADD COLUMN theme TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass


def _row_to_dict(cur: sqlite3.Cursor, row: tuple) -> dict:
    return {d[0]: row[i] for i, d in enumerate(cur.description)}


async def open(db_path: str) -> None:
    """打开（或切换）项目数据库并建表；启动单写者协程。"""
    global _conn_w, _conn_r, _queue, _writer_task, _db_path
    await close()
    import os
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _db_path = db_path
    _conn_w = _connect(db_path)
    _conn_r = _connect(db_path)
    global _vec_loaded
    _vec_loaded = _load_vec_extension(_conn_w) and _load_vec_extension(_conn_r)
    _conn_w.executescript(_SCHEMA)
    _migrate(_conn_w)
    _conn_w.commit()
    _queue = asyncio.Queue()
    _writer_task = asyncio.create_task(_writer_worker(), name="db-writer")


async def close() -> None:
    global _conn_w, _conn_r, _queue, _writer_task, _vec_loaded, _vec_dim
    if _writer_task is not None:
        _writer_task.cancel()
        try:
            await _writer_task
        except (asyncio.CancelledError, Exception):
            pass
        _writer_task = None
    _queue = None
    if _conn_w is not None:
        _conn_w.close()
        _conn_w = None
    if _conn_r is not None:
        _conn_r.close()
        _conn_r = None
    _vec_loaded = False
    _vec_dim = 0


async def _writer_worker() -> None:
    """单写者协程：串行消费写队列，杜绝并发写竞争。"""
    assert _queue is not None
    while True:
        item = await _queue.get()
        if item is STOP:
            break
        fn, args, kwargs, fut = item
        try:
            assert _conn_w is not None
            result = fn(_conn_w, *args, **kwargs)
            _conn_w.commit()
            if not fut.cancelled():
                fut.set_result(result)
        except Exception as e:
            try:
                _conn_w.rollback()
            except Exception:
                pass
            if not fut.cancelled():
                fut.set_exception(e)


async def write(fn: Callable, *args, **kwargs) -> Any:
    """投递写操作到单写者队列，返回 fn 的返回值。"""
    assert _queue is not None, "db 尚未 open()"
    fut = asyncio.get_running_loop().create_future()
    _queue.put_nowait((fn, args, kwargs, fut))
    return await fut


def _read_sync(fn: Callable, args: tuple, kwargs: dict) -> Any:
    with _lock_r:
        assert _conn_r is not None
        return fn(_conn_r, *args, **kwargs)


async def read(fn: Callable, *args, **kwargs) -> Any:
    """读操作：to_thread 直连（WAL 并发读安全）。"""
    return await asyncio.to_thread(_read_sync, fn, args, kwargs)


# ==================== DAO：project_core ====================

async def get_project() -> Optional[dict]:
    def q(conn):
        cur = conn.execute("SELECT * FROM project_core WHERE id='main'")
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None
    return await read(q)


async def ensure_project_row(**defaults) -> None:
    """确保 project_core 主行存在（幂等；仅在缺失时以默认值插入）。"""
    cols = ["id"] + list(defaults.keys())
    vals = ["main"] + list(defaults.values())
    marks = ", ".join("?" for _ in cols)
    def w(conn):
        conn.execute(
            f"INSERT INTO project_core ({', '.join(cols)}) VALUES ({marks}) "
            f"ON CONFLICT(id) DO NOTHING", vals)
    await write(w)


async def update_project(**fields) -> None:
    fields["updated_at"] = "datetime('now')"

    def w(conn):
        keys = [k for k in fields if k != "updated_at"]
        sets = ", ".join(f"{k}=?" for k in keys) + ", updated_at=datetime('now')"
        conn.execute(
            f"INSERT INTO project_core (id) VALUES ('main') "
            f"ON CONFLICT(id) DO NOTHING"
        )
        conn.execute(
            f"UPDATE project_core SET {sets} WHERE id='main'",
            [fields[k] for k in keys],
        )
    await write(w)


# ==================== DAO：chapters ====================

async def list_chapters() -> list[dict]:
    def q(conn):
        cur = conn.execute("SELECT * FROM chapters ORDER BY order_index ASC")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


async def get_chapter(chapter_id: str) -> Optional[dict]:
    def q(conn):
        cur = conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None
    return await read(q)


async def insert_chapter(data: dict) -> None:
    def w(conn):
        cols = ", ".join(data.keys())
        marks = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO chapters ({cols}) VALUES ({marks})",
                     list(data.values()))
    await write(w)


async def update_chapter(chapter_id: str, **fields) -> None:
    def w(conn):
        keys = list(fields.keys())
        sets = ", ".join(f"{k}=?" for k in keys)
        conn.execute(f"UPDATE chapters SET {sets} WHERE id=?",
                     [fields[k] for k in keys] + [chapter_id])
    await write(w)


async def delete_chapter_row(chapter_id: str) -> None:
    def w(conn):
        for table, col in [
            ("canon_timeline", "chapter_id"), ("canon_summaries", "chapter_id"),
            ("canon_char_state", "chapter_id"), ("drafts", "chapter_id"),
            ("post_process_steps", "chapter_id"),
        ]:
            conn.execute(f"DELETE FROM {table} WHERE {col}=?", (chapter_id,))
        conn.execute("DELETE FROM chapters WHERE id=?", (chapter_id,))
        try:  # RAG 索引清理（vec 表可能不存在）
            conn.execute(
                "DELETE FROM rag_chunks WHERE chunk_id IN "
                "(SELECT chunk_id FROM rag_meta WHERE source='chapter' "
                "AND ref_id=?)", (chapter_id,))
            conn.execute(
                "DELETE FROM rag_meta WHERE source='chapter' AND ref_id=?",
                (chapter_id,))
        except sqlite3.OperationalError:
            pass
    await write(w)


async def renumber_chapters(ids_in_order: list[str]) -> None:
    """按 order 顺序重排展示序号 number = 1..n（纯展示字段，不影响任何外键）。"""
    def w(conn):
        for i, cid in enumerate(ids_in_order, start=1):
            conn.execute("UPDATE chapters SET number=? WHERE id=?", (i, cid))
    await write(w)


# ==================== DAO：characters ====================

async def list_characters() -> list[dict]:
    def q(conn):
        cur = conn.execute("SELECT * FROM characters ORDER BY name")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


# ==================== DAO：drafts ====================

async def next_draft_version(chapter_id: str) -> int:
    def q(conn):
        cur = conn.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM drafts WHERE chapter_id=?",
            (chapter_id,))
        return cur.fetchone()[0]
    return await read(q)


async def insert_draft(chapter_id: str, version: int, source: str,
                       file_path: str, word_count: int,
                       status: str = "draft") -> None:
    def w(conn):
        conn.execute(
            "INSERT INTO drafts (chapter_id, version, status, source, "
            "file_path, word_count) VALUES (?,?,?,?,?,?)",
            (chapter_id, version, status, source, file_path, word_count))
    await write(w)


async def list_drafts(chapter_id: str) -> list[dict]:
    def q(conn):
        cur = conn.execute(
            "SELECT * FROM drafts WHERE chapter_id=? ORDER BY version DESC",
            (chapter_id,))
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


async def update_draft_file_path(chapter_id: str, version: int,
                                 file_path: str) -> None:
    def w(conn):
        conn.execute(
            "UPDATE drafts SET file_path=? WHERE chapter_id=? AND version=?",
            (file_path, chapter_id, version))
    await write(w)


# ==================== DAO：llm_calls ====================

async def insert_llm_call(provider: str, model: str, purpose: str,
                          prompt_tokens: int = 0, cached_tokens: int = 0,
                          completion_tokens: int = 0, ttft_ms: int = 0,
                          duration_ms: int = 0, cost: float = 0.0,
                          success: int = 1, error_msg: str = "") -> None:
    def w(conn):
        conn.execute(
            "INSERT INTO llm_calls (provider, model, purpose, prompt_tokens, "
            "cached_tokens, completion_tokens, ttft_ms, duration_ms, cost, "
            "success, error_msg) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (provider, model, purpose, prompt_tokens, cached_tokens,
             completion_tokens, ttft_ms, duration_ms, cost, success, error_msg))
    await write(w)


async def session_usage() -> dict:
    """本会话累计用量（按 created_at 在本会话窗口内统计由调用方过滤；P0 简化为全表）。"""
    def q(conn):
        cur = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0), "
            "COALESCE(SUM(completion_tokens),0), COALESCE(SUM(cost),0) "
            "FROM llm_calls")
        row = cur.fetchone()
        return {"prompt_tokens": row[0], "completion_tokens": row[1],
                "cost": row[2]}
    return await read(q)


# ==================== DAO：canon 五表 ====================

async def replace_chapter_timeline(chapter_id: str,
                                   events: list[dict]) -> None:
    """整章时间线替换写入（seq 章内有序由 extractor 生成）。"""
    def w(conn):
        conn.execute("DELETE FROM canon_timeline WHERE chapter_id=?",
                     (chapter_id,))
        for ev in events:
            conn.execute(
                "INSERT INTO canon_timeline (chapter_id, seq, location, "
                "summary, impact) VALUES (?,?,?,?,?)",
                (chapter_id, ev.get("seq", 0), ev.get("location", ""),
                 ev.get("summary", ""), ev.get("impact", "")))
    await write(w)


async def list_chapter_timeline(chapter_id: str) -> list[dict]:
    def q(conn):
        cur = conn.execute(
            "SELECT * FROM canon_timeline WHERE chapter_id=? ORDER BY seq",
            (chapter_id,))
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


async def list_recent_timeline(before_order: float, limit: int = 10) -> list[dict]:
    """最近 N 条时间线事件（按章节序倒序，供上下文注入）。"""
    def q(conn):
        cur = conn.execute(
            "SELECT t.* FROM canon_timeline t JOIN chapters c "
            "ON t.chapter_id=c.id WHERE c.order_index<? "
            "ORDER BY c.order_index DESC, t.seq DESC LIMIT ?",
            (before_order, limit))
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


async def upsert_char_state(character: str, chapter_id: str, *,
                            location: str = "", level: str = "",
                            state: str = "", items: str = "") -> None:
    def w(conn):
        conn.execute(
            "INSERT INTO canon_char_state (character, chapter_id, location, "
            "level, state, items) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(character, chapter_id) DO UPDATE SET "
            "location=excluded.location, level=excluded.level, "
            "state=excluded.state, items=excluded.items",
            (character, chapter_id, location, level, state, items))
    await write(w)


async def latest_char_states() -> list[dict]:
    """每角色最近一次状态快照（按章节 order 取最大）。"""
    def q(conn):
        cur = conn.execute(
            "SELECT s.character, s.chapter_id, s.location, s.level, "
            "s.state, s.items FROM canon_char_state s "
            "JOIN chapters c ON s.chapter_id=c.id "
            "WHERE c.order_index = (SELECT MAX(c2.order_index) "
            "FROM canon_char_state s2 JOIN chapters c2 "
            "ON s2.chapter_id=c2.id WHERE s2.character=s.character)")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


async def latest_char_state_before(character: str, chapter_id: str) -> Optional[dict]:
    """该角色在指定章之前的最近快照（逆向回滚用）。"""
    def q(conn):
        cur = conn.execute(
            "SELECT s.* FROM canon_char_state s JOIN chapters c "
            "ON s.chapter_id=c.id WHERE s.character=? AND c.order_index<"
            "(SELECT order_index FROM chapters WHERE id=?) "
            "ORDER BY c.order_index DESC LIMIT 1",
            (character, chapter_id))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None
    return await read(q)


async def upsert_summary(chapter_id: str, summary: str) -> None:
    def w(conn):
        conn.execute(
            "INSERT INTO canon_summaries (chapter_id, summary) VALUES (?,?) "
            "ON CONFLICT(chapter_id) DO UPDATE SET summary=excluded.summary",
            (chapter_id, summary))
    await write(w)


async def get_summary(chapter_id: str) -> Optional[str]:
    def q(conn):
        cur = conn.execute(
            "SELECT summary FROM canon_summaries WHERE chapter_id=?",
            (chapter_id,))
        row = cur.fetchone()
        return row[0] if row else None
    return await read(q)


async def list_recent_summaries(before_order: float, limit: int = 2) -> list[dict]:
    def q(conn):
        cur = conn.execute(
            "SELECT s.chapter_id, s.summary, c.number, c.title "
            "FROM canon_summaries s JOIN chapters c ON s.chapter_id=c.id "
            "WHERE c.order_index<? ORDER BY c.order_index DESC LIMIT ?",
            (before_order, limit))
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


async def list_archive_summaries(limit: int = 2) -> list[dict]:
    """长期记忆压缩归档（P3 compression 产物，chapter_id = archive_NNN）。"""
    def q(conn):
        cur = conn.execute(
            "SELECT chapter_id, summary FROM canon_summaries "
            "WHERE chapter_id LIKE 'archive_%' ORDER BY chapter_id DESC "
            "LIMIT ?", (limit,))
        return [{"chapter_id": r[0], "summary": r[1]} for r in cur.fetchall()]
    return await read(q)


async def upsert_plot_line(name: str, *, status: str = "active",
                           started_at: str = "", last_advanced: str = "",
                           description: str = "") -> int:
    def w(conn):
        cur = conn.execute(
            "SELECT id FROM canon_plot_lines WHERE name=?", (name,))
        row = cur.fetchone()
        if row:
            conn.execute(
                "UPDATE canon_plot_lines SET status=?, last_advanced=?, "
                "description=CASE WHEN ?<>'' THEN ? ELSE description END "
                "WHERE id=?",
                (status, last_advanced, description, description, row[0]))
            return row[0]
        cur = conn.execute(
            "INSERT INTO canon_plot_lines (name, status, started_at, "
            "last_advanced, description) VALUES (?,?,?,?,?)",
            (name, status, started_at or last_advanced, last_advanced,
             description))
        return cur.lastrowid
    return await write(w)


async def list_plot_lines() -> list[dict]:
    def q(conn):
        cur = conn.execute("SELECT * FROM canon_plot_lines "
                           "ORDER BY id ASC")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


async def set_plot_line_snapshot(plot_line_id: int, chapter_id: str, *,
                                 status: str, last_advanced: str) -> None:
    """伏笔状态快照（写回波及前落旧状态，供回滚还原）。"""
    def w(conn):
        conn.execute(
            "INSERT INTO canon_plot_line_snapshots (plot_line_id, "
            "chapter_id, status, last_advanced) VALUES (?,?,?,?) "
            "ON CONFLICT(plot_line_id, chapter_id) DO UPDATE SET "
            "status=excluded.status, last_advanced=excluded.last_advanced",
            (plot_line_id, chapter_id, status, last_advanced))
    await write(w)


async def get_plot_line_snapshots_for_chapter(chapter_id: str) -> list[dict]:
    def q(conn):
        cur = conn.execute(
            "SELECT * FROM canon_plot_line_snapshots WHERE chapter_id=?",
            (chapter_id,))
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


async def restore_plot_lines_from_snapshots(chapter_id: str) -> None:
    """回滚：按快照还原本章波及的伏笔状态（单写者事务内执行）。"""
    def w(conn):
        rows = conn.execute(
            "SELECT plot_line_id, status, last_advanced "
            "FROM canon_plot_line_snapshots WHERE chapter_id=?",
            (chapter_id,)).fetchall()
        for pid, status, last_adv in rows:
            conn.execute(
                "UPDATE canon_plot_lines SET status=?, last_advanced=? "
                "WHERE id=?", (status, last_adv, pid))
    await write(w)


async def upsert_fact(category: str, statement: str,
                      since_chapter_id: str = "") -> None:
    def w(conn):
        conn.execute(
            "INSERT INTO canon_facts (category, statement, "
            "since_chapter_id) VALUES (?,?,?)",
            (category, statement, since_chapter_id))
    await write(w)


async def list_facts() -> list[dict]:
    def q(conn):
        cur = conn.execute("SELECT * FROM canon_facts ORDER BY id ASC")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


# ==================== DAO：characters ====================

async def upsert_character(name: str, **fields) -> None:
    def w(conn):
        cur = conn.execute(
            "SELECT 1 FROM characters WHERE name=?", (name,))
        if cur.fetchone() is None:
            cols = ["name"] + list(fields.keys())
            vals = [name] + list(fields.values())
            marks = ", ".join("?" for _ in cols)
            conn.execute(f"INSERT INTO characters ({', '.join(cols)}) "
                         f"VALUES ({marks})", vals)
        else:
            if fields:
                keys = list(fields.keys())
                sets = ", ".join(f"{k}=?" for k in keys)
                conn.execute(f"UPDATE characters SET {sets} WHERE name=?",
                             [fields[k] for k in keys] + [name])
    await write(w)


async def get_character(name: str) -> Optional[dict]:
    def q(conn):
        cur = conn.execute("SELECT * FROM characters WHERE name=?", (name,))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None
    return await read(q)


async def delete_character(name: str) -> None:
    """删除角色主表记录（设计界面人物管理用）。"""
    def w(conn):
        conn.execute("DELETE FROM characters WHERE name=?", (name,))
    await write(w)


async def delete_characters_created_at(chapter_id: str) -> int:
    """回滚：删除「本章新登场」的角色主表记录（防僵尸实体）。"""
    def w(conn):
        cur = conn.execute(
            "DELETE FROM characters WHERE created_at_chapter_id=?",
            (chapter_id,))
        return cur.rowcount
    return await write(w)


async def revert_characters_cs(chapter_id: str) -> None:
    """回滚：将本章更新过 cs_* 的角色回退到上一章快照（无快照则清空）。"""
    def w(conn):
        rows = conn.execute(
            "SELECT character FROM canon_char_state WHERE chapter_id=?",
            (chapter_id,)).fetchall()
        for (name,) in rows:
            cur = conn.execute(
                "SELECT s.location, s.level, s.state, s.items, s.chapter_id "
                "FROM canon_char_state s JOIN chapters c ON s.chapter_id=c.id "
                "WHERE s.character=? AND c.order_index<"
                "(SELECT order_index FROM chapters WHERE id=?) "
                "ORDER BY c.order_index DESC LIMIT 1", (name, chapter_id))
            prev = cur.fetchone()
            if prev:
                conn.execute(
                    "UPDATE characters SET cs_location=?, cs_level=?, "
                    "cs_physical=?, cs_items=?, cs_chapter_id=? "
                    "WHERE name=?",
                    (prev[0], prev[1], prev[2], prev[3], prev[4], name))
            else:
                conn.execute(
                    "UPDATE characters SET cs_location='', cs_level='', "
                    "cs_physical='', cs_items='', cs_knowledge='[]', "
                    "cs_chapter_id='' WHERE name=?", (name,))
    await write(w)


async def delete_canon_for_chapter(chapter_id: str) -> None:
    def w(conn):
        for table in ("canon_timeline", "canon_summaries",
                      "canon_char_state", "canon_facts",
                      "canon_plot_line_snapshots", "diagnostics_dismissed"):
            col = "chapter_id"
            conn.execute(f"DELETE FROM {table} WHERE {col}=?", (chapter_id,))
    await write(w)


# ==================== DAO：post_process_steps ====================

async def get_pp_step(chapter_id: str, step_key: str) -> Optional[dict]:
    def q(conn):
        cur = conn.execute(
            "SELECT * FROM post_process_steps WHERE chapter_id=? AND "
            "step_key=?", (chapter_id, step_key))
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None
    return await read(q)


async def record_pp_step(chapter_id: str, step_key: str, *, ok: bool,
                         critical: bool = False, error_msg: str = "") -> None:
    def w(conn):
        conn.execute(
            "INSERT INTO post_process_steps (chapter_id, step_key, ok, "
            "critical, error_msg, attempts) VALUES (?,?,?,?,?,1) "
            "ON CONFLICT(chapter_id, step_key) DO UPDATE SET ok=excluded.ok, "
            "critical=excluded.critical, error_msg=excluded.error_msg, "
            "attempts=attempts+1",
            (chapter_id, step_key, 1 if ok else 0, 1 if critical else 0,
             error_msg[:500]))
    await write(w)


# ==================== DAO：诊断忽略 ====================

async def dismiss_diagnostic(chapter_id: str, fingerprint: str) -> None:
    def w(conn):
        conn.execute(
            "INSERT INTO diagnostics_dismissed (chapter_id, fingerprint) "
            "VALUES (?,?) ON CONFLICT DO NOTHING",
            (chapter_id, fingerprint))
    await write(w)


async def list_dismissed(chapter_id: str) -> set[str]:
    def q(conn):
        cur = conn.execute(
            "SELECT fingerprint FROM diagnostics_dismissed WHERE chapter_id=?",
            (chapter_id,))
        return {r[0] for r in cur.fetchall()}
    return await read(q)


async def usage_by_purpose() -> list[dict]:
    """用量统计（P2）：按 用途+模型 聚合。"""
    def q(conn):
        cur = conn.execute(
            "SELECT purpose, model, provider, COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            "COALESCE(SUM(cost),0) AS cost, "
            "COALESCE(AVG(CASE WHEN success=1 THEN duration_ms END),0) "
            "AS avg_ms, "
            "SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failures "
            "FROM llm_calls GROUP BY purpose, model, provider "
            "ORDER BY purpose")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


# ==================== DAO：设计域（对话 / 世界观分节 / 引导进度）====================

# 结构化世界观默认分节（首次使用时按需创建，顺序即展示顺序）
WORLD_SECTION_DEFAULTS: list[tuple[str, str, float]] = [
    ("power", "力量 / 科技体系", 1.0),
    ("faction", "势力格局", 2.0),
    ("geography", "地理与时代", 3.0),
    ("rules", "世界规则", 4.0),
    ("misc", "自由补充", 9.0),
]


async def list_design_messages(limit: int = 0) -> list[dict]:
    """按时间正序返回对话历史；limit>0 时仅返回最近 limit 条。"""
    def q(conn):
        cur = conn.execute(
            "SELECT * FROM design_chat_messages ORDER BY id ASC")
        rows = [_row_to_dict(cur, r) for r in cur.fetchall()]
        return rows[-limit:] if limit and limit > 0 else rows
    return await read(q)


async def insert_design_message(role: str, content: str, *,
                                mode: str = "free", step: str = "") -> None:
    def w(conn):
        conn.execute(
            "INSERT INTO design_chat_messages (role, content, mode, step) "
            "VALUES (?,?,?,?)", (role, content, mode, step))
    await write(w)


async def clear_design_messages() -> None:
    """清空本项目的设计对话（切换项目天然隔离，此接口供显式重置）。"""
    def w(conn):
        conn.execute("DELETE FROM design_chat_messages")
    await write(w)


async def list_world_sections() -> list[dict]:
    def q(conn):
        cur = conn.execute(
            "SELECT * FROM world_sections ORDER BY order_index ASC")
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
    return await read(q)


async def upsert_world_section(section_key: str, *, label: str = "",
                               content: str = "",
                               order_index: float = 0.0) -> None:
    """新增或更新单节；label 传空串时保留原值（只改正文的场景）。"""
    def w(conn):
        cur = conn.execute(
            "SELECT 1 FROM world_sections WHERE section_key=?", (section_key,))
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO world_sections (section_key, label, content, "
                "order_index) VALUES (?,?,?,?)",
                (section_key, label or section_key, content, order_index))
        else:
            conn.execute(
                "UPDATE world_sections SET "
                "label=CASE WHEN ?<>'' THEN ? ELSE label END, content=? "
                "WHERE section_key=?",
                (label, label, content, section_key))
    await write(w)


async def delete_world_section(section_key: str) -> None:
    def w(conn):
        conn.execute("DELETE FROM world_sections WHERE section_key=?",
                     (section_key,))
    await write(w)


async def get_design_guide() -> Optional[dict]:
    def q(conn):
        cur = conn.execute("SELECT * FROM design_guide WHERE id='main'")
        row = cur.fetchone()
        return _row_to_dict(cur, row) if row else None
    return await read(q)


async def upsert_design_guide(*, idea: str = "", current_step: str = "core",
                              steps_done: str = "[]") -> None:
    """写入引导进度（全量字段；UI 每次提交完整状态）。"""
    def w(conn):
        conn.execute(
            "INSERT INTO design_guide (id, idea, current_step, steps_done, "
            "updated_at) VALUES ('main', ?, ?, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET idea=excluded.idea, "
            "current_step=excluded.current_step, "
            "steps_done=excluded.steps_done, updated_at=datetime('now')",
            (idea, current_step, steps_done))
    await write(w)
