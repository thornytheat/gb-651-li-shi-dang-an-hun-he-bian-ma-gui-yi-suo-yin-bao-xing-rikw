from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS content_rows (
  archive_id TEXT NOT NULL,
  line_no TEXT NOT NULL,
  current_value TEXT NOT NULL,
  version INTEGER NOT NULL,
  updated_by TEXT NOT NULL,
  PRIMARY KEY (archive_id, line_no)
);
CREATE TABLE IF NOT EXISTS content_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS normalize_ops (
  op_id TEXT PRIMARY KEY,
  profile TEXT NOT NULL,
  status TEXT NOT NULL,
  change_count INTEGER NOT NULL,
  seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS normalize_op_items (
  op_id TEXT NOT NULL,
  archive_id TEXT NOT NULL,
  line_no TEXT NOT NULL,
  before_value TEXT NOT NULL,
  after_value TEXT NOT NULL,
  version_before INTEGER NOT NULL,
  version_after INTEGER NOT NULL,
  PRIMARY KEY (op_id, archive_id, line_no)
);
CREATE TABLE IF NOT EXISTS undo_ops (
  undo_id TEXT PRIMARY KEY,
  op_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS undo_items (
  undo_id TEXT NOT NULL,
  archive_id TEXT NOT NULL,
  line_no TEXT NOT NULL,
  planned_value TEXT NOT NULL,
  expected_version INTEGER NOT NULL,
  status TEXT NOT NULL,
  conflict_version INTEGER,
  conflict_value TEXT,
  PRIMARY KEY (undo_id, archive_id, line_no)
);
CREATE TABLE IF NOT EXISTS content_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  archive_id TEXT NOT NULL,
  line_no TEXT NOT NULL,
  old_value TEXT NOT NULL,
  new_value TEXT NOT NULL,
  source TEXT NOT NULL
);
"""

class ContentStore:
    """M4 内容归一的状态存储。档号与行号逐字保留为字符串主键。

    所有多语句写入都包在 BEGIN IMMEDIATE 事务里，行级更新一律带
    version 条件做 compare-and-set，保证并发修订不会被撤销覆盖。
    """

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        db = self.connect()
        try:
            db.executescript(SCHEMA)
        finally:
            db.close()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def txn(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()
