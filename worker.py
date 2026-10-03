#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立工作器：轮询任务表，生成脚本预览与素材清单（导出包）。"""
import time
from app import init_db, process_pending_jobs

if __name__ == "__main__":
    init_db()
    print("worker 已启动（轮询 jobs 表）")
    while True:
        n = process_pending_jobs()
        if n:
            print("已处理 %d 个任务" % n)
        time.sleep(1.0)
