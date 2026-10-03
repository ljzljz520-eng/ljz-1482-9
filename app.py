#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
心理科普短片策划平台 — 后端（纯标准库：http.server + sqlite3）

核心原则：
- 本工具不作个体诊断，也不提供自动治疗建议；
- 词表命中仅作为复核线索，未命中不代表内容安全；
- 字幕、旁白及角色台词须使用同一审阅快照；
- 脱离语境的批准不得沿用：剪辑重排、邻段修改、适用人群变更等均使既有批准失效。
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import traceback
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("APP_DB", os.path.join(BASE, "app.db"))
STATIC_DIR = os.path.join(BASE, "static")

DISCLAIMERS = [
    "本工具不作个体诊断，也不提供自动治疗建议；如有需要，请联系专业求助渠道。",
    "词表命中仅作为复核线索；未命中不代表内容安全。",
    "字幕、旁白及角色台词须使用同一审阅快照；脱离语境的批准不得沿用。",
]
SEGMENT_KINDS = ("subtitle", "narration", "character_line")
KIND_LABELS = {"subtitle": "字幕", "narration": "旁白", "character_line": "角色台词"}


# ---------------------------------------------------------------- 数据库

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with open(os.path.join(BASE, "schema.sql"), encoding="utf-8") as fh:
        schema = fh.read()
    conn = connect()
    conn.executescript(schema)
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today():
    return date.today().isoformat()


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- 领域逻辑

def get_project(conn, pid):
    r = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not r:
        raise ApiError(404, "项目不存在")
    return r


def ordered_segments(conn, pid):
    return conn.execute(
        "SELECT * FROM segments WHERE project_id=? ORDER BY position, id", (pid,)
    ).fetchall()


def character_map(conn, pid):
    return {c["id"]: dict(c) for c in
            conn.execute("SELECT * FROM characters WHERE project_id=?", (pid,)).fetchall()}


