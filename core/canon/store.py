"""Canon 五表读写与领域操作（方案 5.4，store 层）。

机械 CRUD 在 core/db.py；本模块组装领域操作：
- Canon 束（上下文注入数据源）
- 实体对齐白名单（防僵尸实体，管线 B 硬前置）
- 伏笔休眠监测（纯数值计算）
- 管线 A/B 写回（含伏笔快照）
- 逆向回滚的单事务还原
"""
import json
from typing import Optional

from core import db

VALID_STATUSES = {"outlined", "drafted", "revised", "finalized"}


async def get_canon_bundle(current_chapter: dict) -> dict:
    """组装 Canon 束：当前状态 / 时间线 / 摘要 / 伏笔 / 事实。"""
    order = current_chapter["order_index"]
    states = await db.latest_char_states()
    characters = {c["name"]: c for c in await db.list_characters()}
    timeline = await db.list_recent_timeline(order, limit=10)
    summaries = await db.list_recent_summaries(order, limit=2)
    archives = await db.list_archive_summaries(limit=2)
    plot_lines = [p for p in await db.list_plot_lines()
                  if p["status"] == "active"]
    facts = await db.list_facts()
    return {"characters": characters, "char_states": states,
            "timeline": timeline, "summaries": summaries,
            "archives": archives,
            "plot_lines": plot_lines, "facts": facts}


async def get_entity_whitelist() -> list[str]:
    """实体对齐白名单：已知角色名 + 全部别名（管线 A/B 硬前置，方案 5.4.4）。"""
    names = []
    for c in await db.list_characters():
        names.append(c["name"])
        try:
            aliases = json.loads(c.get("aliases") or "[]")
            if isinstance(aliases, list):
                names.extend(a for a in aliases if a)
        except json.JSONDecodeError:
            pass
    return names


async def get_dormant_plot_lines(current_number: int,
                                 threshold: int = 25) -> list[dict]:
    """伏笔休眠监测（方案 5.4.5）：章差 > 阈值的 active 伏笔。纯数值计算。"""
    chapters = {c["id"]: c["number"] for c in await db.list_chapters()}
    result = []
    for p in await db.list_plot_lines():
        if p["status"] != "active":
            continue
        last_n = chapters.get(p["last_advanced"], 0)
        if current_number - last_n > threshold:
            result.append({**p, "dormant_chapters": current_number - last_n})
    return result


async def get_pov_bundle(chapter: dict) -> Optional[dict]:
    """POV 信息差束（方案 5.4.6）：当前视角角色 + 别名 + 已知情报。"""
    pov_name = (chapter.get("pov") or "").strip()
    if not pov_name:
        return None
    ch = await db.get_character(pov_name)
    if ch is None:
        return None
    knowledge = []
    try:
        knowledge = json.loads(ch.get("cs_knowledge") or "[]")
    except json.JSONDecodeError:
        pass
    aliases = []
    try:
        aliases = json.loads(ch.get("aliases") or "[]")
    except json.JSONDecodeError:
        pass
    return {"name": ch["name"], "aliases": aliases, "knowledge": knowledge,
            "level": ch.get("cs_level", ""), "location": ch.get("cs_location", "")}


# ==================== 管线 A 写回（宏观：摘要/时间线/伏笔）====================

