import threading
import pytest
from app.errors import DomainError
from app.modules.m4_content.store import ContentStore
from app.modules.m4_content.service import ContentService, CrashFault

ROWS = [
    {"archive_id": "000123", "line_no": "001", "title": "年度　 汇编\r\n上册"},
    {"archive_id": "000123", "line_no": "002", "title": "ＡＢＣ－００１"},
    {"archive_id": "000124", "line_no": "001", "title": "馆藏\t目录"},
    {"archive_id": "9007199254740993", "line_no": "007", "title": "会议　　记录"},
]

@pytest.fixture
def svc(tmp_path):
    service = ContentService(ContentStore(str(tmp_path / "m4.db")))
    service.load_rows(ROWS)
    return service

def row(svc, aid, lno):
    return next(r for r in svc.state()["rows"]
                if r["archive_id"] == aid and r["line_no"] == lno)

def undo_sources(svc, aid, lno):
    return [r for r in svc.state()["revisions"]
            if r["archive_id"] == aid and r["line_no"] == lno
            and r["source"].startswith("undo:")]

# ---- 实际档案接入：档号与行号原样关联 ----
def test_load_preserves_archive_and_line_verbatim(svc):
    keys = {(r["archive_id"], r["line_no"]) for r in svc.state()["rows"]}
    assert ("000123", "001") in keys
    assert ("9007199254740993", "007") in keys  # 超大流水号按字符串保留
    assert all(isinstance(a, str) and isinstance(l, str) for a, l in keys)

def test_load_is_idempotent(svc):
    again = svc.load_rows(ROWS)
    assert again["loaded"] == 0 and again["total"] == 4

# ---- 归一核对：全半角、连续空白、换行 ----
def test_normalize_fixes_fullwidth_space_and_newline(svc):
    out = svc.normalize()
    assert out["status"] == "READY" and out["changed"] == 4
    assert row(svc, "000123", "002")["current_value"] == "ABC-001"          # 全半角
    assert row(svc, "000123", "001")["current_value"] == "年度 汇编\n上册"   # 连续空白+换行
    assert row(svc, "000124", "001")["current_value"] == "馆藏 目录"         # 制表空白
    assert row(svc, "9007199254740993", "007")["current_value"] == "会议 记录"

def test_normalize_records_per_row_evidence(svc):
    op = svc.normalize()
    changes = {c["line_no"]: c for c in op["changes"]}
    assert changes["002"]["before"] == "ＡＢＣ－００１"
    assert changes["002"]["after"] == "ABC-001"

def test_normalize_second_run_no_changes(svc):
    svc.normalize()
    assert svc.normalize()["status"] == "NO_CHANGES"

# ---- 选择性撤销 ----
def test_undo_restores_rows_without_later_revision(svc):
    op = svc.normalize()
    result = svc.undo(op["op_id"])
    assert result["status"] == "completed"
    assert row(svc, "000123", "002")["current_value"] == "ＡＢＣ－００１"
    assert row(svc, "000123", "001")["current_value"] == "年度　 汇编\r\n上册"

def test_undo_keeps_rows_with_manual_revision(svc):
    op = svc.normalize()
    svc.revise("000123", "002", "馆员定稿 ABC-001", row(svc, "000123", "002")["version"])
    result = svc.undo(op["op_id"])
    assert result["status"] == "conflicts_open"
    assert row(svc, "000123", "002")["current_value"] == "馆员定稿 ABC-001"  # 保留现文
    assert row(svc, "000124", "001")["current_value"] == "馆藏\t目录"        # 其余恢复原文
    conflicts = svc.state()["conflicts"]
    assert [(c["archive_id"], c["line_no"]) for c in conflicts] == [("000123", "002")]

def test_same_literal_revision_still_counts(svc):
    op = svc.normalize()
    # 馆员把值"改回"与归一结果相同的字面，也算后续修订
    svc.revise("000123", "002", "ABC-001", row(svc, "000123", "002")["version"])
    result = svc.undo(op["op_id"])
    assert result["status"] == "conflicts_open"
    assert row(svc, "000123", "002")["current_value"] == "ABC-001"  # 不得自动恢复原文