def compute_context(conn, pid):
    """当前语境：适用人群 + 表述限制 + 片段序列(含修订号/类型/角色)。任何变化都会改变哈希。"""
    proj = get_project(conn, pid)
    segs = ordered_segments(conn, pid)
    chars = character_map(conn, pid)
    structural = {
        "audience": proj["audience"] or "",
        "expression_limits": proj["expression_limits"] or "",
        "segments": [
            [s["id"], s["revision_no"], s["kind"], s["character_id"], i]
            for i, s in enumerate(segs)
        ],
    }
    h = hashlib.sha256(
        json.dumps(structural, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    full = {
        "audience": proj["audience"],
        "expression_limits": proj["expression_limits"],
        "segments": [
            {
                "segment_id": s["id"],
                "revision_no": s["revision_no"],
                "kind": s["kind"],
                "position": i,
                "character_id": s["character_id"],
                "character": chars.get(s["character_id"], {}).get("name") if s["character_id"] else None,
                "text": s["text"],
                "source_id": s["source_id"],
                "source_version": s["source_version"],
            }
            for i, s in enumerate(segs)
        ],
    }
    return h, full


def segment_approvals(conn, seg, prev, nxt):
    """段落级批准：绑定 片段修订号 + 前后邻段(含其修订号)。重排或邻段修改即失效。"""
    rs = conn.execute(
        "SELECT * FROM reviews WHERE scope='segment' AND segment_id=? AND status='active' AND decision='approved'",
        (seg["id"],),
    ).fetchall()
    valid, stale = [], []
    for r in rs:
        ok = (
            r["revision_no"] == seg["revision_no"]
            and r["context_prev"] == (prev["id"] if prev else None)
            and r["context_prev_rev"] == (prev["revision_no"] if prev else None)
            and r["context_next"] == (nxt["id"] if nxt else None)
            and r["context_next_rev"] == (nxt["revision_no"] if nxt else None)
        )
        (valid if ok else stale).append(dict(r))
    return valid, stale


def whole_approvals(conn, pid):
    """整片批准：绑定完整审阅快照哈希。适用人群/表述限制/片段任何变化即失效。"""
    h, _ = compute_context(conn, pid)
    rs = conn.execute(
        """SELECT r.*, s.context_hash AS snap_hash FROM reviews r
           JOIN snapshots s ON s.id = r.snapshot_id
           WHERE r.project_id=? AND r.scope='whole' AND r.status='active' AND r.decision='approved'""",
        (pid,),
    ).fetchall()
    valid = [dict(r) for r in rs if r["snap_hash"] == h]
    stale = [dict(r) for r in rs if r["snap_hash"] != h]
    return valid, stale, h


def project_channels(conn, pid):
    return conn.execute(
        """SELECT h.* FROM help_channels h
           JOIN project_channels pc ON pc.channel_id = h.id
           WHERE pc.project_id=? ORDER BY h.id""",
        (pid,),
    ).fetchall()


def expired_channels(conn, pid):
    return [c for c in project_channels(conn, pid) if c["valid_until"][:10] < today()]


def missing_info(conn, pid):
    """缺失信息：网页端展示，且导出前必须清零。"""
    proj = get_project(conn, pid)
    miss = []
    if not (proj["audience"] or "").strip():
        miss.append({"field": "audience", "message": "缺少适用人群"})
    if not (proj["expression_limits"] or "").strip():
        miss.append({"field": "expression_limits", "message": "缺少表述限制"})
    if not conn.execute("SELECT 1 FROM project_sources WHERE project_id=? LIMIT 1", (pid,)).fetchone():
        miss.append({"field": "sources", "message": "未关联专业来源"})
    segs = ordered_segments(conn, pid)
    if not segs:
        miss.append({"field": "segments", "message": "尚未添加任何片段"})
    for s in segs:
        if not s["source_id"] or not s["source_version"]:
            miss.append({"field": "segment_source", "segment_id": s["id"],
                         "message": "片段#%d 未固定来源版本" % s["id"]})
        if s["kind"] == "character_line" and not s["character_id"]:
            miss.append({"field": "segment_character", "segment_id": s["id"],
                         "message": "片段#%d 角色台词未指定角色" % s["id"]})
    chans = project_channels(conn, pid)
    if not chans:
        miss.append({"field": "channels", "message": "未关联求助渠道"})
    for c in chans:
        if c["valid_until"][:10] < today():
            miss.append({"field": "channel_expired", "channel_id": c["id"],
                         "message": "求助渠道「%s」已于 %s 过期" % (c["name"], c["valid_until"])})
        elif c["valid_from"][:10] > today():
            miss.append({"field": "channel_not_yet_valid", "channel_id": c["id"],
                         "message": "求助渠道「%s」%s 才生效" % (c["name"], c["valid_from"])})
    return miss


def pending_scope(conn, pid):
    """待审范围：哪些片段在当前语境下无有效段落级批准；整片审核是否有效。"""
    segs = ordered_segments(conn, pid)
    pend = []
    for i, s in enumerate(segs):
        prev = segs[i - 1] if i > 0 else None
        nxt = segs[i + 1] if i < len(segs) - 1 else None
        valid, stale = segment_approvals(conn, s, prev, nxt)
        if not valid:
            reason = "从未获批" if not stale else "批准已失效（内容或上下文已变化，不得沿用脱离语境的批准）"
            pend.append({"segment_id": s["id"], "kind": s["kind"],
                         "revision_no": s["revision_no"], "reason": reason})
    wvalid, wstale, h = whole_approvals(conn, pid)
    return {
        "segments_pending": pend,
        "whole_review_valid": bool(wvalid),
        "whole_review_needed": not wvalid,
        "whole_stale_reviews": len(wstale),
        "current_context_hash": h,
    }


def review_status(conn, pid):
    """段落级审核 与 整片审核 的比较视图。"""
    segs = ordered_segments(conn, pid)
    seg_list = []
    for i, s in enumerate(segs):
        prev = segs[i - 1] if i > 0 else None
        nxt = segs[i + 1] if i < len(segs) - 1 else None
        valid, stale = segment_approvals(conn, s, prev, nxt)
        seg_list.append({
            "segment_id": s["id"], "kind": s["kind"], "revision_no": s["revision_no"],
            "approved": bool(valid), "stale_approvals": len(stale),
            "context": {"prev": prev["id"] if prev else None, "next": nxt["id"] if nxt else None},
        })
    wvalid, wstale, h = whole_approvals(conn, pid)
    return {
        "segment_level": {
            "segments": seg_list,
            "approved": sum(1 for x in seg_list if x["approved"]),
            "total": len(seg_list),
        },
        "whole_film": {"approved": bool(wvalid), "stale_reviews": len(wstale),
                       "current_context_hash": h},
        "comparison": [
            "段落级审核：批准绑定片段修订号与相邻片段（含其修订号）；剪辑重排或邻段修改即失效。",
            "整片审核：批准绑定完整审阅快照（适用人群、表述限制、片段序列与修订）；任何变化即失效。",
            "导出要求段落级与整片审核同时有效；字幕、旁白、角色台词必须来自同一审阅快照。",
        ],
    }


def invalidate_exports(conn, pid, reason, snapshot_id=None):
    """使已排队/已就绪的导出失效（不得沿用脱离语境的批准）。"""
    q = "UPDATE exports SET status='invalidated', invalid_reason=?, updated_at=? WHERE project_id=? AND status IN ('queued','ready')"
    args = [reason, now_iso(), pid]
    if snapshot_id is not None:
        q += " AND snapshot_id=?"
        args.append(snapshot_id)
    conn.execute(q, args)


def refresh_time_based(conn, pid):
    """时间触发的失效：求助渠道过期 → 已排队/就绪导出失效。"""
    ex = expired_channels(conn, pid)
    if ex:
        names = "、".join(c["name"] for c in ex)
        invalidate_exports(conn, pid, "求助渠道过期：%s" % names)


def glossary_hits(conn, pid):
    """词表命中 —— 仅作为复核线索；未命中不代表内容安全。"""
    terms = conn.execute("SELECT * FROM glossary_terms ORDER BY id").fetchall()
    segs = ordered_segments(conn, pid)
    hits = []
    for s in segs:
        for t in terms:
            if t["term"] and t["term"] in s["text"]:
                hits.append({"segment_id": s["id"], "revision_no": s["revision_no"],
                             "term": t["term"], "category": t["category"], "note": t["note"]})
    return hits


# ---------------------------------------------------------------- 工作器：生成脚本预览与素材清单

def build_export_package(conn, export_id):
    exp = conn.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    snap = conn.execute("SELECT * FROM snapshots WHERE id=?", (exp["snapshot_id"],)).fetchone()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (exp["project_id"],)).fetchone()
    payload = json.loads(snap["payload"])

    script, assets, src_pins = [], [], {}
    for item in payload["segments"]:
        src = None
        if item["source_id"]:
            s = conn.execute("SELECT * FROM sources WHERE id=?", (item["source_id"],)).fetchone()
            src = {"id": s["id"], "title": s["title"], "publisher": s["publisher"],
                   "version": item["source_version"]}   # 固定来源版本
            src_pins[(s["id"], item["source_version"])] = src
        script.append({
            "position": item["position"], "kind": item["kind"],
            "kind_label": KIND_LABELS.get(item["kind"], item["kind"]),
            "character": item["character"], "text": item["text"],
            "revision_no": item["revision_no"], "source": src,
        })
        if item["kind"] == "subtitle":
            assets.append({"type": "subtitle_file", "segment_id": item["segment_id"],
                           "spec": "SRT 字幕文件，与审阅快照同版本", "revision_no": item["revision_no"]})
        elif item["kind"] == "narration":
            assets.append({"type": "voiceover_audio", "segment_id": item["segment_id"],
                           "spec": "旁白录音 48kHz WAV", "revision_no": item["revision_no"]})
        elif item["kind"] == "character_line":
            assets.append({"type": "character_voice", "character": item["character"],
                           "segment_id": item["segment_id"], "revision_no": item["revision_no"]})
            assets.append({"type": "character_avatar", "character": item["character"],
                           "segment_id": item["segment_id"]})

    channels = [
        {"name": c["name"], "region": c["region"], "contact": c["contact"],
         "valid_from": c["valid_from"], "valid_until": c["valid_until"],
         "verification_basis": c["verification_basis"]}
        for c in project_channels(conn, exp["project_id"])
        if c["valid_from"][:10] <= today() <= c["valid_until"][:10]
    ]
    # 公开审阅摘要：仅公开备注，内部意见不进入公开包
    revs = conn.execute(
        """SELECT r.*, v.name AS reviewer_name FROM reviews r
           JOIN reviewers v ON v.id = r.reviewer_id
           WHERE r.snapshot_id=? AND r.status='active' ORDER BY r.id""",
        (snap["id"],),
    ).fetchall()
    review_summary = [
        {"scope": r["scope"], "segment_id": r["segment_id"], "reviewer": r["reviewer_name"],
         "decision": r["decision"], "public_note": r["comment_public"],
         "snapshot_id": r["snapshot_id"], "created_at": r["created_at"]}
        for r in revs
    ]
    return {
        "format": "psyche-short-export/1",
        "generated_at": now_iso(),
        "project": {"id": proj["id"], "title": proj["title"],
                    "audience": payload["audience"],
                    "expression_limits": payload["expression_limits"]},
        "snapshot": {"id": snap["id"], "context_hash": snap["context_hash"]},
        "script_preview": script,
        "asset_list": assets,
        "sources": list(src_pins.values()),
        "help_channels": channels,
        "review_summary": review_summary,
        "disclaimers": DISCLAIMERS,
    }


def process_pending_jobs():
    conn = connect()
    processed = 0
    try:
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE status='pending' ORDER BY id LIMIT 50").fetchall()
        for j in jobs:
            try:
                payload = json.loads(j["payload"])
                if j["type"] == "build_export":
                    eid = payload["export_id"]
                    exp = conn.execute("SELECT * FROM exports WHERE id=?", (eid,)).fetchone()
                    if exp and exp["status"] == "queued":
                        refresh_time_based(conn, exp["project_id"])
                        exp = conn.execute("SELECT * FROM exports WHERE id=?", (eid,)).fetchone()
                    if exp and exp["status"] == "queued":
                        ps = pending_scope(conn, exp["project_id"])
                        snap = conn.execute("SELECT * FROM snapshots WHERE id=?",
                                            (exp["snapshot_id"],)).fetchone()
                        h, _ = compute_context(conn, exp["project_id"])
                        if ps["segments_pending"] or not ps["whole_review_valid"] or snap["context_hash"] != h:
                            conn.execute(
                                "UPDATE exports SET status='invalidated', invalid_reason=?, updated_at=? WHERE id=?",
                                ("构建时审阅状态已变化，导出失效", now_iso(), eid))
                        else:
                            pkg = build_export_package(conn, eid)
                            conn.execute(
                                "UPDATE exports SET status='ready', package_json=?, updated_at=? WHERE id=?",
                                (json.dumps(pkg, ensure_ascii=False), now_iso(), eid))
                conn.execute("UPDATE jobs SET status='done', processed_at=? WHERE id=?",
                             (now_iso(), j["id"]))
                processed += 1
            except Exception:
                conn.execute("UPDATE jobs SET status='failed', processed_at=? WHERE id=?",
                             (now_iso(), j["id"]))
                traceback.print_exc()
        conn.commit()
    finally:
        conn.close()
    return processed


def worker_loop(stop):
    while not stop.is_set():
        try:
            process_pending_jobs()
        except Exception:
            traceback.print_exc()
        stop.wait(1.0)


# ---------------------------------------------------------------- 路由

ROUTES = []


def route(method, pattern):
    def deco(fn):
        int_names = []

        def repl_int(m):
            int_names.append(m.group(1))
            return "(?P<%s>\\d+)" % m.group(1)

        rx = re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern)  # 不含冒号，不会误匹配 <int:x>
        rx = re.sub(r"<int:(\w+)>", repl_int, rx)
        ROUTES.append((method, re.compile("^" + rx + "$"), fn, set(int_names)))
        return fn
    return deco


