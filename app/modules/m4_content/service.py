from __future__ import annotations
import json
from pathlib import Path
from app.errors import DomainError
from . import core
from .store import ContentStore

class CrashFault(RuntimeError):
    """测试注入的撤销中断故障。"""

def _as_int(value, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DomainError("INVALID_VALUE", f"字段 {field} 应为整数")

class ContentService:
    """内容页档案归一、人工修订与按操作选择性撤销。

    语义约定：
    - 每次行变更（归一/修订/撤销补偿/裁决）都使 version 单调递增并写修订日志；
      改回相同字面也算一次后续修订（按事件而非按值判定）。
    - 撤销以操作为单位逐行补偿：行当前版本仍等于该操作落库版本才恢复原文，
      否则保留现文并生成待裁决冲突。
    - 撤销条目逐行独立事务落库，中断后重跑从 pending 条目续跑，已完成条目
      不会重复补偿。
    """

    def __init__(self, store: ContentStore, sample_path: str | None = None):
        self.store = store
        self.sample_path = sample_path

    # ---- 档案接入 ----
    def _read_sample(self) -> list[dict]:
        if not self.sample_path:
            return []
        rows = []
        for line in Path(self.sample_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            rows.append({"archive_id": str(obj["archive_id"]),
                         "line_no": str(obj["line_no"]),
                         "title": str(obj["title"])})
        return rows

    def load_rows(self, rows: list[dict]) -> dict:
        with self.store.txn() as db:
            before = db.execute("SELECT COUNT(*) c FROM content_rows").fetchone()["c"]
            for r in rows:
                db.execute(
                    "INSERT OR IGNORE INTO content_rows (archive_id, line_no, current_value, version, updated_by)"
                    " VALUES (?,?,?,1,'load')",
                    (str(r["archive_id"]), str(r["line_no"]), str(r["title"])))
            total = db.execute("SELECT COUNT(*) c FROM content_rows").fetchone()["c"]
        return {"status": "READY", "input_count": len(rows), "loaded": total - before, "total": total}

    def load(self) -> dict:
        return self.load_rows(self._read_sample())

    def ensure_loaded(self) -> None:
        with self.store.txn() as db:
            total = db.execute("SELECT COUNT(*) c FROM content_rows").fetchone()["c"]
        if total == 0:
            self.load()

    # ---- 只读状态 ----
    def state(self) -> dict:
        self.ensure_loaded()
        with self.store.txn() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT * FROM content_rows ORDER BY archive_id, line_no").fetchall()]
            ops = [dict(r) for r in db.execute("SELECT * FROM normalize_ops ORDER BY seq").fetchall()]
            undos = [dict(r) for r in db.execute("SELECT * FROM undo_ops ORDER BY seq").fetchall()]
            items = [dict(r) for r in db.execute(
                "SELECT * FROM undo_items ORDER BY undo_id, archive_id, line_no").fetchall()]
            revisions = [dict(r) for r in db.execute(
                "SELECT * FROM content_revisions ORDER BY id").fetchall()]
        by_key = {(r["archive_id"], r["line_no"]): r for r in rows}
        conflicts = []
        for it in items:
            if it["status"] != "conflict":
                continue
            cur = by_key.get((it["archive_id"], it["line_no"]))
            # 裁决令牌取行当前版本：裁决提交时若行已再被修订则按过期拒绝。
            it["current_value"] = cur["current_value"] if cur else None
            it["current_version"] = cur["version"] if cur else None
            conflicts.append(it)
        return {"status": "READY", "input_count": len(rows), "valid_count": len(rows),
                "quarantine_count": 0, "rows": rows, "ops": ops, "undos": undos,
                "undo_items": items, "conflicts": conflicts, "revisions": revisions,
                "reason": "OK"}

    # ---- 归一 ----
    @staticmethod
    def _next_seq(db, key: str) -> int:
        db.execute("INSERT OR IGNORE INTO content_meta VALUES (?, '0')", (key,))
        db.execute("UPDATE content_meta SET value = CAST(value AS INTEGER) + 1 WHERE key=?", (key,))
        return int(db.execute("SELECT value FROM content_meta WHERE key=?", (key,)).fetchone()["value"])

    def normalize(self, profile: str = "standard") -> dict:
        if profile not in core.PROFILES:
            raise DomainError("BAD_PROFILE", f"归一方案未登记: {profile}")
        self.ensure_loaded()
        with self.store.txn() as db:
            rows = db.execute("SELECT * FROM content_rows ORDER BY archive_id, line_no").fetchall()
            changes = []
            for r in rows:
                new = core.normalize_with(profile, r["current_value"])
                if new != r["current_value"]:
                    changes.append((r, new))
            if not changes:
                return {"status": "NO_CHANGES", "profile": profile,
                        "input_count": len(rows), "changed": 0, "changes": []}
            seq = self._next_seq(db, "op_seq")
            op_id = f"nop-{seq:04d}"
            for r, new in changes:
                cur = db.execute(
                    "UPDATE content_rows SET current_value=?, version=version+1, updated_by=?"
                    " WHERE archive_id=? AND line_no=? AND version=?",
                    (new, f"normalize:{op_id}", r["archive_id"], r["line_no"], r["version"]))
                if cur.rowcount != 1:
                    raise DomainError("STALE_VERSION", "归一期间档案行被并发修改")
                db.execute("INSERT INTO normalize_op_items VALUES (?,?,?,?,?,?,?)",
                           (op_id, r["archive_id"], r["line_no"], r["current_value"], new,
                            r["version"], r["version"] + 1))
                db.execute(
                    "INSERT INTO content_revisions (archive_id, line_no, old_value, new_value, source)"
                    " VALUES (?,?,?,?,?)",
                    (r["archive_id"], r["line_no"], r["current_value"], new, f"normalize:{op_id}"))
            db.execute("INSERT INTO normalize_ops VALUES (?,?,?,?,?)",
                       (op_id, profile, "applied", len(changes), seq))
        return {"status": "READY", "op_id": op_id, "profile": profile,
                "input_count": len(rows), "changed": len(changes),
                "changes": [{"archive_id": r["archive_id"], "line_no": r["line_no"],
                             "before": r["current_value"], "after": new} for r, new in changes]}

    # ---- 人工修订 ----
    def revise(self, archive_id: str, line_no: str, value: str, base_version) -> dict:
        base_version = _as_int(base_version, "base_version")
        with self.store.txn() as db:
            row = db.execute("SELECT * FROM content_rows WHERE archive_id=? AND line_no=?",
                             (archive_id, line_no)).fetchone()
            if row is None:
                raise DomainError("ROW_NOT_FOUND", f"档案行不存在: {archive_id}/{line_no}")
            # 改回相同字面也算后续修订：不做值比较，直接落库并递增版本。
            cur = db.execute(
                "UPDATE content_rows SET current_value=?, version=version+1, updated_by='manual'"
                " WHERE archive_id=? AND line_no=? AND version=?",
                (value, archive_id, line_no, base_version))
            if cur.rowcount != 1:
                raise DomainError("STALE_VERSION", "提交版本已过期")
            db.execute(
                "INSERT INTO content_revisions (archive_id, line_no, old_value, new_value, source)"
                " VALUES (?,?,?,?,'manual')",
                (archive_id, line_no, row["current_value"], value))
        return {"status": "READY", "archive_id": archive_id, "line_no": line_no,
                "version": base_version + 1}

    # ---- 选择性撤销 ----
    def plan_undo(self, op_id: str) -> dict:
        with self.store.txn() as db:
            op = db.execute("SELECT * FROM normalize_ops WHERE op_id=?", (op_id,)).fetchone()
            if op is None:
                raise DomainError("UNKNOWN_OP", f"归一操作不存在: {op_id}")
            existing = db.execute("SELECT * FROM undo_ops WHERE op_id=?", (op_id,)).fetchone()
            if existing is not None:
                return {"status": existing["status"], "undo_id": existing["undo_id"],
                        "op_id": op_id, "resumed": True}
            seq = self._next_seq(db, "undo_seq")
            undo_id = f"und-{seq:04d}"
            db.execute("INSERT INTO undo_ops VALUES (?,?,?,?)", (undo_id, op_id, "running", seq))
            items = db.execute(
                "SELECT * FROM normalize_op_items WHERE op_id=? ORDER BY archive_id, line_no",
                (op_id,)).fetchall()
            for it in items:
                db.execute("INSERT INTO undo_items VALUES (?,?,?,?,?,'pending',NULL,NULL)",
                           (undo_id, it["archive_id"], it["line_no"], it["before_value"],
                            it["version_after"]))
        return {"status": "running", "undo_id": undo_id, "op_id": op_id,
                "planned": len(items), "resumed": False}

    def _execute_one_undo_item(self, undo_id: str) -> bool:
        """处理一条 pending 撤销条目，独立事务提交；无待处理条目返回 False。"""
        with self.store.txn() as db:
            item = db.execute(
                "SELECT * FROM undo_items WHERE undo_id=? AND status='pending'"
                " ORDER BY archive_id, line_no LIMIT 1", (undo_id,)).fetchone()
            if item is None:
                return False
            key = (item["archive_id"], item["line_no"])
            row = db.execute("SELECT * FROM content_rows WHERE archive_id=? AND line_no=?",
                             key).fetchone()
            if row is not None and row["version"] == item["expected_version"]:
                # 无后续修订：compare-and-set 恢复原文，并发修订会使版本错位而失败。
                cur = db.execute(
                    "UPDATE content_rows SET current_value=?, version=version+1, updated_by=?"
                    " WHERE archive_id=? AND line_no=? AND version=?",
                    (item["planned_value"], f"undo:{undo_id}", key[0], key[1],
                     item["expected_version"]))
                if cur.rowcount == 1:
                    db.execute(
                        "INSERT INTO content_revisions (archive_id, line_no, old_value, new_value, source)"
                        " VALUES (?,?,?,?,?)",
                        (key[0], key[1], row["current_value"], item["planned_value"],
                         f"undo:{undo_id}"))
                    db.execute(
                        "UPDATE undo_items SET status='restored'"
                        " WHERE undo_id=? AND archive_id=? AND line_no=?",
                        (undo_id, key[0], key[1]))
                    return True
            # 有后续修订（含改回相同字面）：保留现文，登记冲突待逐条裁决。
            db.execute(
                "UPDATE undo_items SET status='conflict', conflict_version=?, conflict_value=?"
                " WHERE undo_id=? AND archive_id=? AND line_no=?",
                (row["version"] if row else None, row["current_value"] if row else None,
                 undo_id, key[0], key[1]))
            return True

    def _finalize_undo(self, undo_id: str) -> dict:
        with self.store.txn() as db:
            counts = {r["status"]: r["c"] for r in db.execute(
                "SELECT status, COUNT(*) c FROM undo_items WHERE undo_id=? GROUP BY status",
                (undo_id,)).fetchall()}
            if counts.get("pending"):
                status = "running"
            elif counts.get("conflict"):
                status = "conflicts_open"
            elif counts.get("adjudicated_keep") or counts.get("adjudicated_restore"):
                status = "closed"
            else:
                status = "completed"
            db.execute("UPDATE undo_ops SET status=? WHERE undo_id=?", (status, undo_id))
            op_status = {"completed": "undone", "closed": "undone",
                         "conflicts_open": "undo_conflicts", "running": "applied"}[status]
            db.execute(
                "UPDATE normalize_ops SET status=?"
                " WHERE op_id=(SELECT op_id FROM undo_ops WHERE undo_id=?)",
                (op_status, undo_id))
        return {"undo_id": undo_id, "status": status, "counts": counts}

    def execute_undo(self, undo_id: str, fail_after: int | None = None) -> dict:
        """逐条执行撤销补偿；中断后重入从 pending 续跑，已完成条目不重复补偿。"""
        with self.store.txn() as db:
            if db.execute("SELECT 1 FROM undo_ops WHERE undo_id=?", (undo_id,)).fetchone() is None:
                raise DomainError("UNKNOWN_UNDO", f"撤销操作不存在: {undo_id}")
        applied = 0
        while self._execute_one_undo_item(undo_id):
            applied += 1
            if fail_after is not None and applied >= fail_after:
                raise CrashFault(f"simulated crash after {applied} undo items")
        return self._finalize_undo(undo_id)

    def undo(self, op_id: str) -> dict:
        plan = self.plan_undo(op_id)
        result = self.execute_undo(plan["undo_id"])
        return {"op_id": op_id, "resumed": plan["resumed"], **result}

    # ---- 逐条裁决 ----
    def adjudicate(self, undo_id: str, archive_id: str, line_no: str,
                   decision: str, conflict_version) -> dict:
        if decision not in ("keep", "restore"):
            raise DomainError("BAD_DECISION", f"裁决取值非法: {decision}")
        conflict_version = _as_int(conflict_version, "conflict_version")
        with self.store.txn() as db:
            item = db.execute(
                "SELECT * FROM undo_items WHERE undo_id=? AND archive_id=? AND line_no=?",
                (undo_id, archive_id, line_no)).fetchone()
            if item is None or item["status"] != "conflict":
                raise DomainError("NO_CONFLICT", "该条目不在待裁决状态")
            row = db.execute("SELECT * FROM content_rows WHERE archive_id=? AND line_no=?",
                             (archive_id, line_no)).fetchone()
            # 过期裁决应拒绝：裁决令牌必须等于行当前版本。
            if row is None or row["version"] != conflict_version:
                raise DomainError("STALE_VERSION", "裁决已过期，请刷新后重新裁决")
            if decision == "restore":
                cur = db.execute(
                    "UPDATE content_rows SET current_value=?, version=version+1, updated_by=?"
                    " WHERE archive_id=? AND line_no=? AND version=?",
                    (item["planned_value"], f"adjudicate:{undo_id}",
                     archive_id, line_no, conflict_version))
                if cur.rowcount != 1:
                    raise DomainError("STALE_VERSION", "裁决已过期，请刷新后重新裁决")
                db.execute(
                    "INSERT INTO content_revisions (archive_id, line_no, old_value, new_value, source)"
                    " VALUES (?,?,?,?,?)",
                    (archive_id, line_no, row["current_value"], item["planned_value"],
                     f"adjudicate:{undo_id}"))
                new_status = "adjudicated_restore"
            else:
                new_status = "adjudicated_keep"
            db.execute(
                "UPDATE undo_items SET status=? WHERE undo_id=? AND archive_id=? AND line_no=?",
                (new_status, undo_id, archive_id, line_no))
        final = self._finalize_undo(undo_id)
        return {"status": "READY", "decision": decision, "undo_id": undo_id,
                "undo_status": final["status"], "counts": final["counts"]}
