from dataclasses import dataclass
import os
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    db_path: str = "./data/archivecodec.db"
    rule_version: str = "2026.11"
    timezone: str = "Asia/Shanghai"
    sample_dir: str = str(Path(__file__).resolve().parent.parent / "sample_data")

    @classmethod
    def from_env(cls):
        return cls(db_path=os.getenv("DB_PATH", cls.db_path),
                   rule_version=os.getenv("RULE_VERSION", cls.rule_version),
                   sample_dir=os.getenv("SAMPLE_DIR", cls.sample_dir))
