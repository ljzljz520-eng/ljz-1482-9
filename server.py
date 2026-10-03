#!/usr/bin/env python3
import json
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app import service
from app.db import ROOT, get_db, init_db

STATIC_DIR = ROOT / "static"
PACKAGE_DIR = Path(os.environ.get("PACKAGE_DIR", ROOT / "data" / "packages"))


class ApiError(Exception):
    def __init__(self, status, message, details=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details


class Handler(BaseHTTPRequestHandler):
    server_version = "PsychPlatform/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("HTTP_LOG"):
            super().log_message(fmt, *args)

    def _send(self, status, payload, extra_headers=None):
        # Mutation handlers must only expose success after their transaction is durable.
        if self.command in ("POST", "PATCH", "PUT", "DELETE") and getattr(self, "db", None) is not None:
            self.db.commit()
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


    def _rollback(self):
        db = getattr(self, "db", None)
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("object expected")
            return data
        except Exception as exc:
            raise ApiError(400, f"invalid JSON body: {exc}")

    def do_GET(self):
        try:
            self._route_get()
        except ApiError as exc:
            self._send(exc.status, {"error": exc.message, "details": exc.details})
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def do_POST(self):
        try:
            self._route_post()
        except PermissionError as exc:
            self._rollback()
            self._send(409, {"error": "not ready", "details": safe_json(exc)})
        except ApiError as exc:
            self._rollback()
            self._send(exc.status, {"error": exc.message, "details": exc.details})
        except Exception as exc:
            self._rollback()
            self._send(500, {"error": str(exc)})

    def do_PATCH(self):
        try:
            self._route_patch()
        except ApiError as exc:
            self._rollback()
            self._send(exc.status, {"error": exc.message, "details": exc.details})
        except Exception as exc:
            self._rollback()
            self._send(500, {"error": str(exc)})

    def do_DELETE(self):
        try:
            self._route_delete()
        except ApiError as exc:
            self._send(exc.status, {"error": exc.message, "details": exc.details})
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def _static(self, rel):
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            target = STATIC_DIR / "index.html"
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route_get(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        self.db = get_db()
        db = self.db
        try:
            if path == "/api/health":
                return self._send(200, {"ok": True, "tool_boundary": "education only; no diagnosis or automated therapy"})
            if path == "/api/rules":
                return self._send(200, {"review_rules": service.REVIEW_RULES})
            if path == "/api/projects":
                return self._send(200, service.list_projects(db))
            m = re.fullmatch(r"/api/projects/([^/]+)", path)
            if m:
                p = service.get_project(db, m.group(1))
                if not p:
                    raise ApiError(404, "project not found")
                return self._send(200, p)
            m = re.fullmatch(r"/api/projects/([^/]+)/dashboard", path)
            if m:
                p = service.get_project(db, m.group(1))
                if not p:
                    raise ApiError(404, "project not found")
                status = service.snapshot_status(db, p["id"])
                return self._send(200, {
                    "project": p,
                    "missing_information": status["missing_information"],
                    "pending_review_scope": status["pending_review_scope"],
                    "readiness": status["readiness"],
                    "characters": [service.row_to_dict(r) for r in db.execute(
                        "SELECT * FROM characters WHERE project_id=? ORDER BY created_at", (p["id"],)
                    )],
                    "segments": service.list_segments(db, p["id"]),
                    "revisions": service.list_revisions(db, p["id"]),
                    "snapshots": service.list_snapshots(db, p["id"]),
                    "publications": service.list_publications(db, p["id"]),
                    "internal_review_policy": "internal comments are stored for reviewers and never copied into public packages",
                })
            m = re.fullmatch(r"/api/projects/([^/]+)/segments", path)
            if m:
                return self._send(200, service.list_segments(db, m.group(1)))
            m = re.fullmatch(r"/api/projects/([^/]+)/revisions", path)
            if m:
                return self._send(200, service.list_revisions(db, m.group(1)))
            m = re.fullmatch(r"/api/projects/([^/]+)/snapshots", path)
            if m:
                return self._send(200, service.list_snapshots(db, m.group(1)))
            m = re.fullmatch(r"/api/snapshots/([^/]+)", path)
            if m:
                s = service.get_snapshot(db, m.group(1))
                if not s:
                    raise ApiError(404, "snapshot not found")
                reviews = [service.row_to_dict(r) for r in db.execute(
                    """SELECT r.*, reviewer.name AS reviewer_name FROM reviews r
                       JOIN reviewers reviewer ON reviewer.id=r.reviewer_id
                       WHERE r.snapshot_id=? ORDER BY r.created_at DESC""",
                    (m.group(1),),
                )]
                invalidations = [service.row_to_dict(r) for r in db.execute(
                    "SELECT * FROM review_invalidations WHERE snapshot_id=? ORDER BY created_at DESC",
                    (m.group(1),),
                )]
                return self._send(200, {"snapshot": s, "reviews": reviews, "invalidations": invalidations,
                                        "readiness": service.evaluate_readiness(db, snapshot_id=m.group(1))})
            m = re.fullmatch(r"/api/projects/([^/]+)/jobs", path)
            if m:
                rows = [service.row_to_dict(r) for r in db.execute(
                    "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC", (m.group(1),)
                )]
                for j in rows:
                    j["result"] = safe_json_loads(j.pop("result_json", None))
                return self._send(200, rows)
            m = re.fullmatch(r"/api/projects/([^/]+)/exports", path)
            if m:
                rows = [service.row_to_dict(r) for r in db.execute(
                    "SELECT * FROM exports WHERE project_id=? ORDER BY created_at DESC", (m.group(1),)
                )]
                for e in rows:
                    e["readiness"] = safe_json_loads(e.pop("readiness_json", "{}"))
                return self._send(200, rows)
            if path == "/api/sources":
                return self._send(200, service.list_sources(db))
            if path == "/api/reviewers":
                return self._send(200, service.list_reviewers(db))
            if path == "/api/channels":
                return self._send(200, service.list_channels(db))
            if path == "/api/vocabularies":
                return self._send(200, [service.row_to_dict(r) for r in db.execute("SELECT * FROM vocabularies")])
            m = re.fullmatch(r"/api/exports/([^/]+)/download", path)
            if m:
                token = qs.get("token", [""])[0]
                resolved, error = service.resolve_download(db, token)
                if error:
                    # Distinguish revoked (410) from unauthorized (403).
                    status = 410 if "revoked" in error else 403
                    raise ApiError(status, error)
                grant, export = resolved
                if export["id"] != m.group(1):
                    raise ApiError(403, "token does not grant this export")
                target = Path(export["package_path"])
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("X-Content-SHA256", export["package_hash"])
                self.send_header("Content-Disposition", f'attachment; filename="{export["id"]}.json"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        finally:
            db.commit()
            db.close()
            self.db = None

    def _route_post(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        data = self._read_json()
        self.db = get_db()
        db = self.db
        try:
            if path == "/api/sources":
                source, version = service.create_source(db, data)
                return self._send(201, {"source": service.row_to_dict(source), "version": service.row_to_dict(version)})
            m = re.fullmatch(r"/api/sources/([^/]+)/versions", path)
            if m:
                return self._send(201, service.row_to_dict(service.add_source_version(db, m.group(1), data)))
            if path == "/api/reviewers":
                return self._send(201, service.row_to_dict(service.create_reviewer(db, data)))
            if path == "/api/channels":
                channel, version = service.create_channel(db, data)
                return self._send(201, {"channel": service.row_to_dict(channel), "version": service.row_to_dict(version)})
            m = re.fullmatch(r"/api/channels/([^/]+)/versions", path)
            if m:
                return self._send(201, service.row_to_dict(service.add_channel_version(db, m.group(1), data)))
            if path == "/api/vocabularies":
                return self._send(201, service.row_to_dict(service.create_vocabulary(db, data)))
            if path == "/api/projects":
                return self._send(201, service.get_project(db, service.create_project(db, data)["id"]))
            m = re.fullmatch(r"/api/projects/([^/]+)/snapshots", path)
            if m:
                snap = service.create_snapshot(db, m.group(1), reason=data.get("reason"))
                if not snap:
                    raise ApiError(404, "project not found")
                return self._send(201, snap)
            m = re.fullmatch(r"/api/projects/([^/]+)/characters", path)
            if m:
                c = service.create_character(db, m.group(1), data)
                if not c:
                    raise ApiError(404, "project not found")
                return self._send(201, service.row_to_dict(c))
            m = re.fullmatch(r"/api/projects/([^/]+)/segments", path)
            if m:
                s = service.create_segment(db, m.group(1), data)
                if not s:
                    raise ApiError(404, "project not found")
                return self._send(201, service.row_to_dict(s))
            m = re.fullmatch(r"/api/projects/([^/]+)/reorder", path)
            if m:
                try:
                    segments = service.reorder_segments(db, m.group(1), data.get("segment_ids", []))
                except ValueError as exc:
                    raise ApiError(400, str(exc))
                return self._send(200, segments)
            m = re.fullmatch(r"/api/snapshots/([^/]+)/reviews", path)
            if m:
                try:
                    review = service.add_review(db, m.group(1), data)
                except ValueError as exc:
                    raise ApiError(400, str(exc))
                if not review:
                    raise ApiError(404, "snapshot not found")
                return self._send(201, service.row_to_dict(review))
            m = re.fullmatch(r"/api/projects/([^/]+)/jobs/([^/]+)", path)
            if m:
                try:
                    job, reused = service.enqueue_job(db, m.group(1), data["snapshot_id"], m.group(2),
                                                      data.get("request_key", "default"))
                except ValueError as exc:
                    raise ApiError(400, str(exc))
                return self._send(201 if not reused else 200, service.row_to_dict(job))
            m = re.fullmatch(r"/api/projects/([^/]+)/exports", path)
            if m:
                try:
                    export, reused = service.enqueue_export(db, m.group(1), data["snapshot_id"],
                                                            data.get("request_key", "default"))
                except ValueError as exc:
                    raise ApiError(400, str(exc))
                except PermissionError:
                    raise
                return self._send(201 if not reused else 200, service.row_to_dict(export))
            m = re.fullmatch(r"/api/projects/([^/]+)/publish", path)
            if m:
                try:
                    result = service.enqueue_publication(
                        db, m.group(1), data["snapshot_id"], data.get("channel_name", "default"),
                        data.get("request_key", "default"),
                    )
                except ValueError as exc:
                    raise ApiError(400, str(exc))
                result_serialized = {
                    k: service.row_to_dict(v) if hasattr(v, "keys") else v
                    for k, v in result.items()
                }
                status = 409 if result["duplicate"] else 201
                return self._send(status, result_serialized)
            m = re.fullmatch(r"/api/exports/([^/]+)/grant", path)
            if m:
                grant, reused = service.grant_download(db, m.group(1), data["granted_to"], data.get("ttl_hours"))
                return self._send(200 if reused else 201, service.row_to_dict(grant))
        finally:
            db.commit()
            db.close()
            self.db = None

    def _route_patch(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        data = self._read_json()
        self.db = get_db()
        db = self.db
        try:
            m = re.fullmatch(r"/api/projects/([^/]+)", path)
            if m:
                p = service.update_project(db, m.group(1), data)
                if not p:
                    raise ApiError(404, "project not found")
                return self._send(200, p)
            m = re.fullmatch(r"/api/projects/([^/]+)/segments/([^/]+)", path)
            if m:
                s = service.update_segment(db, m.group(1), m.group(2), data)
                if not s:
                    raise ApiError(404, "segment not found")
                return self._send(200, service.row_to_dict(s))
            m = re.fullmatch(r"/api/projects/([^/]+)/characters/([^/]+)/replace", path)
            if m:
                try:
                    old, new = service.replace_character(db, m.group(1), m.group(2), data["new_character_id"])
                except ValueError as exc:
                    raise ApiError(400, str(exc))
                return self._send(200, {"old": service.row_to_dict(old), "new": service.row_to_dict(new)})
            m = re.fullmatch(r"/api/reviews/([^/]+)/withdraw", path)
            if m:
                r = service.withdraw_review(db, m.group(1))
                if not r:
                    raise ApiError(404, "review not found")
                return self._send(200, service.row_to_dict(r))
            m = re.fullmatch(r"/api/exports/([^/]+)/revoke", path)
            if m:
                e = service.revoke_export(db, m.group(1))
                if not e:
                    raise ApiError(404, "export not found")
                return self._send(200, service.row_to_dict(e))
            m = re.fullmatch(r"/api/downloads/([^/]+)/revoke", path)
            if m:
                g = service.revoke_download(db, unquote(m.group(1)))
                return self._send(200, service.row_to_dict(g))
        finally:
            db.commit()
            db.close()
            self.db = None

    def _route_delete(self):
        raise ApiError(405, "DELETE is not enabled; use explicit revoke/withdraw transitions")


def safe_json(exc):
    try:
        return json.loads(str(exc))
    except Exception:
        return {"error": str(exc)}


def safe_json_loads(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def main():
    init_db()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"listening on http://0.0.0.0:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