def ok(data, status=200):
    return status, data


# ---- 元信息 ----

@route("GET", "/api/meta")
def meta(req):
    return ok({"disclaimers": DISCLAIMERS, "kinds": SEGMENT_KINDS, "kind_labels": KIND_LABELS})


# ---- 项目 ----

@route("GET", "/api/projects")
def list_projects(req):
    conn = connect()
    try:
        out = []
        for p in conn.execute("SELECT * FROM projects ORDER BY id").fetchall():
            refresh_time_based(conn, p["id"])
            ps = pending_scope(conn, p["id"])
            out.append({**dict(p),
                        "missing_count": len(missing_info(conn, p["id"])),
                        "pending_segments": len(ps["segments_pending"]),
                        "whole_review_valid": ps["whole_review_valid"]})
        conn.commit()
        return ok(out)
    finally:
        conn.close()


@route("POST", "/api/projects")
def create_project(req):
    title = (req.json.get("title") or "").strip()
    if not title:
        raise ApiError(400, "title 必填")
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO projects(title, audience, expression_limits, created_at) VALUES(?,?,?,?)",
            (title, req.json.get("audience"), req.json.get("expression_limits"), now_iso()))
        conn.commit()
        return ok({"id": cur.lastrowid}, 201)
    finally:
        conn.close()


