import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if "APP_DB" in os.environ:
    tmp = None
else:
    tmp = tempfile.TemporaryDirectory()
    os.environ["APP_DB"] = str(Path(tmp.name) / "test.db")
    os.environ["PACKAGE_DIR"] = str(Path(tmp.name) / "packages")

from app.db import init_db, get_db
from app import service
from datetime import datetime

init_db()
TODAY = date.today()
FUTURE = (TODAY + timedelta(days=30)).isoformat()
PAST = (TODAY - timedelta(days=1)).isoformat()
TODAY_S = TODAY.isoformat()


def req(method, path, body=None, expect=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(os.environ.get("TEST_BASE_URL", "http://127.0.0.1:8000") + path, data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            payload = json.loads(resp.read().decode())
            if expect:
                assert resp.status == expect, (resp.status, payload)
            return payload
    except urllib.error.HTTPError as e:
        payload = json.loads(e.read().decode())
        if expect:
            assert e.code == expect, (e.code, payload)
        return {"__http_error__": e.code, **payload}


with get_db() as db:
    source, sv = service.create_source(db, {
        "title": "公众焦虑科普指南", "author": "专业学会", "version": "v1",
        "archived_text": "焦虑是常见情绪反应，科普应建议必要时寻求合格专业帮助。" * 2,
        "publication_date": TODAY_S,
    })
    reviewer = service.create_reviewer(db, {"name": "林审阅", "role": "临床心理顾问"})
    channel, cv = service.create_channel(db, {
        "name": "本地心理援助热线", "region": "CN-测试市", "contact": "010-TEST",
        "valid_from": TODAY_S, "valid_until": FUTURE, "verified_at": TODAY_S,
        "verification_basis": "官网 2026 年服务公告并经回拨核验",
    })
    project = service.create_project(db, {
        "title": "焦虑情绪科普",
        "professional_content": "讲解焦虑的常见身体信号、正常化求助行为并避免自我诊断。" * 2,
        "intended_audience": "普通成年公众，非急症场景",
        "expression_constraints": ["不得承诺疗效", "不得鼓励自行停药", "避免病耻感标签"],
        "source_version_id": sv["id"], "channel_version_id": cv["id"],
    })
    pid = project["id"]
    narrator = service.create_character(db, pid, {"name": "旁白者", "role": "narrator"})
    host = service.create_character(db, pid, {"name": "主持人", "role": "host"})
    s1 = service.create_segment(db, pid, {"kind": "title", "position": 1, "text": "焦虑不等于懦弱"})
    s2 = service.create_segment(db, pid, {"kind": "narration", "position": 2, "character_id": narrator["id"],
                                          "text": "焦虑可能表现为心跳加快，持续困扰时请寻求专业帮助。",
                                          "context_note": "位于标题后，用于一般科普"})
    s3 = service.create_segment(db, pid, {"kind": "dialogue", "position": 3, "character_id": host["id"],
                                          "text": "如果你感到安全风险，请使用当地官方求助渠道。"})
    s4 = service.create_segment(db, pid, {"kind": "subtitle", "position": 4,
                                          "text": "本内容不做个体诊断或治疗建议。"})
    service.update_segment(db, pid, s2["id"], {
        "text": "焦虑可能表现为心跳加快或入睡困难；持续困扰时请寻求合格专业帮助。",
        "reviewer_id": reviewer["id"],
        "reason": "降低诊断化表述，保留求助建议",
    })
    revisions = service.list_revisions(db, pid)
    assert revisions and revisions[0]["reviewer_id"] == reviewer["id"]
    service.create_vocabulary(db, {"name": "诊断治疗高风险词", "terms": ["诊断", "治疗", "停药"]})
    snap1 = service.create_snapshot(db, pid, reason="INITIAL")
    sid1 = snap1["id"]

    # 词表命中只是线索；存在命中且未审时不能导出，但未命中也不会被标记为安全证明。
    hits = len(snap1["vocabulary_hits"])
    assert hits > 0
    initial_readiness = service.evaluate_readiness(db, pid, sid1)
    assert "SEGMENT_REVIEW_PENDING_OR_INVALID" in initial_readiness["blocking"]

    service.add_review(db, sid1, {"reviewer_id": reviewer["id"], "scope": "whole", "status": "approved",
                              "internal_comment": "内部意见：确认边界清楚"})
    ready1 = service.evaluate_readiness(db, pid, sid1)
    assert ready1["ok"], ready1

    # 同一不可变快照供预览和素材清单使用。
    job1, _ = service.enqueue_job(db, pid, sid1, "script_preview")
    job2, _ = service.enqueue_job(db, pid, sid1, "material_list")
    duplicate_job, reused = service.enqueue_job(db, pid, sid1, "script_preview")
    assert reused and duplicate_job["id"] == job1["id"]
    service.complete_job(db, job1["id"], service.generate_job_result(db, job1))
    service.complete_job(db, job2["id"], service.generate_job_result(db, job2))
    completed = db.execute("SELECT * FROM jobs WHERE id=?", (job1["id"],)).fetchone()
    assert json.loads(completed["result_json"])["snapshot_id"] == sid1

# HTTP acceptance through public API (server must be running).
source_api = req("GET", "/api/sources", expect=200)[0]
project_api = req("GET", f"/api/projects/{pid}", expect=200)
assert project_api["current_snapshot_id"] == sid1

# 导出并由 worker 处理。
queued_export = req("POST", f"/api/projects/{pid}/exports", {"snapshot_id": sid1, "request_key": "e1"}, 201)
dup_export = req("POST", f"/api/projects/{pid}/exports", {"snapshot_id": sid1, "request_key": "e1"}, 200)
assert dup_export["id"] == queued_export["id"]
with get_db() as db:
    export = service.process_due_export(db, queued_export["id"], Path(os.environ["PACKAGE_DIR"]))
    assert export["status"] == "ready"
    grant, _ = service.grant_download(db, export["id"], "user-a", ttl_hours=24)
    token = grant["token"]
    package = json.loads(Path(export["package_path"]).read_text())
    assert package["review_snapshot"]["id"] == sid1
    assert package["source_reference"]["content_hash"]
    package_text = json.dumps(package, ensure_ascii=False)
    assert "内部意见" not in package_text and "林审阅" not in package_text

download = req("GET", f"/api/exports/{export['id']}/download?token={token}", expect=200)
assert download["review_snapshot"]["id"] == sid1
revoke_resp = req("PATCH", f"/api/downloads/{token}/revoke", {}, expect=200)
revoked = req("GET", f"/api/exports/{export['id']}/download?token={token}", expect=410)
assert revoked["error"]
revoked_export = req("PATCH", f"/api/exports/{export['id']}/revoke", {}, expect=200)
assert revoked_export["status"] == "revoked"

# 重复发布：第一次排队，同 request_key 返回 409，不新建第二请求。
pub1 = req("POST", f"/api/projects/{pid}/publish",
           {"snapshot_id": sid1, "channel_name": "web", "request_key": "p1"}, 201)
pub2 = req("POST", f"/api/projects/{pid}/publish",
           {"snapshot_id": sid1, "channel_name": "web", "request_key": "p1"}, 409)
assert pub2["duplicate"] is True
with get_db() as db:
    pub_before_change = service.process_due_publication(db, pub1["publication"]["id"],
                                                        Path(os.environ["PACKAGE_DIR"]))
    assert pub_before_change["status"] == "published"

# 适用人群变更：产生新快照并使旧整片批准失效；已排队请求在执行前复验失败。
with get_db() as db:
    queued = service.enqueue_publication(db, pid, sid1, "web", "p-queued-audience")
    assert queued["duplicate"] is False
req("PATCH", f"/api/projects/{pid}", {"intended_audience": "青少年照护者"}, expect=200)
db.commit()  # end this connection's read snapshot before observing the HTTP transaction
with get_db() as db:
    project = service.get_project(db, pid)
    sid2 = project["current_snapshot_id"]
    assert sid2 != sid1
    invalidations = db.execute("SELECT rule_code FROM review_invalidations WHERE snapshot_id=?", (sid1,)).fetchall()
    assert any(r["rule_code"] == "AUDIENCE_CHANGED" for r in invalidations)
    readiness2 = service.evaluate_readiness(db, pid, sid2)
    assert not readiness2["ok"]
    blocked = req("POST", f"/api/projects/{pid}/publish",
                  {"snapshot_id": sid2, "channel_name": "web", "request_key": "p-audience"}, 409)
    assert "SEGMENT_REVIEW_PENDING_OR_INVALID" in str(blocked)

with get_db() as db:
    invalidated_pub = service.process_due_publication(db, queued["publication"]["id"],
                                                      Path(os.environ["PACKAGE_DIR"]))
    assert invalidated_pub["status"] == "invalidated"

# 段落重排：段落批准绑定上下文，重排后旧批准不允许沿用。
with get_db() as db:
    service.add_review(db, sid2, {"reviewer_id": reviewer["id"], "scope": "segment", "segment_id": s2["id"],
                              "status": "approved", "internal_comment": "单段通过"})
    service.add_review(db, sid2, {"reviewer_id": reviewer["id"], "scope": "whole",
                              "status": "approved", "internal_comment": "重排前整片通过"})
    queued_before_reorder = service.enqueue_publication(db, pid, sid2, "web", "p-queued-reorder")
    service.reorder_segments(db, pid, [s1["id"], s4["id"], s2["id"], s3["id"]])
    sid3 = service.get_project(db, pid)["current_snapshot_id"]
    r3 = service.evaluate_readiness(db, pid, sid3)
    reorder_invalidations = [r["rule_code"] for r in db.execute(
        "SELECT rule_code FROM review_invalidations WHERE snapshot_id=?", (sid2,)
    ).fetchall()]
    assert not r3["ok"] and "SEGMENT_REORDERED" in reorder_invalidations
    invalidated_reorder_pub = service.process_due_publication(
        db, queued_before_reorder["publication"]["id"], Path(os.environ["PACKAGE_DIR"])
    )
    assert invalidated_reorder_pub["status"] == "invalidated"

# 角色替换：当前工作快照改变，旧批准失效，需要重审。
with get_db() as db:
    new_host = service.create_character(db, pid, {"name": "新主持人", "role": "host"})
    service.add_review(db, sid3, {"reviewer_id": reviewer["id"], "scope": "whole", "status": "approved"})
    queued_before_replace = service.enqueue_publication(db, pid, sid3, "web", "p-queued-character")
    service.replace_character(db, pid, host["id"], new_host["id"])
    sid4 = service.get_project(db, pid)["current_snapshot_id"]
    assert not service.evaluate_readiness(db, pid, sid4)["ok"]
    inv = db.execute("SELECT rule_code FROM review_invalidations WHERE snapshot_id=?", (sid3,)).fetchall()
    assert any(r["rule_code"] == "CHARACTER_REPLACED" for r in inv)
    invalidated_character_pub = service.process_due_publication(
        db, queued_before_replace["publication"]["id"], Path(os.environ["PACKAGE_DIR"])
    )
    assert invalidated_character_pub["status"] == "invalidated"

# 审阅撤回：恢复审阅通过并排队导出，撤回后执行前复验必须失败。
with get_db() as db:
    review = service.add_review(db, sid4, {"reviewer_id": reviewer["id"], "scope": "whole", "status": "approved"})
    assert service.evaluate_readiness(db, pid, sid4)["ok"]
    export4, _ = service.enqueue_export(db, pid, sid4, request_key="e-withdraw")
    service.withdraw_review(db, review["id"])
    failed = service.process_due_export(db, export4["id"], Path(os.environ["PACKAGE_DIR"]))
    assert failed["status"] == "failed" and "REVALIDATION_FAILED" in failed["failure_reason"]

# 渠道过期：快照引用固定版本，执行前按有效期复验。
with get_db() as db:
    expired_cv = service.add_channel_version(db, channel["id"], {
        "contact": "010-EXPIRED", "valid_from": PAST, "valid_until": PAST,
        "verified_at": PAST, "verification_basis": "旧公告，已过期",
    })
    service.update_project(db, pid, {"channel_version_id": expired_cv["id"]})
    sid5 = service.get_project(db, pid)["current_snapshot_id"]
    service.add_review(db, sid5, {"reviewer_id": reviewer["id"], "scope": "whole", "status": "approved"})
    r5 = service.evaluate_readiness(db, pid, sid5)
    assert "HELP_CHANNEL_EXPIRED" in r5["blocking"]

    # 队列创建时渠道仍有效；模拟到 future_at 才执行，则必须因有效期失效。
    service.update_project(db, pid, {"channel_version_id": cv["id"]})
    valid_sid = service.get_project(db, pid)["current_snapshot_id"]
    service.add_review(db, valid_sid, {"reviewer_id": reviewer["id"], "scope": "whole", "status": "approved"})
    export5, _ = service.enqueue_export(db, pid, valid_sid, request_key="e-future-expiry")
    future_at = datetime.fromisoformat((TODAY + timedelta(days=31)).isoformat() + "T00:00:00+00:00")
    expired_later = service.process_due_export(db, export5["id"], Path(os.environ["PACKAGE_DIR"]), at=future_at)
    assert expired_later["status"] == "failed" and "HELP_CHANNEL_EXPIRED" in expired_later["failure_reason"]

    service.update_project(db, pid, {"channel_version_id": expired_cv["id"]})
    sid5 = service.get_project(db, pid)["current_snapshot_id"]
    service.add_review(db, sid5, {"reviewer_id": reviewer["id"], "scope": "whole", "status": "approved"})

# 整片批准不得复制到后来的新快照；段落级批准也不覆盖新语境。
with get_db() as db:
    dash = service.snapshot_status(db, pid)
    assert sid5 in str(dash)
    rules = {r["code"]: r for r in service.REVIEW_RULES}
    assert "CONTEXT_FINGERPRINT_CHANGED" in rules
    assert "SEGMENT_REORDER_CONTEXT" in rules

print("ALL ACCEPTANCE CHECKS PASSED")
PY_GUARD = True
