#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收测试：覆盖平台核心规则。"""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

os.environ["APP_DB"] = tempfile.mktemp(suffix=".db", prefix="psyche_test_")

import app as appmod  # noqa: E402


def request(method, port, path, data=None):
    url = "http://127.0.0.1:%d%s" % (port, path)
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        appmod.init_db()
        cls.srv = appmod.create_server(0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    # ---- HTTP 助手 ----
    def get(self, p):
        return request("GET", self.port, p)

    def post(self, p, d=None):
        return request("POST", self.port, p, d if d is not None else {})

    def patch(self, p, d):
        return request("PATCH", self.port, p, d)

    # ---- 领域助手 ----
    def make_source(self, title="来源A"):
        st, r = self.post("/api/sources", {"title": title, "publisher": "权威出版方", "content": "专业内容 v1"})
        self.assertEqual(st, 201, r)
        return r["id"]

    def make_channel(self, valid_until="2099-12-31", name="心理援助热线"):
        st, r = self.post("/api/help-channels", {
            "name": name, "region": "全国", "contact": "12356",
            "valid_from": "2020-01-01", "valid_until": valid_until,
            "verification_basis": "官网公示，维护者电话核验", "maintainer": "维护者甲"})
        self.assertEqual(st, 201, r)
        return r["id"]

    def make_reviewer(self, name="审阅人"):
        st, r = self.post("/api/reviewers", {"name": name})
        self.assertEqual(st, 201)
        return r["id"]

    def make_project(self):
        st, r = self.post("/api/projects", {"title": "测试短片"})
        self.assertEqual(st, 201)
        pid = r["id"]
        sid = self.make_source()
        chid = self.make_channel()
        self.post("/api/projects/%d/sources" % pid, {"source_id": sid})
        self.post("/api/projects/%d/channels" % pid, {"channel_id": chid})
        st, r = self.post("/api/projects/%d/characters" % pid, {"name": "小安"})
        char1 = r["id"]
        st, r = self.patch("/api/projects/%d" % pid,
                           {"audience": "18-35岁公众", "expression_limits": "不作个体诊断"})
        self.assertTrue(r["context_changed"])
        segs = []
        for kind, char in (("subtitle", None), ("narration", None), ("character_line", char1)):
            st, r = self.post("/api/projects/%d/segments" % pid, {
                "kind": kind, "text": "%s 文本" % kind, "character_id": char,
                "source_id": sid, "source_version": 1})
            self.assertEqual(st, 201, r)
            segs.append(r["id"])
        return {"pid": pid, "source_id": sid, "channel_id": chid,
                "character_id": char1, "segments": segs}

    def approve_all(self, pid, segments):
        """创建快照并完成 段落级(每段) + 整片 审阅，返回 snapshot_id。"""
        st, r = self.post("/api/projects/%d/snapshots" % pid)
        self.assertEqual(st, 201, r)
        snap = r["id"]
        rid = self.make_reviewer()
        for sg in segments:
            st, r = self.post("/api/reviews", {
                "project_id": pid, "snapshot_id": snap, "reviewer_id": rid,
                "scope": "segment", "segment_id": sg, "decision": "approved",
                "comment_public": "段落OK", "comment_internal": "内部：措辞再斟酌"})
            self.assertEqual(st, 201, r)
        st, r = self.post("/api/reviews", {
            "project_id": pid, "snapshot_id": snap, "reviewer_id": rid,
            "scope": "whole", "decision": "approved",
            "comment_public": "整片OK", "comment_internal": "内部机密意见XYZ"})
        self.assertEqual(st, 201, r)
        return snap, rid

    def queue_export(self, pid):
        st, r = self.post("/api/projects/%d/exports" % pid)
        self.assertEqual(st, 201, r)
        return r["id"]

    def run_worker(self):
        st, r = self.post("/api/worker/run")
        self.assertEqual(st, 200)
        return r["processed"]

    def export_status(self, eid):
        st, r = self.get("/api/exports/%d" % eid)
        self.assertEqual(st, 200)
        return r

    def make_ready_export(self):
        """完整流程：项目+审阅+排队+工作器构建 → 就绪导出。"""
        ctx = self.make_project()
        snap, rid = self.approve_all(ctx["pid"], ctx["segments"])
        eid = self.queue_export(ctx["pid"])
        self.run_worker()
        e = self.export_status(eid)
        self.assertEqual(e["status"], "ready", e)
        ctx.update({"snapshot_id": snap, "reviewer_id": rid, "export_id": eid})
        return ctx


class TestMissingAndPending(Base):
    def test_missing_info_and_pending_scope_display(self):
        st, r = self.post("/api/projects", {"title": "空项目"})
        pid = r["id"]
        st, d = self.get("/api/projects/%d" % pid)
        fields = {m["field"] for m in d["missing_info"]}
        self.assertTrue({"audience", "expression_limits", "sources", "segments", "channels"} <= fields)
        self.assertTrue(d["pending_scope"]["whole_review_needed"])
        self.assertIn("comparison", d["review_status"])
        self.assertEqual(len(d["review_status"]["comparison"]), 3)

    def test_export_blocked_without_reviews(self):
        ctx = self.make_project()
        st, r = self.post("/api/projects/%d/exports" % ctx["pid"])
        self.assertEqual(st, 409)
        self.assertEqual(len(r["pending_scope"]["segments_pending"]), 3)
        self.assertTrue(r["pending_scope"]["whole_review_needed"])


class TestExportPackage(Base):
    def test_full_flow_public_package(self):
        ctx = self.make_ready_export()
        eid = ctx["export_id"]
        # 授权并下载
        st, _ = self.post("/api/exports/%d/grants" % eid, {"user_token": "editor-1"})
        self.assertEqual(st, 200)
        st, pkg = self.get("/api/exports/%d/download?token=editor-1" % eid)
        self.assertEqual(st, 200, pkg)
        # 脚本预览 + 素材清单
        self.assertEqual(len(pkg["script_preview"]), 3)
        self.assertTrue(pkg["asset_list"])
        kinds = [s["kind"] for s in pkg["script_preview"]]
        self.assertEqual(kinds, ["subtitle", "narration", "character_line"])
        # 同一审阅快照：所有片段修订号与快照一致，且审阅摘要均引用该快照
        self.assertTrue(all(s["revision_no"] == 1 for s in pkg["script_preview"]))
        self.assertTrue(all(r["snapshot_id"] == ctx["snapshot_id"] for r in pkg["review_summary"]))
        # 固定来源版本
        self.assertEqual(pkg["sources"][0]["version"], 1)
        # 求助渠道含地区/有效期/核验依据
        ch = pkg["help_channels"][0]
        self.assertIn("region", ch)
        self.assertIn("valid_until", ch)
        self.assertIn("verification_basis", ch)
        # 内部意见不进入公开包
        raw = json.dumps(pkg, ensure_ascii=False)
        self.assertNotIn("内部机密意见XYZ", raw)
        self.assertNotIn("comment_internal", raw)
        self.assertIn("整片OK", raw)
        # 免责声明
        self.assertTrue(any("不作个体诊断" in d for d in pkg["disclaimers"]))
        self.assertTrue(any("未命中不代表内容安全" in d for d in pkg["disclaimers"]))

    def test_fixed_source_version_after_source_update(self):
        ctx = self.make_ready_export()
        eid = ctx["export_id"]
        # 来源发布新版本 → 已导出成果仍引用固定旧版本，且导出不失效
        st, r = self.post("/api/sources/%d/versions" % ctx["source_id"], {"content": "专业内容 v2"})
        self.assertEqual(st, 201)
        self.assertEqual(self.export_status(eid)["status"], "ready")
        self.post("/api/exports/%d/grants" % eid, {"user_token": "t1"})
        st, pkg = self.get("/api/exports/%d/download?token=t1" % eid)
        self.assertEqual(pkg["sources"][0]["version"], 1)


class TestInvalidation(Base):
    def test_audience_change_invalidates_queued_export(self):
        ctx = self.make_project()
        self.approve_all(ctx["pid"], ctx["segments"])
        eid = self.queue_export(ctx["pid"])   # 导出已排队
        st, r = self.patch("/api/projects/%d" % ctx["pid"], {"audience": "初中生"})
        self.assertTrue(r["context_changed"])
        e = self.export_status(eid)
        self.assertEqual(e["status"], "invalidated")
        self.assertIn("适用人群", e["invalid_reason"])
        st, d = self.get("/api/projects/%d" % ctx["pid"])
        self.assertFalse(d["review_status"]["whole_film"]["approved"])
        self.assertTrue(d["pending_scope"]["whole_review_needed"])

    def test_reorder_invalidates_context_approvals(self):
        ctx = self.make_project()
        self.approve_all(ctx["pid"], ctx["segments"])
        eid = self.queue_export(ctx["pid"])
        segs = ctx["segments"]
        st, r = self.post("/api/projects/%d/reorder" % ctx["pid"],
                          {"segment_ids": [segs[2], segs[1], segs[0]]})
        self.assertEqual(st, 200)
        # 导出已排队 → 失效
        self.assertEqual(self.export_status(eid)["status"], "invalidated")
        # 所有段落级批准因上下文改变而失效（不得沿用脱离语境的批准）
        st, d = self.get("/api/projects/%d" % ctx["pid"])
        pend = d["pending_scope"]["segments_pending"]
        self.assertEqual(len(pend), 3)
        self.assertTrue(all("不得沿用" in p["reason"] for p in pend))
        self.assertFalse(d["review_status"]["whole_film"]["approved"])

    def test_neighbor_edit_invalidates_context(self):
        ctx = self.make_project()
        self.approve_all(ctx["pid"], ctx["segments"])
        # 只改第 1 段文本 → 第 2 段因邻段修订号变化而失效
        st, r = self.patch("/api/segments/%d" % ctx["segments"][0], {"text": "改写后的字幕"})
        self.assertEqual(st, 200)
        st, d = self.get("/api/projects/%d" % ctx["pid"])
        pend_ids = {p["segment_id"] for p in d["pending_scope"]["segments_pending"]}
        self.assertIn(ctx["segments"][0], pend_ids)  # 自身修订变化
        self.assertIn(ctx["segments"][1], pend_ids)  # 邻段上下文变化
        self.assertNotIn(ctx["segments"][2], pend_ids)  # 远端片段不受影响

    def test_channel_expiry_invalidates_export(self):
        ctx = self.make_ready_export()
        eid = ctx["export_id"]
        # 关联一个已过期渠道 → 就绪导出失效
        expired = self.make_channel(valid_until="2020-01-01", name="旧热线")
        st, _ = self.post("/api/projects/%d/channels" % ctx["pid"], {"channel_id": expired})
        self.assertEqual(st, 200)
        e = self.export_status(eid)
        self.assertEqual(e["status"], "invalidated")
        self.assertIn("渠道过期", e["invalid_reason"])
        st, d = self.get("/api/projects/%d" % ctx["pid"])
        self.assertTrue(any(m["field"] == "channel_expired" for m in d["missing_info"]))

    def test_character_replace_requires_rereview(self):
        ctx = self.make_project()
        self.approve_all(ctx["pid"], ctx["segments"])
        eid = self.queue_export(ctx["pid"])
        st, r = self.post("/api/characters/%d/replace" % ctx["character_id"], {"new_name": "小黎"})
        self.assertEqual(st, 200)
        self.assertEqual(r["affected_segments"], [ctx["segments"][2]])
        self.assertEqual(self.export_status(eid)["status"], "invalidated")
        st, d = self.get("/api/projects/%d" % ctx["pid"])
        pend_ids = {p["segment_id"] for p in d["pending_scope"]["segments_pending"]}
        self.assertIn(ctx["segments"][2], pend_ids)   # 角色台词需重审
        self.assertFalse(d["review_status"]["whole_film"]["approved"])

    def test_review_withdrawal_invalidates_export(self):
        ctx = self.make_ready_export()
        eid = ctx["export_id"]
        st, reviews = self.get("/api/projects/%d/reviews" % ctx["pid"])
        whole = [r for r in reviews if r["scope"] == "whole"][0]
        st, _ = self.post("/api/reviews/%d/withdraw" % whole["id"])
        self.assertEqual(st, 200)
        e = self.export_status(eid)
        self.assertEqual(e["status"], "invalidated")
        self.assertIn("撤回", e["invalid_reason"])
        # 已授权用户也无法再下载
        self.post("/api/exports/%d/grants" % eid, {"user_token": "t9"})
        st, r = self.get("/api/exports/%d/download?token=t9" % eid)
        self.assertEqual(st, 410)


class TestPublishAndDownload(Base):
    def test_duplicate_publish_requests(self):
        ctx = self.make_ready_export()
        pid, eid = ctx["pid"], ctx["export_id"]
        st, r1 = self.post("/api/projects/%d/publish" % pid, {"export_id": eid, "idempotency_key": "K1"})
        self.assertEqual(st, 201)
        self.assertFalse(r1["duplicate"])
        st, r2 = self.post("/api/projects/%d/publish" % pid, {"export_id": eid, "idempotency_key": "K1"})
        self.assertEqual(st, 200)
        self.assertTrue(r2["duplicate"])
        self.assertEqual(r1["publish_request"]["id"], r2["publish_request"]["id"])
        st, r3 = self.post("/api/projects/%d/publish" % pid, {"export_id": eid, "idempotency_key": "K2"})
        self.assertEqual(st, 201)
        st, lst = self.get("/api/projects/%d/exports" % pid)
        self.assertEqual(len(lst["publish_requests"]), 2)

    def test_publish_blocked_when_invalidated(self):
        ctx = self.make_project()
        self.approve_all(ctx["pid"], ctx["segments"])
        eid = self.queue_export(ctx["pid"])
        self.patch("/api/projects/%d" % ctx["pid"], {"audience": "老年人"})
        st, r = self.post("/api/projects/%d/publish" % ctx["pid"],
                          {"export_id": eid, "idempotency_key": "KX"})
        self.assertEqual(st, 410)

    def test_download_grant_revocable(self):
        ctx = self.make_ready_export()
        eid = ctx["export_id"]
        st, _ = self.get("/api/exports/%d/download?token=nobody" % eid)
        self.assertEqual(st, 403)                       # 无授权
        self.post("/api/exports/%d/grants" % eid, {"user_token": "user-a"})
        st, _ = self.get("/api/exports/%d/download?token=user-a" % eid)
        self.assertEqual(st, 200)                       # 已授权
        self.post("/api/exports/%d/grants/user-a/revoke" % eid)
        st, _ = self.get("/api/exports/%d/download?token=user-a" % eid)
        self.assertEqual(st, 403)                       # 撤销后拒绝


class TestGlossary(Base):
    def test_hits_are_clues_only(self):
        ctx = self.make_project()
        st, _ = self.post("/api/glossary", {"term": "抑郁", "category": "情绪", "note": "复核线索"})
        # 片段文本不含该词 → 先制造命中
        self.patch("/api/segments/%d" % ctx["segments"][0], {"text": "抑郁情绪很常见"})
        st, g = self.get("/api/projects/%d/glossary" % ctx["pid"])
        self.assertEqual(st, 200)
        self.assertEqual(len(g["hits"]), 1)
        self.assertIn("未命中不代表内容安全", g["disclaimer"])
        # 未命中 ≠ 安全：无任何"安全"标记，待审范围不因未命中而减少
        self.assertNotIn("safe", g)
        st, d = self.get("/api/projects/%d" % ctx["pid"])
        self.assertEqual(len(d["pending_scope"]["segments_pending"]), 3)


class TestSnapshotFreshness(Base):
    def test_stale_snapshot_rejected_for_review(self):
        ctx = self.make_project()
        st, r = self.post("/api/projects/%d/snapshots" % ctx["pid"])
        snap = r["id"]
        self.patch("/api/segments/%d" % ctx["segments"][0], {"text": "新文本"})
        rid = self.make_reviewer()
        st, r = self.post("/api/reviews", {
            "project_id": ctx["pid"], "snapshot_id": snap, "reviewer_id": rid,
            "scope": "whole", "decision": "approved"})
        self.assertEqual(st, 409)
        self.assertEqual(r.get("code"), "snapshot_stale")


if __name__ == "__main__":
    unittest.main(verbosity=2)