async def writeback_pipeline_a(chapter_id: str, result: dict) -> dict:
    """管线 A 落库：伏笔先快照旧状态再写回（方案 5.5 回滚依据）。"""
    timeline = result.get("timeline") or []
    events = [{"seq": i + 1, "location": ev.get("location", ""),
               "summary": ev.get("summary", ""),
               "impact": ev.get("impact", "")}
              for i, ev in enumerate(timeline) if ev.get("summary")]
    await db.replace_chapter_timeline(chapter_id, events)

    summary = result.get("summary", "")
    if summary:
        await db.upsert_summary(chapter_id, summary)

    plot_lines = result.get("plot_lines") or []
    affected = 0
    for pl in plot_lines:
        name = (pl.get("name") or "").strip()
        if not name:
            continue
        status = pl.get("status", "active")
        status = status if status in ("active", "resolved") else "active"
        existing = next((p for p in await db.list_plot_lines()
                         if p["name"] == name), None)
        if existing:  # 波及前先落快照
            await db.set_plot_line_snapshot(
                existing["id"], chapter_id, status=existing["status"],
                last_advanced=existing["last_advanced"])
        await db.upsert_plot_line(
            name, status=status, last_advanced=chapter_id,
            description=pl.get("description", ""))
        affected += 1
    return {"events": len(events), "plot_lines": affected,
            "summary": summary}


# ==================== 管线 B 写回（微观：角色状态 delta + 事实）====================

async def writeback_pipeline_b(chapter_id: str, result: dict,
                               whitelist: list[str]) -> dict:
    """管线 B 落库：白名单外且未显式声明新角色的实体一律跳过（防僵尸实体）。"""
    whitelist_set = set(whitelist)
    updated, skipped, facts_n = 0, [], 0
    for delta in result.get("characters") or []:
        name = (delta.get("name") or "").strip()
        if not name:
            continue
        is_new = bool(delta.get("is_new_character"))
        if name not in whitelist_set and not is_new:
            skipped.append(name)
            continue
        knowledge_add = delta.get("knowledge_add") or []
        if name not in whitelist_set and is_new:
            await db.upsert_character(
                name, created_at_chapter_id=chapter_id,
                cs_knowledge=json.dumps(knowledge_add, ensure_ascii=False))
        else:
            existing = await db.get_character(name)
            merged = list(json.loads(existing.get("cs_knowledge") or "[]")
                          if existing else [])
            for item in knowledge_add:
                if item not in merged:
                    merged.append(item)
            fields = {
                "cs_location": delta.get("location", ""),
                "cs_level": delta.get("level", ""),
                "cs_physical": delta.get("physical", ""),
                "cs_mental": delta.get("mental", ""),
                "cs_items": delta.get("items", ""),
                "cs_chapter_id": chapter_id,
            }
            if merged:
                fields["cs_knowledge"] = json.dumps(merged,
                                                    ensure_ascii=False)
            await db.upsert_character(name, **fields)
        await db.upsert_char_state(
            name, chapter_id, location=delta.get("location", ""),
            level=delta.get("level", ""), state=delta.get("state", ""),
            items=delta.get("items", ""))
        updated += 1
    for f in result.get("facts") or []:
        statement = (f.get("statement") or "").strip()
        if statement:
            await db.upsert_fact(f.get("category", "其他"), statement,
                                 since_chapter_id=chapter_id)
            facts_n += 1
    return {"characters": updated, "skipped": skipped, "facts": facts_n}


# ==================== 定稿写回自检（纯 SQL，方案 5.4.2）====================

async def writeback_self_check(chapter_id: str) -> list[dict]:
    """extractor 写回后的完整性自检：seq 有序、无悬挂引用。返回问题列表。"""
    problems = []
    events = await db.list_chapter_timeline(chapter_id)
    seqs = [ev["seq"] for ev in events]
    if seqs != sorted(seqs):
        problems.append({"table": "canon_timeline",
                         "problem": f"seq 无序：{seqs}"})
    chapter_ids = {c["id"] for c in await db.list_chapters()}
    for table, sql in [
        ("canon_timeline", "SELECT DISTINCT chapter_id FROM canon_timeline"),
        ("canon_summaries", "SELECT DISTINCT chapter_id FROM canon_summaries"),
        ("canon_char_state", "SELECT DISTINCT chapter_id FROM canon_char_state"),
        ("canon_facts", "SELECT DISTINCT since_chapter_id FROM canon_facts "
                        "WHERE since_chapter_id<>''"),
        ("post_process_steps", "SELECT DISTINCT chapter_id FROM post_process_steps"),
    ]:
        def q(conn, _sql=sql):
            return [r[0] for r in conn.execute(_sql).fetchall()]
        for cid in await db.read(q):
            if cid not in chapter_ids:
                problems.append({"table": table,
                                 "problem": f"悬挂 chapter_id：{cid}"})
    return problems


