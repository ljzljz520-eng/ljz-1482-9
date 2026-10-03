#!/usr/bin/env python3
import os
import sys
import time
from pathlib import Path

from app.db import ROOT, get_db, init_db
from app.service import (
    claim_next_export, claim_next_job, claim_next_publication, complete_job,
    generate_job_result, process_due_publication,
)

PACKAGE_DIR = Path(os.environ.get("PACKAGE_DIR", ROOT / "data" / "packages"))
WORKER_ID = os.environ.get("WORKER_ID", "worker-local")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "1"))


def run_once():
    did = False
    with get_db() as db:
        job = claim_next_job(db, WORKER_ID)
        if job:
            did = True
            try:
                result = generate_job_result(db, job)
                complete_job(db, job["id"], result)
                print(f"job {job['id']} completed: {job['job_type']}")
            except Exception as exc:  # keep queue failure inspectable
                complete_job(db, job["id"], None, str(exc))
                print(f"job {job['id']} failed: {exc}", file=sys.stderr)
        export = claim_next_export(db)
        if export:
            did = True
            from app.service import process_due_export
            processed = process_due_export(db, export["id"], PACKAGE_DIR)
            print(f"export {processed['id']} -> {processed['status']}")
        publication = claim_next_publication(db)
        if publication:
            did = True
            processed = process_due_publication(db, publication["id"], PACKAGE_DIR)
            print(f"publication {processed['id']} -> {processed['status']}")
    return did


def main():
    init_db()
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    once = "--once" in sys.argv
    while True:
        try:
            run_once()
        except Exception as exc:
            print(f"worker loop error: {exc}", file=sys.stderr)
        if once:
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