# ---- 逐条裁决 ----
def _conflicted(svc):
    op = svc.normalize()
    svc.revise("000123", "002", "馆员定稿", row(svc, "000123", "002")["version"])
    svc.undo(op["op_id"])
    return svc.state()["conflicts"][0]

def test_adjudicate_restore(svc):
    conflict = _conflicted(svc)
    out = svc.adjudicate(conflict["undo_id"], "000123", "002", "restore",
                        conflict["current_version"])
    assert out["status"] == "READY"
    assert row(svc, "000123", "002")["current_value"] == "ＡＢＣ－００１"
    assert svc.state()["undos"][0]["status"] == "closed"

def test_adjudicate_keep(svc):
    conflict = _conflicted(svc)
    svc.adjudicate(conflict["undo_id"], "000123", "002", "keep",
                   conflict["current_version"])
    assert row(svc, "000123", "002")["current_value"] == "馆员定稿"

def test_stale_adjudication_rejected(svc):
    conflict = _conflicted(svc)
    stale_token = conflict["current_version"]
    svc.revise("000123", "002", "再次修订", stale_token)  # 冲突浮现后行又被修订
    with pytest.raises(DomainError) as err:
        svc.adjudicate(conflict["undo_id"], "000123", "002", "restore", stale_token)
    assert err.value.code == "STALE_VERSION"
    assert row(svc, "000123", "002")["current_value"] == "再次修订"

def test_adjudicate_twice_rejected(svc):
    conflict = _conflicted(svc)
    svc.adjudicate(conflict["undo_id"], "000123", "002", "keep", conflict["current_version"])
    with pytest.raises(DomainError) as err:
        svc.adjudicate(conflict["undo_id"], "000123", "002", "keep",
                       row(svc, "000123", "002")["version"])
    assert err.value.code == "NO_CONFLICT"

# ---- 中断续跑与重试幂等 ----
def test_undo_resumes_after_crash_without_duplicate_compensation(svc, tmp_path):
    op = svc.normalize()
    plan = svc.plan_undo(op["op_id"])
    with pytest.raises(CrashFault):
        svc.execute_undo(plan["undo_id"], fail_after=1)
    mid = svc.state()
    assert sum(i["status"] == "restored" for i in mid["undo_items"]) == 1
    assert sum(i["status"] == "pending" for i in mid["undo_items"]) == 3
    # 模拟进程重启：同一库文件上重建服务实例，从已完成处续跑
    restarted = ContentService(ContentStore(str(tmp_path / "m4.db")))
    result = restarted.execute_undo(plan["undo_id"])
    assert result["status"] == "completed"
    for r in ROWS:  # 每行补偿恰好一次
        assert len(undo_sources(restarted, r["archive_id"], r["line_no"])) == 1
    assert row(restarted, "000123", "002")["current_value"] == "ＡＢＣ－００１"

def test_undo_retry_is_idempotent(svc):
    op = svc.normalize()
    first = svc.undo(op["op_id"])
    versions = {r["line_no"]: r["version"] for r in svc.state()["rows"]}
    second = svc.undo(op["op_id"])
    assert second["undo_id"] == first["undo_id"] and second["resumed"] is True
    assert {r["line_no"]: r["version"] for r in svc.state()["rows"]} == {
        k: v for k, v in versions.items()}

# ---- 并发修订不被撤销覆盖 ----
def test_concurrent_revision_not_overwritten_by_undo(svc):
    op = svc.normalize()
    plan = svc.plan_undo(op["op_id"])  # 撤销已计划未执行
    svc.revise("000123", "002", "并发修订", row(svc, "000123", "002")["version"])
    svc.execute_undo(plan["undo_id"])
    assert row(svc, "000123", "002")["current_value"] == "并发修订"
    assert row(svc, "000124", "001")["current_value"] == "馆藏\t目录"