@route("GET", "/api/projects/<int:pid>")
def project_detail(req, pid):
    conn = connect()
    try:
        refresh_time_based(conn, pid)
        proj = get_project(conn, pid)
        data = {
            "project": dict(proj),
            "missing_info": missing_info(conn, pid),
            "pending_scope": pending_scope(conn, pid),
            "review_status": review_status(conn, pid),
            "disclaimers": DISCLAIMERS,
        }
        conn.commit()
        return ok(data)
    finally:
        conn.close()


@route("PATCH", "/api/projects/<int:pid>")
def update_project(req, pid):
    conn = connect()
    try:
        proj = get_project(conn, pid)
        fields = {f: req.json[f] for f in ("title", "audience", "expression_limits", "status")
                  if f in req.json}
        context_changed = (
            ("audience" in fields and fields["audience"] != proj["audience"]) or
            ("expression_limits" in fields and fields["expression_limits"] != proj["expression_limits"])
        )
        if fields:
            sets = ", ".join("%s=?" % k for k in fields)
            conn.execute("UPDATE projects SET %s WHERE id=?" % sets, (*fields.values(), pid))
        if context_changed:
            # 适用人群/表述限制变更 → 快照失效，已排队/就绪导出失效
            invalidate_exports(conn, pid, "适用人群或表述限制已变更，审阅快照失效")
        conn.commit()
        return ok({"ok": True, "context_changed": context_changed})
    finally:
        conn.close()


# ---- 来源（含不可变版本） ----

@route("GET", "/api/sources")
def list_sources(req):
    conn = connect()
    try:
        out = []
        for s in conn.execute("SELECT * FROM sources ORDER BY id").fetchall():
            vers = rows(conn.execute(
                "SELECT version, content, created_at FROM source_versions WHERE source_id=? ORDER BY version",
                (s["id"],)))
            out.append({**dict(s), "versions": vers})
        return ok(out)
    finally:
        conn.close()


@route("POST", "/api/sources")
def create_source(req):
    title = (req.json.get("title") or "").strip()
    publisher = (req.json.get("publisher") or "").strip()
    content = (req.json.get("content") or "").strip()
    if not (title and publisher and content):
        raise ApiError(400, "title/publisher/content 必填")
    conn = connect()
    try:
        cur = conn.execute("INSERT INTO sources(title, publisher, created_at) VALUES(?,?,?)",
                           (title, publisher, now_iso()))
        sid = cur.lastrowid
        conn.execute("INSERT INTO source_versions(source_id, version, content, created_at) VALUES(?,?,?,?)",
                     (sid, 1, content, now_iso()))
        conn.commit()
        return ok({"id": sid, "version": 1}, 201)
    finally:
        conn.close()


@route("POST", "/api/sources/<int:sid>/versions")
def add_source_version(req, sid):
    content = (req.json.get("content") or "").strip()
    if not content:
        raise ApiError(400, "content 必填")
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM sources WHERE id=?", (sid,)).fetchone():
            raise ApiError(404, "来源不存在")
        v = conn.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM source_versions WHERE source_id=?",
                         (sid,)).fetchone()["v"]
        conn.execute("INSERT INTO source_versions(source_id, version, content, created_at) VALUES(?,?,?,?)",
                     (sid, v, content, now_iso()))
        conn.commit()
        return ok({"source_id": sid, "version": v}, 201)
    finally:
        conn.close()


@route("POST", "/api/projects/<int:pid>/sources")
def attach_source(req, pid):
    sid = req.json.get("source_id")
    conn = connect()
    try:
        get_project(conn, pid)
        if not conn.execute("SELECT 1 FROM sources WHERE id=?", (sid,)).fetchone():
            raise ApiError(404, "来源不存在")
        conn.execute("INSERT OR IGNORE INTO project_sources(project_id, source_id) VALUES(?,?)", (pid, sid))
        conn.commit()
        return ok({"ok": True})
    finally:
        conn.close()


# ---- 审阅人 ----

@route("GET", "/api/reviewers")
def list_reviewers(req):
    conn = connect()
    try:
        return ok(rows(conn.execute("SELECT * FROM reviewers ORDER BY id")))
    finally:
        conn.close()


@route("POST", "/api/reviewers")
def create_reviewer(req):
    name = (req.json.get("name") or "").strip()
    if not name:
        raise ApiError(400, "name 必填")
    conn = connect()
    try:
        cur = conn.execute("INSERT INTO reviewers(name, role) VALUES(?,?)",
                           (name, req.json.get("role", "reviewer")))
        conn.commit()
        return ok({"id": cur.lastrowid}, 201)
    finally:
        conn.close()


# ---- 角色 ----

@route("GET", "/api/projects/<int:pid>/characters")
def list_characters(req, pid):
    conn = connect()
    try:
        get_project(conn, pid)
        return ok(rows(conn.execute("SELECT * FROM characters WHERE project_id=? ORDER BY id", (pid,))))
    finally:
        conn.close()


@route("POST", "/api/projects/<int:pid>/characters")
def create_character(req, pid):
    name = (req.json.get("name") or "").strip()
    if not name:
        raise ApiError(400, "name 必填")
    conn = connect()
    try:
        get_project(conn, pid)
        cur = conn.execute("INSERT INTO characters(project_id, name) VALUES(?,?)", (pid, name))
        conn.commit()
        return ok({"id": cur.lastrowid}, 201)
    finally:
        conn.close()


