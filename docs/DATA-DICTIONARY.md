# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |
| `line_no` | string | 否 | 档案行号，与档号逐字关联为主键 |
| `version` | integer | 否 | 行版本，每次变更单调递增，供 compare-and-set |
| `op_id` | string | 否 | 归一操作编号，撤销以它为粒度 |
| `undo_id` | string | 否 | 撤销操作编号，一个归一操作至多一个 |
| `planned_value` | string | 否 | 撤销条目计划恢复的原文 |
| `expected_version` | integer | 否 | 撤销补偿要求的行版本（归一落库版本） |
| `conflict_version` | integer | 是 | 裁决令牌，须等于行当前版本，过期拒绝 |
| `decision` | string | 否 | 裁决取值：`keep` 保留现文 / `restore` 恢复原文 |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。