def test_undo_and_revisions_race_without_lost_updates(tmp_path):
    service = ContentService(ContentStore(str(tmp_path / "race.db")))
    service.load_rows([{"archive_id": "000123", "line_no": f"{i:03d}", "title": f"Ａ{i}"}
                       for i in range(6)])
    op = service.normalize()
    plan = service.plan_undo(op["op_id"])

    def revise_loop():
        for _ in range(6):
            for _ in range(20):  # 版本竞争时按新版本重试
                r = row(service, "000123", "000")
                try:
                    service.revise("000123", "000", f"并发{r['version']}", r["version"])
                    break
                except DomainError as e:
                    if e.code != "STALE_VERSION":
                        raise

    threads = [threading.Thread(target=revise_loop) for _ in range(3)]
    for t in threads:
        t.start()
    service.execute_undo(plan["undo_id"])
    for t in threads:
        t.join()
    state = service.state()
    r0 = row(service, "000123", "000")
    logged = [x for x in state["revisions"]
              if x["archive_id"] == "000123" and x["line_no"] == "000"]
    # 版本数 = 归一(1) + 成功修订数 + 撤销补偿(0或1)，无丢失更新
    assert r0["version"] == 1 + len(logged)
    assert all(x["new_value"] != "" for x in logged)

# ---- 同档案先后两次归一，再撤销较早操作 ----
def test_two_normalizes_then_undo_earlier_keeps_later_result(tmp_path):
    service = ContentService(ContentStore(str(tmp_path / "two.db")))
    service.load_rows([{"archive_id": "000123", "line_no": "001", "title": "ＡＢＣ　 －００１"}])
    op1 = service.normalize(profile="width")   # ＡＢＣ　 －００１ -> ABC  -001
    assert row(service, "000123", "001")["current_value"] == "ABC  -001"
    op2 = service.normalize(profile="space")   # ABC  -001 -> ABC -001
    assert row(service, "000123", "001")["current_value"] == "ABC -001"
    earlier = service.undo(op1["op_id"])       # 撤销较早操作
    assert earlier["status"] == "conflicts_open"
    assert row(service, "000123", "001")["current_value"] == "ABC -001"  # 保留较晚归一结果
    later = service.undo(op2["op_id"])         # 撤销较晚操作则回到 op1 的结果
    assert later["status"] == "completed"
    assert row(service, "000123", "001")["current_value"] == "ABC  -001"

# ---- API 集成 ----
def test_api_flow(client):
    page = client.get("/content")
    assert page.status_code == 200 and "000123" in page.text
    op = client.post("/api/content/normalize", json={}).json()
    assert op["status"] == "READY" and op["changed"] >= 1
    state = client.get("/api/content/state").json()
    target = next(r for r in state["rows"] if (r["archive_id"], r["line_no"]) == ("000123", "002"))
    rev = client.post("/api/content/revise", json={
        "archive_id": "000123", "line_no": "002", "value": "人工定稿",
        "base_version": target["version"]})
    assert rev.status_code == 200
    undone = client.post("/api/content/undo", json={"op_id": op["op_id"]}).json()
    assert undone["status"] == "conflicts_open"
    conflict = client.get("/api/content/state").json()["conflicts"][0]
    stale = client.post("/api/content/adjudicate", json={
        "undo_id": conflict["undo_id"], "archive_id": "000123", "line_no": "002",
        "decision": "restore", "conflict_version": conflict["current_version"] - 1})
    assert stale.status_code == 422 and stale.json()["error"] == "STALE_VERSION"
    ok = client.post("/api/content/adjudicate", json={
        "undo_id": conflict["undo_id"], "archive_id": "000123", "line_no": "002",
        "decision": "keep", "conflict_version": conflict["current_version"]})
    assert ok.status_code == 200
    final = client.get("/api/content/state").json()
    assert next(r for r in final["rows"] if r["line_no"] == "002")["current_value"] == "人工定稿"

def test_api_revise_stale_version(client):
    client.post("/api/content/normalize", json={})
    res = client.post("/api/content/revise", json={
        "archive_id": "000123", "line_no": "002", "value": "x", "base_version": 99})
    assert res.status_code == 422 and res.json()["error"] == "STALE_VERSION"