@route("POST", "/api/characters/<int:cid>/replace")
def replace_character(req, cid):
    """角色替换：涉及台词的片段修订号提升 → 既有段落级批准与整片批准全部失效，需重审。"""
    new_name = (req.json.get("new_name") or "").strip()
    if not new_name:
        raise ApiError(400, "new_name 必填")
    conn = connect()
    try:
        old = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
        if not old:
            raise ApiError(404, "角色不存在")
        pid = old["project_id"]
        cur = conn.execute("INSERT INTO characters(project_id, name) VALUES(?,?)", (pid, new_name))
        new_id = cur.lastrowid
        conn.execute("UPDATE characters SET replaced_by=?, replaced_at=? WHERE id=?",
                     (new_id, now_iso(), cid))
        affected = conn.execute("SELECT * FROM segments WHERE character_id=?", (cid,)).fetchall()
        for s in affected:
            new_rev = s["revision_no"] + 1
            conn.execute("UPDATE segments SET character_id=?, revision_no=?, updated_at=? WHERE id=?",
                         (new_id, new_rev, now_iso(), s["id"]))
            conn.execute("INSERT INTO segment_revisions(segment_id, revision_no, text, created_at) VALUES(?,?,?,?)",
                         (s["id"], new_rev, s["text"], now_iso()))
        invalidate_exports(conn, pid, "角色替换：「%s」→「%s」，相关台词需重审" % (old["name"], new_name))
        conn.commit()
        return ok({"new_character_id": new_id, "affected_segments": [s["id"] for s in affected]})
    finally:
        conn.close()


# ---- 片段 ----

def segment_view(conn, pid):
    segs = ordered_segments(conn, pid)
    chars = character_map(conn, pid)
    srcs = {s["id"]: dict(s) for s in conn.execute("SELECT * FROM sources").fetchall()}
    out = []
    for i, s in enumerate(segs):
        prev = segs[i - 1] if i > 0 else None
        nxt = segs[i + 1] if i < len(segs) - 1 else None
        valid, stale = segment_approvals(conn, s, prev, nxt)
        src = srcs.get(s["source_id"])
        out.append({
            "id": s["id"], "position": s["position"], "kind": s["kind"],
            "kind_label": KIND_LABELS.get(s["kind"], s["kind"]),
            "text": s["text"], "revision_no": s["revision_no"],
            "character_id": s["character_id"],
            "character": chars.get(s["character_id"], {}).get("name") if s["character_id"] else None,
            "source_id": s["source_id"], "source_version": s["source_version"],
            "source_title": src["title"] if src else None,
            "approved": bool(valid), "stale_approvals": len(stale),
            "context": {"prev": prev["id"] if prev else None, "next": nxt["id"] if nxt else None},
        })
    return out


@route("GET", "/api/projects/<int:pid>/segments")
def list_segments(req, pid):
    conn = connect()
    try:
        get_project(conn, pid)
        return ok(segment_view(conn, pid))
    finally:
        conn.close()


@route("POST", "/api/projects/<int:pid>/segments")
def create_segment(req, pid):
    kind = req.json.get("kind")
    text = (req.json.get("text") or "").strip()
    if kind not in SEGMENT_KINDS:
        raise ApiError(400, "kind 须为 subtitle/narration/character_line")
    if not text:
        raise ApiError(400, "text 必填")
    conn = connect()
    try:
        get_project(conn, pid)
        pos = conn.execute("SELECT COALESCE(MAX(position),-1)+1 AS p FROM segments WHERE project_id=?",
                           (pid,)).fetchone()["p"]
        cur = conn.execute(
            """INSERT INTO segments(project_id, position, kind, character_id, source_id, source_version,
                                    revision_no, text, updated_at)
               VALUES(?,?,?,?,?,?,1,?,?)""",
            (pid, pos, kind, req.json.get("character_id"), req.json.get("source_id"),
             req.json.get("source_version"), text, now_iso()))
        sid = cur.lastrowid
        conn.execute("INSERT INTO segment_revisions(segment_id, revision_no, text, created_at) VALUES(?,?,?,?)",
                     (sid, 1, text, now_iso()))
        invalidate_exports(conn, pid, "新增片段，审阅快照失效")
        conn.commit()
        return ok({"id": sid, "position": pos}, 201)
    finally:
        conn.close()


@route("PATCH", "/api/segments/<int:sid>")
def update_segment(req, sid):
    """任何内容/归属修改都提升修订号 → 脱离语境的批准自动失效。"""
    conn = connect()
    try:
        s = conn.execute("SELECT * FROM segments WHERE id=?", (sid,)).fetchone()
        if not s:
            raise ApiError(404, "片段不存在")
        new_text = req.json.get("text", s["text"])
        new_char = req.json.get("character_id", s["character_id"])
        new_src = req.json.get("source_id", s["source_id"])
        new_srcv = req.json.get("source_version", s["source_version"])
        changed = (new_text != s["text"] or new_char != s["character_id"]
                   or new_src != s["source_id"] or new_srcv != s["source_version"])
        if changed:
            new_rev = s["revision_no"] + 1
            conn.execute(
                """UPDATE segments SET text=?, character_id=?, source_id=?, source_version=?,
                                       revision_no=?, updated_at=? WHERE id=?""",
                (new_text, new_char, new_src, new_srcv, new_rev, now_iso(), sid))
            conn.execute("INSERT INTO segment_revisions(segment_id, revision_no, text, created_at) VALUES(?,?,?,?)",
                         (sid, new_rev, new_text, now_iso()))
            invalidate_exports(conn, s["project_id"], "片段#%d 已修改（修订 %d），审阅快照失效" % (sid, new_rev))
        conn.commit()
        return ok({"ok": True, "revision_no": s["revision_no"] + (1 if changed else 0)})
    finally:
        conn.close()


