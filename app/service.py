import json
import sqlite3
from datetime import datetime, timezone

from .util import canonical, is_expired, new_id, now, parse_json_array, sha256_json, sha256_text


REVIEW_RULES = [
    {
        "code": "CONTEXT_FINGERPRINT_CHANGED",
        "name": "上下文指纹变更即失效",
        "description": "审阅批准绑定不可变快照。剪辑重排、受众、角色、约束或固定来源/渠道变化都会产生新指纹，禁止沿用旧批准。",
    },
    {
        "code": "SEGMENT_REORDER_CONTEXT",
        "name": "段落重排不继承段落批准",
        "description": "段落级批准包含前后段与整片语境；position 或相邻内容变化后必须重新审阅。",
    },
    {
        "code": "WHOLE_APPROVAL_SNAPSHOT_SCOPED",
        "name": "整片批准只覆盖该快照",
        "description": "整片批准不可复制到后续快照，也不能作为脱离语境的段落永久授权。",
    },
    {
        "code": "REVIEW_WITHDRAWN",
        "name": "审阅撤回立即阻塞",
        "description": "审阅人撤回后，排队导出/发布在执行前复验失败；已生成下载授权可由维护者撤销。",
    },
]


def row_to_dict(row):
    return dict(row) if row is not None else None


def jsonable_snapshot(snap):
    d = row_to_dict(snap)
    if d:
        d["payload"] = json.loads(d.pop("payload_json"))
    return d


def get_source_version(db, source_version_id):
    return db.execute(
        """
        SELECT sv.*, s.title AS source_title, s.author, s.publisher
        FROM source_versions sv JOIN sources s ON s.id=sv.source_id
        WHERE sv.id=?
        """,
        (source_version_id,),
    ).fetchone()


def get_channel_version(db, channel_version_id):
    return db.execute(
        """
        SELECT cv.*, hc.name AS channel_name, hc.region
        FROM help_channel_versions cv JOIN help_channels hc ON hc.id=cv.channel_id
        WHERE cv.id=?
        """,
        (channel_version_id,),
    ).fetchone()


def create_source(db, data):
    sid = new_id("src")
    db.execute(
        "INSERT INTO sources(id,title,author,publisher,created_at) VALUES(?,?,?,?,?)",
        (sid, data["title"], data.get("author", ""), data.get("publisher", ""), now()),
    )
    version = add_source_version(db, sid, data)
    return db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone(), version


def add_source_version(db, source_id, data):
    source = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not source:
        raise ValueError("source not found")
    archived = data.get("archived_text", "")
    vid = new_id("sv")
    db.execute(
        """INSERT INTO source_versions
           (id,source_id,version,url,publication_date,content_hash,archived_text,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            vid,
            source_id,
            data["version"],
            data.get("url", ""),
            data.get("publication_date", ""),
            data.get("content_hash") or sha256_text(archived),
            archived,
            now(),
        ),
    )
    return get_source_version(db, vid)


def list_sources(db):
    sources = [row_to_dict(r) for r in db.execute("SELECT * FROM sources ORDER BY created_at DESC")]
    for source in sources:
        source["versions"] = [
            row_to_dict(r)
            for r in db.execute(
                "SELECT * FROM source_versions WHERE source_id=? ORDER BY created_at DESC",
                (source["id"],),
            )
        ]
    return sources


def create_reviewer(db, data):
    rid = new_id("rev")
    db.execute(
        "INSERT INTO reviewers(id,name,role,active,created_at) VALUES(?,?,?,?,?)",
        (rid, data["name"], data.get("role", "reviewer"), 1 if data.get("active", True) else 0, now()),
    )
    return db.execute("SELECT * FROM reviewers WHERE id=?", (rid,)).fetchone()


def list_reviewers(db):
    return [row_to_dict(r) for r in db.execute("SELECT * FROM reviewers ORDER BY created_at DESC")]


def create_channel(db, data):
    cid = new_id("ch")
    db.execute(
        "INSERT INTO help_channels(id,name,region,contact,created_at) VALUES(?,?,?,?,?)",
        (cid, data["name"], data["region"], data.get("contact", ""), now()),
    )
    version = add_channel_version(db, cid, data)
    return db.execute("SELECT * FROM help_channels WHERE id=?", (cid,)).fetchone(), version


def add_channel_version(db, channel_id, data):
    channel = db.execute("SELECT * FROM help_channels WHERE id=?", (channel_id,)).fetchone()
    if not channel:
        raise ValueError("channel not found")
    required = ["contact", "valid_from", "valid_until", "verification_basis", "verified_at"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError("missing channel version fields: " + ",".join(missing))
    last = db.execute(
        "SELECT COALESCE(MAX(version),0)+1 AS next_version FROM help_channel_versions WHERE channel_id=?",
        (channel_id,),
    ).fetchone()["next_version"]
    vid = new_id("cv")
    db.execute(
        """INSERT INTO help_channel_versions
           (id,channel_id,version,contact,valid_from,valid_until,verification_basis,verified_at,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            vid,
            channel_id,
            last,
            data["contact"],
            data["valid_from"],
            data["valid_until"],
            data["verification_basis"],
            data["verified_at"],
            now(),
        ),
    )
    return get_channel_version(db, vid)


