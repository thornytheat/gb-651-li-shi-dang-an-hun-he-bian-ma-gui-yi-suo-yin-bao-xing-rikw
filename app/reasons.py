REASONS = {
    "BAD_COLUMNS": "字段数量不符", "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突", "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义", "STALE_VERSION": "提交版本已过期",
    "ROW_NOT_FOUND": "档案行不存在", "UNKNOWN_OP": "归一操作不存在",
    "UNKNOWN_UNDO": "撤销操作不存在", "NO_CONFLICT": "该条目不在待裁决状态",
    "BAD_DECISION": "裁决取值非法", "BAD_PROFILE": "归一方案未登记",
}

def reason(code: str) -> str:
    return REASONS.get(code, "未登记原因")