@route("POST", "/api/projects/<int:pid>/reorder")
def reorder_segments(req, pid):
    """剪辑重排：上下文改变 → 所有依赖语境的批准失效。"""
    ids = req.json.get("segment_ids") or []
    conn = connect()
    try:
        get_project(conn, pid)
        current = [s["id"] for s in ordered_segments(conn, pid)]
        if sorted(ids) != sorted(current):
            raise ApiError(400, "segment_ids 必须是当前片段的全排列")
        for pos, sid in enumerate(ids):
            conn.execute("UPDATE segments SET position=? WHERE id=?", (pos, sid))
        invalidate_exports(conn, pid, "剪辑重排导致上下文改变，脱离语境的批准不得沿用")
        conn.commit()
        return ok({"ok": True})
    finally:
        conn.close()


# ---- 词表 ----

@route("GET", "/api/glossary")
def list_glossary(req):
    conn = connect()
    try:
        return ok(rows(conn.execute("SELECT * FROM glossary_terms ORDER BY id")))
    finally:
        conn.close()


@route("POST", "/api/glossary")
def create_term(req):
    term = (req.json.get("term") or "").strip()
    if not term:
        raise ApiError(400, "term 必填")
    conn = connect()
    try:
        try:
            cur = conn.execute("INSERT INTO glossary_terms(term, category, note) VALUES(?,?,?)",
                               (term, req.json.get("category", "general"), req.json.get("note")))
        except sqlite3.IntegrityError:
            raise ApiError(409, "词条已存在")
        conn.commit()
        return ok({"id": cur.lastrowid}, 201)
    finally:
        conn.close()


@route("GET", "/api/projects/<int:pid>/glossary")
def project_glossary(req, pid):
    conn = connect()
    try:
        get_project(conn, pid)
        return ok({
            "hits": glossary_hits(conn, pid),
            "disclaimer": "词表命中仅作为复核线索；未命中不代表内容安全，内容是否适当须以人工审阅为准。",
        })
    finally:
        conn.close()


# ---- 求助渠道 ----

def channel_view(c):
    d = dict(c)
    t = today()
    d["validity"] = "expired" if d["valid_until"][:10] < t else (
        "pending" if d["valid_from"][:10] > t else "valid")
    return d


@route("GET", "/api/help-channels")
def list_channels(req):
    conn = connect()
    try:
        return ok([channel_view(c) for c in
                   conn.execute("SELECT * FROM help_channels ORDER BY id").fetchall()])
    finally:
        conn.close()