def list_channels(db, at=None):
    channels = [row_to_dict(r) for r in db.execute("SELECT * FROM help_channels ORDER BY created_at DESC")]
    for channel in channels:
        versions = [
            row_to_dict(r)
            for r in db.execute(
                "SELECT * FROM help_channel_versions WHERE channel_id=? ORDER BY version DESC",
                (channel["id"],),
            )
        ]
        for v in versions:
            v["expired"] = is_expired(v["valid_until"], at)
        channel["versions"] = versions
        channel["current"] = versions[0] if versions else None
    return channels


def create_project(db, data):
    pid = new_id("prj")
    ts = now()
    constraints = data.get("expression_constraints", [])
    db.execute(
        """INSERT INTO projects
           (id,title,professional_content,intended_audience,expression_constraints,
            source_version_id,channel_version_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            pid,
            data["title"],
            data.get("professional_content", ""),
            data.get("intended_audience", ""),
            canonical(constraints if isinstance(constraints, list) else []),
            data.get("source_version_id"),
            data.get("channel_version_id"),
            ts,
            ts,
        ),
    )
    return get_project(db, pid)


def get_project(db, pid):
    project = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not project:
        return None
    d = row_to_dict(project)
    d["expression_constraints"] = parse_json_array(d["expression_constraints"])
    return d


def list_projects(db):
    projects = [get_project(db, r["id"]) for r in db.execute("SELECT id FROM projects ORDER BY created_at DESC")]
    for p in projects:
        p["status"] = snapshot_status(db, p["id"])
    return projects


def update_project(db, pid, data):
    p = get_project(db, pid)
    if not p:
        return None
    fields = {
        "title": data.get("title", p["title"]),
        "professional_content": data.get("professional_content", p["professional_content"]),
        "intended_audience": data.get("intended_audience", p["intended_audience"]),
        "source_version_id": data.get("source_version_id", p["source_version_id"]),
        "channel_version_id": data.get("channel_version_id", p["channel_version_id"]),
    }
    if "expression_constraints" in data:
        constraints = data["expression_constraints"]
        fields["expression_constraints"] = canonical(constraints if isinstance(constraints, list) else [])
    else:
        fields["expression_constraints"] = canonical(p["expression_constraints"])
    db.execute(
        """UPDATE projects SET title=?, professional_content=?, intended_audience=?, expression_constraints=?,
           source_version_id=?, channel_version_id=?, updated_at=? WHERE id=?""",
        (
            fields["title"],
            fields["professional_content"],
            fields["intended_audience"],
            fields["expression_constraints"],
            fields["source_version_id"],
            fields["channel_version_id"],
            now(),
            pid,
        ),
    )
    if p.get("current_snapshot_id"):
        create_snapshot(db, pid)
    return get_project(db, pid)


def create_character(db, pid, data):
    if not get_project(db, pid):
        return None
    cid = new_id("chr")
    db.execute(
        "INSERT INTO characters(id,project_id,name,role,replaced_by,active,created_at) VALUES(?,?,?,?,?,?,?)",
        (cid, pid, data["name"], data.get("role", ""), None, 1, now()),
    )
    return db.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()


def replace_character(db, pid, old_id, new_id):
    old = db.execute("SELECT * FROM characters WHERE id=? AND project_id=?", (old_id, pid)).fetchone()
    new = db.execute("SELECT * FROM characters WHERE id=? AND project_id=?", (new_id, pid)).fetchone()
    if not old or not new:
        raise ValueError("character not found")
    db.execute("UPDATE characters SET active=0, replaced_by=?, name=name WHERE id=?", (new_id, old_id))
    db.execute("UPDATE segments SET character_id=? WHERE project_id=? AND character_id=?", (new_id, pid, old_id))
    create_snapshot(db, pid, reason="CHARACTER_REPLACED")
    return old, new


def list_segments(db, pid):
    return [row_to_dict(r) for r in db.execute(
        """SELECT s.*, c.name AS character_name FROM segments s
           LEFT JOIN characters c ON c.id=s.character_id
           WHERE s.project_id=? ORDER BY s.position, s.created_at""",
        (pid,),
    )]


def create_segment(db, pid, data):
    if not get_project(db, pid):
        return None
    maxpos = db.execute("SELECT COALESCE(MAX(position),0) AS p FROM segments WHERE project_id=?", (pid,)).fetchone()["p"]
    sid = new_id("seg")
    ts = now()
    db.execute(
        """INSERT INTO segments(id,project_id,character_id,kind,position,text,context_note,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            sid,
            pid,
            data.get("character_id"),
            data["kind"],
            data.get("position", maxpos + 1),
            data.get("text", ""),
            data.get("context_note", ""),
            ts,
            ts,
        ),
    )
    return db.execute("SELECT * FROM segments WHERE id=?", (sid,)).fetchone()