# ==================== 逆向回滚（方案 5.5，单事务）====================

async def revert_chapter_canon(chapter_id: str) -> dict:
    """单事务还原本章写回的所有 Canon delta（正文保留不删）。"""
    def w(conn):
        stats = {"timeline": 0, "summaries": 0, "char_states": 0,
                 "facts": 0, "plot_lines": 0, "characters_removed": 0}
        cur = conn.execute("DELETE FROM canon_timeline WHERE chapter_id=?",
                           (chapter_id,))
        stats["timeline"] = cur.rowcount
        cur = conn.execute("DELETE FROM canon_summaries WHERE chapter_id=?",
                           (chapter_id,))
        stats["summaries"] = cur.rowcount

        # 角色动态状态回退到上一章快照
        rows = conn.execute(
            "SELECT character FROM canon_char_state WHERE chapter_id=?",
            (chapter_id,)).fetchall()
        stats["char_states"] = len(rows)
        for (name,) in rows:
            prev = conn.execute(
                "SELECT s.location, s.level, s.state, s.items, s.chapter_id "
                "FROM canon_char_state s JOIN chapters c ON s.chapter_id=c.id "
                "WHERE s.character=? AND c.order_index<"
                "(SELECT order_index FROM chapters WHERE id=?) "
                "ORDER BY c.order_index DESC LIMIT 1", (name, chapter_id)
            ).fetchone()
            if prev:
                conn.execute(
                    "UPDATE characters SET cs_location=?, cs_level=?, "
                    "cs_physical=?, cs_items=?, cs_chapter_id=? WHERE name=?",
                    (prev[0], prev[1], prev[2], prev[3], prev[4], name))
            else:
                conn.execute(
                    "UPDATE characters SET cs_location='', cs_level='', "
                    "cs_physical='', cs_items='', cs_knowledge='[]', "
                    "cs_chapter_id='' WHERE name=?", (name,))

        # 客观事实：删除自本章起成立的事实
        cur = conn.execute(
            "DELETE FROM canon_facts WHERE since_chapter_id=?", (chapter_id,))
        stats["facts"] = cur.rowcount

        # 伏笔：先从快照还原既有线，再删本章快照；本章新登记的线直接删除
        snaps = conn.execute(
            "SELECT plot_line_id, status, last_advanced "
            "FROM canon_plot_line_snapshots WHERE chapter_id=?",
            (chapter_id,)).fetchall()
        for pid, status, last_adv in snaps:
            conn.execute(
                "UPDATE canon_plot_lines SET status=?, last_advanced=? "
                "WHERE id=?", (status, last_adv, pid))
            stats["plot_lines"] += 1
        conn.execute("DELETE FROM canon_plot_line_snapshots WHERE chapter_id=?",
                     (chapter_id,))
        cur = conn.execute(
            "DELETE FROM canon_plot_lines WHERE started_at=? AND id NOT IN "
            "(SELECT plot_line_id FROM canon_plot_line_snapshots "
            "WHERE chapter_id=?)", (chapter_id, chapter_id))
        stats["plot_lines_removed"] = cur.rowcount

        # 删除「本章新登场」的角色主表记录（防僵尸实体）
        cur = conn.execute(
            "DELETE FROM characters WHERE created_at_chapter_id=?",
            (chapter_id,))
        stats["characters_removed"] = cur.rowcount

        # 清掉本章语义诊断忽略记录（重新定稿后会重新审查）
        conn.execute("DELETE FROM diagnostics_dismissed WHERE chapter_id=?",
                     (chapter_id,))
        return stats
    return await db.write(w)