@route("POST", "/api/help-channels")
def create_channel(req):
    need = ("name", "region", "contact", "valid_from", "valid_until", "verification_basis", "maintainer")
    missing = [k for k in need if not (req.json.get(k) or "").strip()]
    if missing:
        raise ApiError(400, "缺少字段: " + ",".join(missing))
    conn = connect()
    try:
        cur = conn.execute(
            """INSERT INTO help_channels(name, region, contact, valid_from, valid_until,
                                         verification_basis, maintainer, created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (req.json["name"], req.json["region"], req.json["contact"], req.json["valid_from"],
             req.json["valid_until"], req.json["verification_basis"], req.json["maintainer"], now_iso()))
        conn.commit()
        return ok({"id": cur.lastrowid}, 201)
    finally:
        conn.close()


@route("GET", "/api/projects/<int:pid>/channels")
def list_project_channels(req, pid):
    conn = connect()
    try:
        get_project(conn, pid)
        return ok([channel_view(c) for c in project_channels(conn, pid)])
    finally:
        conn.close()


@route("POST", "/api/projects/<int:pid>/channels")
def attach_channel(req, pid):
    cid = req.json.get("channel_id")
    conn = connect()
    try:
        get_project(conn, pid)
        if not conn.execute("SELECT 1 FROM help_channels WHERE id=?", (cid,)).fetchone():
            raise ApiError(404, "渠道不存在")
        conn.execute("INSERT OR IGNORE INTO project_channels(project_id, channel_id) VALUES(?,?)", (pid, cid))
        refresh_time_based(conn, pid)   # 若渠道已过期，立即使已排队/就绪导出失效
        conn.commit()
        return ok({"ok": True})
    finally:
        conn.close()


# ---- 审阅快照 ----

@route("POST", "/api/projects/<int:pid>/snapshots")
def create_snapshot(req, pid):
    conn = connect()
    try:
        get_project(conn, pid)
        h, payload = compute_context(conn, pid)
        cur = conn.execute("INSERT INTO snapshots(project_id, context_hash, payload, created_at) VALUES(?,?,?,?)",
                           (pid, h, json.dumps(payload, ensure_ascii=False), now_iso()))
        conn.commit()
        return ok({"id": cur.lastrowid, "context_hash": h}, 201)
    finally:
        conn.close()


@route("GET", "/api/projects/<int:pid>/snapshots")
def list_snapshots(req, pid):
    conn = connect()
    try:
        get_project(conn, pid)
        h, _ = compute_context(conn, pid)
        out = []
        for s in conn.execute("SELECT * FROM snapshots WHERE project_id=? ORDER BY id DESC", (pid,)).fetchall():
            out.append({"id": s["id"], "context_hash": s["context_hash"],
                        "created_at": s["created_at"], "current": s["context_hash"] == h})
        return ok(out)
    finally:
        conn.close()


# ---- 审阅 ----

@route("GET", "/api/projects/<int:pid>/reviews")
def list_reviews(req, pid):
    conn = connect()
    try:
        get_project(conn, pid)
        return ok(rows(conn.execute(
            """SELECT r.*, v.name AS reviewer_name FROM reviews r
               JOIN reviewers v ON v.id=r.reviewer_id
               WHERE r.project_id=? ORDER BY r.id""", (pid,))))
    finally:
        conn.close()


@route("POST", "/api/reviews")
def create_review(req):
    pid = req.json.get("project_id")
    snap_id = req.json.get("snapshot_id")
    scope = req.json.get("scope")
    decision = req.json.get("decision")
    if scope not in ("segment", "whole"):
        raise ApiError(400, "scope 须为 segment/whole")
    if decision not in ("approved", "rejected", "needs_changes"):
        raise ApiError(400, "decision 非法")
    conn = connect()
    try:
        get_project(conn, pid)
        snap = conn.execute("SELECT * FROM snapshots WHERE id=? AND project_id=?",
                            (snap_id, pid)).fetchone()
        if not snap:
            raise ApiError(404, "快照不存在")
        h, _ = compute_context(conn, pid)
        if snap["context_hash"] != h:
            raise ApiError(409, "快照已过期：项目语境已变化，请重新创建审阅快照",
                           code="snapshot_stale")
        rev = conn.execute("SELECT * FROM reviewers WHERE id=?", (req.json.get("reviewer_id"),)).fetchone()
        if not rev or not rev["active"]:
            raise ApiError(400, "审阅人无效")
        seg_id = rev_no = cp = cpr = cn = cnr = None
        if scope == "segment":
            seg_id = req.json.get("segment_id")
            segs = ordered_segments(conn, pid)
            idx = next((i for i, s in enumerate(segs) if s["id"] == seg_id), None)
            if idx is None:
                raise ApiError(400, "片段不属于该项目")
            s = segs[idx]
            prev = segs[idx - 1] if idx > 0 else None
            nxt = segs[idx + 1] if idx < len(segs) - 1 else None
            rev_no = s["revision_no"]
            cp, cpr = (prev["id"], prev["revision_no"]) if prev else (None, None)
            cn, cnr = (nxt["id"], nxt["revision_no"]) if nxt else (None, None)
        cur = conn.execute(
            """INSERT INTO reviews(project_id, snapshot_id, reviewer_id, scope, segment_id, revision_no,
                                   context_prev, context_prev_rev, context_next, context_next_rev,
                                   decision, comment_internal, comment_public, status, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)""",
            (pid, snap_id, rev["id"], scope, seg_id, rev_no, cp, cpr, cn, cnr,
             decision, req.json.get("comment_internal"), req.json.get("comment_public"), now_iso()))
        conn.commit()
        return ok({"id": cur.lastrowid}, 201)
    finally:
        conn.close()


@route("POST", "/api/reviews/<int:rid>/withdraw")
def withdraw_review(req, rid):
    """审阅撤回：依赖该快照的已排队/就绪导出立即失效。"""
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM reviews WHERE id=?", (rid,)).fetchone()
        if not r:
            raise ApiError(404, "审阅不存在")
        conn.execute("UPDATE reviews SET status='withdrawn', withdrawn_at=? WHERE id=?",
                     (now_iso(), rid))
        invalidate_exports(conn, r["project_id"], "审阅#%d 已撤回" % rid, snapshot_id=r["snapshot_id"])
        conn.commit()
        return ok({"ok": True})
    finally:
        conn.close()


# ---- 导出 / 下载授权 / 发布 ----

@route("POST", "/api/projects/<int:pid>/exports")
def queue_export(req, pid):
    conn = connect()
    try:
        get_project(conn, pid)
        refresh_time_based(conn, pid)
        miss = missing_info(conn, pid)
        ps = pending_scope(conn, pid)
        if miss or ps["segments_pending"] or not ps["whole_review_valid"]:
            raise ApiError(409, "存在缺失信息或待审范围，不能排队导出",
                           missing_info=miss, pending_scope=ps)
        snap = conn.execute("SELECT * FROM snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1",
                            (pid,)).fetchone()
        h, _ = compute_context(conn, pid)
        if not snap or snap["context_hash"] != h:
            raise ApiError(409, "请先创建当前语境的审阅快照并完成审阅", code="snapshot_stale")
        cur = conn.execute(
            "INSERT INTO exports(project_id, snapshot_id, status, created_at, updated_at) VALUES(?,?, 'queued', ?, ?)",
            (pid, snap["id"], now_iso(), now_iso()))
        eid = cur.lastrowid
        conn.execute("INSERT INTO jobs(type, payload, created_at) VALUES(?,?,?)",
                     ("build_export", json.dumps({"export_id": eid}), now_iso()))
        conn.commit()
        return ok({"id": eid, "status": "queued"}, 201)
    finally:
        conn.close()


def export_view(conn, e):
    d = dict(e)
    d["has_package"] = bool(d.get("package_json"))
    d.pop("package_json", None)
    d["grants"] = rows(conn.execute(
        "SELECT user_token, created_at, revoked_at FROM download_grants WHERE export_id=? ORDER BY id",
        (e["id"],)))
    return d


@route("GET", "/api/projects/<int:pid>/exports")
def list_exports(req, pid):
    conn = connect()
    try:
        get_project(conn, pid)
        refresh_time_based(conn, pid)
        exps = [export_view(conn, e) for e in conn.execute(
            "SELECT * FROM exports WHERE project_id=? ORDER BY id DESC", (pid,)).fetchall()]
        pubs = rows(conn.execute(
            "SELECT * FROM publish_requests WHERE project_id=? ORDER BY id DESC", (pid,)))
        conn.commit()
        return ok({"exports": exps, "publish_requests": pubs})
    finally:
        conn.close()


@route("GET", "/api/exports/<int:eid>")
def get_export(req, eid):
    conn = connect()
    try:
        e = conn.execute("SELECT * FROM exports WHERE id=?", (eid,)).fetchone()
        if not e:
            raise ApiError(404, "导出不存在")
        refresh_time_based(conn, e["project_id"])
        e = conn.execute("SELECT * FROM exports WHERE id=?", (eid,)).fetchone()
        d = export_view(conn, e)
        conn.commit()
        return ok(d)
    finally:
        conn.close()


@route("POST", "/api/exports/<int:eid>/grants")
def grant_download(req, eid):
    token = (req.json.get("user_token") or "").strip()
    if not token:
        raise ApiError(400, "user_token 必填")
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM exports WHERE id=?", (eid,)).fetchone():
            raise ApiError(404, "导出不存在")
        conn.execute("INSERT OR IGNORE INTO download_grants(export_id, user_token, created_at) VALUES(?,?,?)",
                     (eid, token, now_iso()))
        conn.execute("UPDATE download_grants SET revoked_at=NULL WHERE export_id=? AND user_token=?",
                     (eid, token))
        conn.commit()
        return ok({"ok": True})
    finally:
        conn.close()


@route("POST", "/api/exports/<int:eid>/grants/<token>/revoke")
def revoke_download(req, eid, token):
    """撤销站内下载权限。"""
    conn = connect()
    try:
        conn.execute("UPDATE download_grants SET revoked_at=? WHERE export_id=? AND user_token=?",
                     (now_iso(), eid, token))
        conn.commit()
        return ok({"ok": True})
    finally:
        conn.close()


@route("GET", "/api/exports/<int:eid>/download")
def download_export(req, eid):
    token = req.query.get("token", "")
    conn = connect()
    try:
        e = conn.execute("SELECT * FROM exports WHERE id=?", (eid,)).fetchone()
        if not e:
            raise ApiError(404, "导出不存在")
        refresh_time_based(conn, e["project_id"])
        e = conn.execute("SELECT * FROM exports WHERE id=?", (eid,)).fetchone()
        g = conn.execute(
            "SELECT * FROM download_grants WHERE export_id=? AND user_token=? AND revoked_at IS NULL",
            (eid, token)).fetchone()
        if not g:
            raise ApiError(403, "无有效下载授权（或已被撤销）")
        if e["status"] == "invalidated":
            raise ApiError(410, "导出已失效：%s" % (e["invalid_reason"] or ""))
        if e["status"] == "queued":
            raise ApiError(409, "导出仍在排队构建中")
        conn.commit()
        return ok(json.loads(e["package_json"]))
    finally:
        conn.close()


@route("POST", "/api/projects/<int:pid>/publish")
def publish(req, pid):
    """发布请求：按 (项目, 幂等键) 去重，重复请求返回既有记录。"""
    eid = req.json.get("export_id")
    key = (req.json.get("idempotency_key") or "").strip()
    if not key:
        raise ApiError(400, "idempotency_key 必填")
    conn = connect()
    try:
        get_project(conn, pid)
        refresh_time_based(conn, pid)
        e = conn.execute("SELECT * FROM exports WHERE id=? AND project_id=?", (eid, pid)).fetchone()
        if not e:
            raise ApiError(404, "导出不存在")
        dup = conn.execute(
            "SELECT * FROM publish_requests WHERE project_id=? AND idempotency_key=?",
            (pid, key)).fetchone()
        if dup:
            conn.commit()
            return ok({"duplicate": True, "publish_request": dict(dup)})
        if e["status"] == "invalidated":
            raise ApiError(410, "导出已失效：%s" % (e["invalid_reason"] or ""))
        if e["status"] == "queued":
            raise ApiError(409, "导出仍在排队构建中")
        cur = conn.execute(
            "INSERT INTO publish_requests(project_id, export_id, idempotency_key, status, created_at) VALUES(?,?,?,'accepted',?)",
            (pid, eid, key, now_iso()))
        conn.execute("UPDATE exports SET status='published', updated_at=? WHERE id=?",
                     (now_iso(), eid))
        conn.commit()
        return ok({"duplicate": False,
                   "publish_request": dict(conn.execute(
                       "SELECT * FROM publish_requests WHERE id=?", (cur.lastrowid,)).fetchone())}, 201)
    finally:
        conn.close()


# ---- 工作器 ----

@route("POST", "/api/worker/run")
def run_worker(req):
    return ok({"processed": process_pending_jobs()})


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        if method == "GET" and (path == "/" or path.startswith("/static/")):
            return self._static(path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {}
        req = type("Req", (), {"method": method, "path": path,
                               "query": query, "json": payload, "headers": self.headers})
        for m, rx, fn, ints in ROUTES:
            if m != method:
                continue
            match = rx.match(path)
            if not match:
                continue
            kwargs = {k: (int(v) if k in ints else v) for k, v in match.groupdict().items()}
            try:
                status, data = fn(req, **kwargs)
            except ApiError as e:
                status, data = e.status, {"error": e.message, **e.extra}
            except Exception as e:  # noqa
                traceback.print_exc()
                status, data = 500, {"error": "internal", "detail": str(e)}
            return self._json(status, data)
        self._json(404, {"error": "not found"})

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        if path == "/":
            fp = os.path.join(STATIC_DIR, "index.html")
        else:
            fp = os.path.normpath(os.path.join(STATIC_DIR, path[len("/static/"):]))
            if not fp.startswith(STATIC_DIR + os.sep):
                return self._json(403, {"error": "forbidden"})
        if not os.path.isfile(fp):
            return self._json(404, {"error": "not found"})
        mime = {".html": "text/html", ".css": "text/css",
                ".js": "application/javascript"}.get(os.path.splitext(fp)[1], "application/octet-stream")
        with open(fp, "rb") as fh:
            bodyb = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(bodyb)))
        self.end_headers()
        self.wfile.write(bodyb)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def log_message(self, *args):
        pass


def create_server(port=8000):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    init_db()
    stop = threading.Event()
    t = threading.Thread(target=worker_loop, args=(stop,), daemon=True)
    t.start()
    port = int(os.environ.get("PORT", "8000"))
    srv = create_server(port)
    print("心理科普短片策划平台  http://127.0.0.1:%d" % port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.server_close()


if __name__ == "__main__":
    main()