def update_segment(db, pid, sid, data):
    seg = db.execute("SELECT * FROM segments WHERE id=? AND project_id=?", (sid, pid)).fetchone()
    if not seg:
        return None
    old_text = seg["text"]
    new_text = data.get("text", old_text)
    if old_text != new_text and data.get("reviewer_id"):
        db.execute(
            """INSERT INTO segment_revisions(id,segment_id,reviewer_id,old_text,new_text,reason,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (new_id("revseg"), sid, data["reviewer_id"], old_text, new_text, data.get("reason", ""), now()),
        )
    db.execute(
        """UPDATE segments SET character_id=?, kind=?, position=?, text=?, context_note=?, updated_at=?
           WHERE id=? AND project_id=?""",
        (
            data.get("character_id", seg["character_id"]),
            data.get("kind", seg["kind"]),
            data.get("position", seg["position"]),
            new_text,
            data.get("context_note", seg["context_note"]),
            now(),
            sid,
            pid,
        ),
    )
    return db.execute("SELECT * FROM segments WHERE id=?", (sid,)).fetchone()


def reorder_segments(db, pid, ordered_ids):
    existing = [r["id"] for r in db.execute("SELECT id FROM segments WHERE project_id=?", (pid,))]
    if sorted(existing) != sorted(ordered_ids):
        raise ValueError("ordered segment ids must contain exactly all project segments")
    for index, sid in enumerate(ordered_ids, start=1):
        db.execute("UPDATE segments SET position=?, updated_at=? WHERE id=?", (index, now(), sid))
    create_snapshot(db, pid)
    return list_segments(db, pid)


def list_revisions(db, pid):
    return [row_to_dict(r) for r in db.execute(
        """SELECT sr.*, seg.position, reviewer.name AS reviewer_name
           FROM segment_revisions sr
           JOIN segments seg ON seg.id=sr.segment_id
           LEFT JOIN reviewers reviewer ON reviewer.id=sr.reviewer_id
           WHERE seg.project_id=? ORDER BY sr.created_at DESC""",
        (pid,),
    )]


def get_snapshot(db, snapshot_id):
    snap = db.execute("SELECT * FROM review_snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if not snap:
        return None
    out = jsonable_snapshot(snap)
    out["segments"] = [row_to_dict(r) for r in db.execute(
        "SELECT * FROM snapshot_segments WHERE snapshot_id=? ORDER BY position", (snapshot_id,)
    )]
    out["vocabulary_hits"] = [row_to_dict(r) for r in db.execute(
        "SELECT * FROM vocabulary_hits WHERE snapshot_id=? ORDER BY created_at", (snapshot_id,)
    )]
    return out


def _snapshot_payload(db, project):
    segments = list_segments(db, project["id"])
    payload_segments = []
    for seg in segments:
        payload_segments.append({
            "segment_id": seg["id"],
            "position": seg["position"],
            "kind": seg["kind"],
            "character_id": seg["character_id"],
            "character_name": seg.get("character_name"),
            "text": seg["text"],
            "context_note": seg.get("context_note", ""),
            "content_hash": sha256_json({
                "text": seg["text"],
                "kind": seg["kind"],
                "character_id": seg["character_id"],
                "context_note": seg.get("context_note", ""),
            }),
        })
    return {
        "project": {
            "id": project["id"],
            "title": project["title"],
            "professional_content": project["professional_content"],
            "intended_audience": project["intended_audience"],
            "expression_constraints": project["expression_constraints"],
        },
        "source_version_id": project["source_version_id"],
        "channel_version_id": project["channel_version_id"],
        "segments": payload_segments,
    }


def _infer_reason(old, new):
    if not old:
        return "INITIAL_SNAPSHOT"
    op = old["payload"]
    np = new["payload"]
    if op["project"]["intended_audience"] != np["project"]["intended_audience"]:
        return "AUDIENCE_CHANGED"
    if op["project"]["expression_constraints"] != np["project"]["expression_constraints"]:
        return "CONSTRAINT_CHANGED"
    if op["source_version_id"] != np["source_version_id"]:
        return "SOURCE_VERSION_CHANGED"
    if op["channel_version_id"] != np["channel_version_id"]:
        return "CHANNEL_VERSION_CHANGED"
    old_s = {x["segment_id"]: x for x in op["segments"]}
    new_s = {x["segment_id"]: x for x in np["segments"]}
    if set(old_s) != set(new_s):
        return "SEGMENT_SET_CHANGED"
    if any(old_s[k]["position"] != new_s[k]["position"] for k in old_s):
        return "SEGMENT_REORDERED"
    if any(old_s[k]["character_id"] != new_s[k]["character_id"] for k in old_s):
        return "CHARACTER_REPLACED"
    if any(old_s[k]["content_hash"] != new_s[k]["content_hash"] for k in old_s):
        return "SEGMENT_EDITED"
    return "SNAPSHOT_CONTEXT_CHANGED"


def _invalidate_old_snapshot(db, old_snapshot_id, reason, detail=""):
    old_reviews = db.execute(
        "SELECT * FROM reviews WHERE snapshot_id=? AND status='approved'",
        (old_snapshot_id,),
    ).fetchall()
    if old_reviews:
        for review in old_reviews:
            db.execute(
                """INSERT INTO review_invalidations(id,review_id,snapshot_id,rule_code,reason,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (new_id("inv"), review["id"], old_snapshot_id, reason, detail or reason, now()),
            )
    else:
        # Preserve an auditable context-change event even when the old snapshot had no approval.
        db.execute(
            """INSERT INTO review_invalidations(id,review_id,snapshot_id,rule_code,reason,created_at)
               VALUES(?,?,?,?,?,?)""",
            (new_id("inv"), None, old_snapshot_id, reason, detail or reason, now()),
        )
    db.execute("UPDATE review_snapshots SET superseded_at=? WHERE id=?", (now(), old_snapshot_id))


def _record_vocabulary_hits(db, snapshot_id, payload):
    terms = db.execute("SELECT * FROM vocabulary_terms WHERE active=1").fetchall()
    for seg in payload["segments"]:
        haystack = (seg.get("text") or "").casefold()
        for term in terms:
            if term["term"].casefold() in haystack:
                db.execute(
                    """INSERT INTO vocabulary_hits(id,snapshot_id,segment_id,term,vocabulary_id,matched_text,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (new_id("hit"), snapshot_id, seg["segment_id"], term["term"], term["vocabulary_id"],
                     term["term"], now()),
                )


def create_snapshot(db, pid, reason=None):
    project = get_project(db, pid)
    if not project:
        return None
    payload = _snapshot_payload(db, project)
    fingerprint = sha256_json(payload)
    existing = db.execute(
        "SELECT * FROM review_snapshots WHERE project_id=? AND fingerprint=?",
        (pid, fingerprint),
    ).fetchone()
    if existing:
        snap = jsonable_snapshot(existing)
        db.execute("UPDATE projects SET current_snapshot_id=?, updated_at=? WHERE id=?", (snap["id"], now(), pid))
        snap["reused"] = True
        return snap
    old_id = project["current_snapshot_id"]
    if old_id:
        old = get_snapshot(db, old_id)
        inferred = reason or _infer_reason(old, {"payload": payload})
        _invalidate_old_snapshot(db, old_id, inferred)
    sid = new_id("snap")
    ts = now()
    db.execute(
        """INSERT INTO review_snapshots(id,project_id,fingerprint,source_version_id,channel_version_id,payload_json,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (sid, pid, fingerprint, project["source_version_id"], project["channel_version_id"],
         json.dumps(payload, ensure_ascii=False), ts),
    )
    for seg in payload["segments"]:
        db.execute(
            """INSERT INTO snapshot_segments
               (snapshot_id,segment_id,position,kind,character_id,text,context_note,content_hash)
               VALUES(?,?,?,?,?,?,?,?)""",
            (sid, seg["segment_id"], seg["position"], seg["kind"], seg["character_id"],
             seg["text"], seg["context_note"], seg["content_hash"]),
        )
    _record_vocabulary_hits(db, sid, payload)
    db.execute("UPDATE projects SET current_snapshot_id=? , updated_at=? WHERE id=?", (sid, now(), pid))
    return get_snapshot(db, sid)


def add_review(db, snapshot_id, data):
    snap = db.execute("SELECT * FROM review_snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if not snap:
        return None
    reviewer = db.execute("SELECT * FROM reviewers WHERE id=? AND active=1", (data["reviewer_id"],)).fetchone()
    if not reviewer:
        raise ValueError("active reviewer required")
    status = data.get("status", "approved")
    if status not in ("approved", "changes_requested"):
        raise ValueError("invalid review status")
    scope = data.get("scope", "whole")
    segment_id = data.get("segment_id")
    if scope == "segment":
        if not segment_id:
            raise ValueError("segment review requires segment_id")
        exists = db.execute(
            "SELECT 1 FROM snapshot_segments WHERE snapshot_id=? AND segment_id=?",
            (snapshot_id, segment_id),
        ).fetchone()
        if not exists:
            raise ValueError("segment is not part of snapshot")
    review_id = new_id("rvw")
    db.execute(
        """INSERT INTO reviews(id,snapshot_id,reviewer_id,scope,segment_id,status,internal_comment,
           context_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
        (review_id, snapshot_id, reviewer["id"], scope, segment_id, status,
         data.get("internal_comment", ""), snap["fingerprint"], now()),
    )
    return db.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()


def withdraw_review(db, review_id):
    review = db.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
    if not review:
        return None
    db.execute("UPDATE reviews SET status='withdrawn', withdrawn_at=? WHERE id=?", (now(), review_id))
    db.execute(
        """INSERT INTO review_invalidations(id,review_id,snapshot_id,rule_code,reason,created_at)
           VALUES(?,?,?,?,?,?)""",
        (new_id("inv"), review_id, review["snapshot_id"], "REVIEW_WITHDRAWN", "审阅人撤回批准", now()),
    )
    return db.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()


def list_snapshots(db, pid):
    rows = db.execute(
        "SELECT * FROM review_snapshots WHERE project_id=? ORDER BY created_at DESC", (pid,)
    ).fetchall()
    return [jsonable_snapshot(r) for r in rows]


def _latest_approved(db, snapshot_id, segment_id=None):
    if segment_id:
        whole = db.execute(
            """SELECT * FROM reviews WHERE snapshot_id=? AND scope='whole' AND status='approved'
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (snapshot_id,),
        ).fetchone()
        seg = db.execute(
            """SELECT * FROM reviews WHERE snapshot_id=? AND scope='segment' AND segment_id=? AND status='approved'
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (snapshot_id, segment_id),
        ).fetchone()
        return whole or seg
    return db.execute(
        """SELECT * FROM reviews WHERE snapshot_id=? AND scope='whole' AND status='approved'
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        (snapshot_id,),
    ).fetchone()


def _review_has_later_invalidation(db, review):
    return db.execute(
        "SELECT 1 FROM review_invalidations WHERE review_id=? AND created_at>=? LIMIT 1",
        (review["id"], review["created_at"]),
    ).fetchone() is not None


def evaluate_readiness(db, project_id=None, snapshot_id=None, at=None):
    if snapshot_id:
        snap_row = db.execute("SELECT * FROM review_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if not snap_row:
            return {"ok": False, "blocking": ["SNAPSHOT_NOT_FOUND"], "warnings": []}
        project_id = snap_row["project_id"]
    project = get_project(db, project_id)
    if not project:
        return {"ok": False, "blocking": ["PROJECT_NOT_FOUND"], "warnings": []}
    blocking, warnings, pending = [], [], []
    if not snapshot_id:
        snapshot_id = project["current_snapshot_id"]
    snap = get_snapshot(db, snapshot_id) if snapshot_id else None
    if not snap:
        blocking.append("NO_REVIEW_SNAPSHOT")
        return {"ok": False, "blocking": blocking, "warnings": warnings, "pending_segments": []}
    if project["current_snapshot_id"] != snapshot_id:
        blocking.append("STALE_SNAPSHOT_NOT_CURRENT")
    if not project["title"].strip():
        blocking.append("MISSING_TITLE")
    if len(project["professional_content"].strip()) < 20:
        blocking.append("MISSING_PROFESSIONAL_CONTENT")
    if not project["intended_audience"].strip():
        blocking.append("MISSING_INTENDED_AUDIENCE")
    if not project["source_version_id"]:
        blocking.append("MISSING_PINNED_SOURCE_VERSION")
    else:
        sv = get_source_version(db, project["source_version_id"])
        if not sv:
            blocking.append("SOURCE_VERSION_NOT_FOUND")
    if not project["channel_version_id"]:
        blocking.append("MISSING_HELP_CHANNEL")
    else:
        cv = get_channel_version(db, project["channel_version_id"])
        if not cv:
            blocking.append("CHANNEL_VERSION_NOT_FOUND")
        else:
            if not cv["verification_basis"].strip():
                blocking.append("CHANNEL_MISSING_VERIFICATION_BASIS")
            if is_expired(cv["valid_until"], at):
                blocking.append("HELP_CHANNEL_EXPIRED")
    segments = snap["segments"]
    if not segments:
        blocking.append("NO_SEGMENTS")
    kinds = {s["kind"] for s in segments}
    if "subtitle" not in kinds:
        blocking.append("MISSING_SUBTITLE_TRACK")
    if "narration" not in kinds:
        blocking.append("MISSING_NARRATION_TRACK")
    for seg in segments:
        if not seg["text"].strip():
            blocking.append(f"EMPTY_SEGMENT:{seg['segment_id']}")
        if seg["kind"] == "dialogue" and not seg["character_id"]:
            blocking.append(f"DIALOGUE_WITHOUT_CHARACTER:{seg['segment_id']}")
        approval = _latest_approved(db, snapshot_id, seg["segment_id"])
        if not approval:
            pending.append(seg["segment_id"])
        elif _review_has_later_invalidation(db, approval):
            pending.append(seg["segment_id"])
    if pending:
        blocking.append("SEGMENT_REVIEW_PENDING_OR_INVALID")
    hits = snap.get("vocabulary_hits", [])
    hit_segments = sorted({h["segment_id"] for h in hits})
    unreviewed_hits = [sid for sid in hit_segments if sid in set(pending)]
    if unreviewed_hits:
        warnings.append("VOCABULARY_HITS_REQUIRE_HUMAN_RECHECK")
    warnings.append("VOCABULARY_MATCH_IS_ONLY_A_REVIEW_CLUE_UNMATCHED_DOES_NOT_PROVE_SAFE")
    constraints = project.get("expression_constraints", [])
    if not constraints:
        warnings.append("NO_EXPRESSION_CONSTRAINTS_DECLARED")
    # 非治疗/诊断安全护栏：即使词表未命中，也由审阅快照承担最终判断。
    return {
        "ok": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "pending_segments": pending,
        "vocabulary_hit_count": len(hits),
        "snapshot_id": snapshot_id,
        "review_rules": REVIEW_RULES,
    }


def snapshot_status(db, pid):
    project = get_project(db, pid)
    readiness = evaluate_readiness(db, pid)
    missing = [x for x in readiness["blocking"] if x.startswith("MISSING_") or x in ("NO_SEGMENTS", "NO_REVIEW_SNAPSHOT")]
    return {
        "current_snapshot_id": project["current_snapshot_id"] if project else None,
        "readiness": readiness,
        "missing_information": missing,
        "pending_review_scope": readiness["pending_segments"],
    }


def create_vocabulary(db, data):
    vid = new_id("voc")
    db.execute("INSERT INTO vocabularies(id,name,description,created_at) VALUES(?,?,?,?)",
               (vid, data["name"], data.get("description", ""), now()))
    for term in data.get("terms", []):
        db.execute(
            "INSERT INTO vocabulary_terms(id,vocabulary_id,term,rationale,active) VALUES(?,?,?,?,1)",
            (new_id("term"), vid, term if isinstance(term, str) else term["term"],
             "" if isinstance(term, str) else term.get("rationale", "")),
        )
    return db.execute("SELECT * FROM vocabularies WHERE id=?", (vid,)).fetchone()


def enqueue_job(db, project_id, snapshot_id, job_type, request_key="default"):
    project = get_project(db, project_id)
    if not project:
        raise ValueError("project not found")
    snap = db.execute("SELECT * FROM review_snapshots WHERE id=? AND project_id=?", (snapshot_id, project_id)).fetchone()
    if not snap:
        raise ValueError("snapshot not found")
    if project["current_snapshot_id"] != snapshot_id:
        raise ValueError("snapshot is stale; recreate preview from current context")
    try:
        jid = new_id("job")
        db.execute(
            """INSERT INTO jobs(id,project_id,snapshot_id,job_type,status,request_key,created_at)
               VALUES(?,?,?,?, 'queued', ?,?)""",
            (jid, project_id, snapshot_id, job_type, request_key, now()),
        )
    except sqlite3.IntegrityError:
        row = db.execute(
            """SELECT * FROM jobs WHERE snapshot_id=? AND job_type=? AND request_key=?
               AND status IN ('queued','running','completed')""",
            (snapshot_id, job_type, request_key),
        ).fetchone()
        return row, True
    return db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone(), False


def claim_next_job(db, worker_id):
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
    if not row:
        db.rollback()
        return None
    db.execute(
        "UPDATE jobs SET status='running', started_at=?, claimed_by=? WHERE id=?",
        (now(), worker_id, row["id"]),
    )
    db.commit()
    return db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()


def generate_job_result(db, job):
    snap = get_snapshot(db, job["snapshot_id"])
    project = get_project(db, job["project_id"])
    if job["job_type"] == "script_preview":
        lines = []
        for seg in snap["segments"]:
            prefix = {"subtitle": "字幕", "narration": "旁白", "dialogue": "台词", "title": "标题", "note": "备注"}.get(seg["kind"], seg["kind"])
            lines.append(f"{seg['position']:02d}. [{prefix}] {seg['text']}")
        return {
            "disclaimer": "心理科普内容，不构成个体诊断或自动治疗建议。",
            "snapshot_id": snap["id"],
            "fingerprint": snap["fingerprint"],
            "audience": snap["payload"]["project"]["intended_audience"],
            "script": "\n".join(lines),
            "track_consistency": "subtitle/narration/dialogue all copied from this same immutable snapshot",
        }
    materials = []
    for seg in snap["segments"]:
        materials.append({
            "segment_id": seg["segment_id"],
            "position": seg["position"],
            "kind": seg["kind"],
            "required_asset": "caption-card" if seg["kind"] == "subtitle" else
                              "voiceover" if seg["kind"] == "narration" else "character-visual",
            "text_source_snapshot": snap["id"],
            "notes": "仅清单，不自动生成诊断或治疗建议",
        })
    return {
        "snapshot_id": snap["id"],
        "fingerprint": snap["fingerprint"],
        "materials": materials,
        "same_snapshot_required": True,
    }


def complete_job(db, job_id, result, error=None):
    if error:
        db.execute("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?", (error, now(), job_id))
    else:
        db.execute(
            "UPDATE jobs SET status='completed', result_json=?, finished_at=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), now(), job_id),
        )


def _package_public_data(db, snap, project):
    source = get_source_version(db, snap["source_version_id"]) if snap["source_version_id"] else None
    channel = get_channel_version(db, snap["channel_version_id"]) if snap["channel_version_id"] else None
    return {
        "package_type": "psych_science_short_video_public",
        "safety_notice": "本作品为一般心理科普，不提供个体诊断或自动治疗建议。",
        "project": {"id": project["id"], "title": project["title"], "intended_audience": project["intended_audience"]},
        "review_snapshot": {"id": snap["id"], "fingerprint": snap["fingerprint"], "created_at": snap["created_at"]},
        "source_reference": None if not source else {
            "source_version_id": source["id"],
            "title": source["source_title"],
            "version": source["version"],
            "url": source["url"],
            "publication_date": source["publication_date"],
            "content_hash": source["content_hash"],
        },
        "help_channel": None if not channel else {
            "region": channel["region"],
            "name": channel["channel_name"],
            "contact": channel["contact"],
            "valid_until": channel["valid_until"],
            "verification_basis": channel["verification_basis"],
            "verified_at": channel["verified_at"],
        },
        "segments": [
            {k: seg[k] for k in ("segment_id", "position", "kind", "character_id", "text")}
            for seg in snap["segments"]
        ],
    }


def enqueue_export(db, project_id, snapshot_id, request_key="default"):
    readiness = evaluate_readiness(db, project_id, snapshot_id)
    if not readiness["ok"]:
        raise PermissionError(json.dumps(readiness, ensure_ascii=False))
    existing = db.execute(
        """SELECT * FROM exports
           WHERE project_id=? AND snapshot_id=? AND request_key=?
             AND status IN ('queued','processing','ready')
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        (project_id, snapshot_id, request_key),
    ).fetchone()
    # 重复的已排队/处理中/已完成请求直接返回既有导出；失败任务允许重新排队。
    if existing:
        return existing, True
    eid = new_id("exp")
    db.execute(
        """INSERT INTO exports(id,project_id,snapshot_id,request_key,status,readiness_json,created_at)
           VALUES(?,?,?,?,'queued',?,?)""",
        (eid, project_id, snapshot_id, request_key, json.dumps(readiness, ensure_ascii=False), now()),
    )
    return db.execute("SELECT * FROM exports WHERE id=?", (eid,)).fetchone(), False


def process_due_export(db, export_id, package_dir, at=None):
    export = db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    if not export or export["status"] not in ("queued", "processing"):
        return export
    readiness = evaluate_readiness(db, export["project_id"], export["snapshot_id"], at=at)
    if not readiness["ok"]:
        db.execute(
            "UPDATE exports SET status='failed', failure_reason=?, readiness_json=? WHERE id=?",
            ("REVALIDATION_FAILED_BEFORE_PACKAGE: " + ",".join(readiness["blocking"]),
             json.dumps(readiness, ensure_ascii=False), export_id),
        )
        return db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    snap = get_snapshot(db, export["snapshot_id"])
    project = get_project(db, export["project_id"])
    data = _package_public_data(db, snap, project)
    encoded = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    digest = sha256_text(encoded)
    package_dir.mkdir(parents=True, exist_ok=True)
    path = package_dir / f"{export_id}.json"
    path.write_text(encoded, encoding="utf-8")
    db.execute(
        "UPDATE exports SET status='ready', package_hash=?, package_path=?, readiness_json=?, ready_at=? WHERE id=?",
        (digest, str(path), json.dumps(readiness, ensure_ascii=False), now(), export_id),
    )
    return db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()


def claim_next_export(db):
    row = db.execute("SELECT * FROM exports WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
    if row:
        db.execute("UPDATE exports SET status='processing' WHERE id=?", (row["id"],))
        row = db.execute("SELECT * FROM exports WHERE id=?", (row["id"],)).fetchone()
        db.commit()
    else:
        db.rollback()
    return row



def revoke_export(db, export_id):
    export = db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    if not export:
        return None
    db.execute(
        "UPDATE exports SET status='revoked', revoked_at=COALESCE(revoked_at,?) WHERE id=?",
        (now(), export_id),
    )
    db.execute(
        "UPDATE download_grants SET active=0, revoked_at=COALESCE(revoked_at,?) WHERE export_id=? AND active=1",
        (now(), export_id),
    )
    return db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()

def grant_download(db, export_id, granted_to, ttl_hours=None):
    export = db.execute("SELECT * FROM exports WHERE id=? AND status='ready'", (export_id,)).fetchone()
    if not export:
        raise ValueError("ready export required")
    existing = db.execute(
        "SELECT * FROM download_grants WHERE export_id=? AND granted_to=? AND active=1",
        (export_id, granted_to),
    ).fetchone()
    if existing:
        return existing, True
    expires = None
    if ttl_hours is not None:
        from datetime import timedelta
        expires = (datetime.now(timezone.utc) + timedelta(hours=int(ttl_hours))).isoformat(timespec="seconds")
    token = new_id("dl")
    db.execute(
        """INSERT INTO download_grants(token,export_id,granted_to,active,expires_at,created_at)
           VALUES(?,?,?,1,?,?)""",
        (token, export_id, granted_to, expires, now()),
    )
    return db.execute("SELECT * FROM download_grants WHERE token=?", (token,)).fetchone(), False


def revoke_download(db, token):
    db.execute(
        "UPDATE download_grants SET active=0, revoked_at=? WHERE token=?",
        (now(), token),
    )
    return db.execute("SELECT * FROM download_grants WHERE token=?", (token,)).fetchone()


def resolve_download(db, token):
    grant = db.execute("SELECT * FROM download_grants WHERE token=?", (token,)).fetchone()
    if not grant or not grant["active"]:
        return None, "download permission revoked or missing"
    if grant["expires_at"] and is_expired(grant["expires_at"]):
        return None, "download permission expired"
    export = db.execute("SELECT * FROM exports WHERE id=?", (grant["export_id"],)).fetchone()
    if export["status"] == "revoked":
        return None, "export revoked"
    if export["status"] != "ready":
        return None, "export not ready"
    return (grant, export), None


def enqueue_publication(db, project_id, snapshot_id, channel_name, request_key):
    export, reused_export = enqueue_export(db, project_id, snapshot_id, request_key)
    try:
        pid = new_id("pub")
        db.execute(
            """INSERT INTO publications(id,project_id,snapshot_id,export_id,channel_name,status,request_key,created_at)
               VALUES(?,?,?,?,?, 'queued', ?,?)""",
            (pid, project_id, snapshot_id, export["id"], channel_name, request_key, now()),
        )
        return {
            "publication": db.execute("SELECT * FROM publications WHERE id=?", (pid,)).fetchone(),
            "export": export,
            "reused_export": reused_export,
            "duplicate": False,
        }
    except sqlite3.IntegrityError:
        existing = db.execute(
            """SELECT * FROM publications WHERE project_id=? AND snapshot_id=? AND channel_name=? AND request_key=?
               AND status IN ('queued','published')""",
            (project_id, snapshot_id, channel_name, request_key),
        ).fetchone()
        return {"publication": existing, "export": export, "reused_export": reused_export, "duplicate": True}


def list_publications(db, project_id):
    return [row_to_dict(r) for r in db.execute(
        "SELECT * FROM publications WHERE project_id=? ORDER BY created_at DESC", (project_id,)
    )]


def claim_next_publication(db):
    # Status remains queued while process_due_publication performs the atomic revalidation.
    return db.execute(
        "SELECT * FROM publications WHERE status='queued' ORDER BY created_at,id LIMIT 1"
    ).fetchone()


def process_due_publication(db, publication_id, package_dir):
    """Re-check all gates immediately before publishing.

    A queued request never inherits an approval that was valid only at enqueue time.
    The same readiness rules cover audience changes, stale snapshots, channel expiry,
    character replacement and withdrawn reviews.
    """
    pub = db.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    if not pub or pub["status"] != "queued":
        return pub
    readiness = evaluate_readiness(db, pub["project_id"], pub["snapshot_id"])
    if not readiness["ok"]:
        result = {"outcome": "invalidated", "reason": "REVALIDATION_FAILED_BEFORE_PUBLICATION",
                  "readiness": readiness}
        db.execute(
            "UPDATE publications SET status='invalidated', result_json=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), publication_id),
        )
        return db.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    export = None
    if pub["export_id"]:
        export = db.execute("SELECT * FROM exports WHERE id=?", (pub["export_id"],)).fetchone()
    if not export or export["status"] in ("failed", "revoked"):
        new_export, _ = enqueue_export(db, pub["project_id"], pub["snapshot_id"], request_key=pub["request_key"])
        export = new_export
    if export["status"] in ("queued", "processing"):
        export = process_due_export(db, export["id"], package_dir)
    if export["status"] != "ready":
        result = {"outcome": "invalidated", "reason": "EXPORT_NOT_READY_AFTER_REVALIDATION",
                  "export_id": export["id"], "failure_reason": export["failure_reason"]}
        db.execute(
            "UPDATE publications SET status='invalidated', result_json=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), publication_id),
        )
        return db.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
    result = {"outcome": "published", "export_id": export["id"], "package_hash": export["package_hash"]}
    db.execute(
        "UPDATE publications SET status='published', export_id=?, result_json=?, published_at=? WHERE id=?",
        (export["id"], json.dumps(result, ensure_ascii=False), now(), publication_id),
    )
    return db.execute("SELECT * FROM publications WHERE id=?", (publication_id,)).fetchone()
