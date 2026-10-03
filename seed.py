#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示数据：来源、审阅人、词表、求助渠道、示例项目。"""
import json
from app import init_db, connect, now_iso, compute_context

init_db()
conn = connect()

if conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"]:
    print("已有数据，跳过 seed")
    raise SystemExit

def ins(q, a):
    return conn.execute(q, a).lastrowid

# 审阅人
r1 = ins("INSERT INTO reviewers(name, role) VALUES(?,?)", ("陈审（临床心理）", "clinical"))
r2 = ins("INSERT INTO reviewers(name, role) VALUES(?,?)", ("林审（科普编辑）", "editor"))

# 来源与版本
s1 = ins("INSERT INTO sources(title, publisher, created_at) VALUES(?,?,?)",
         ("《心理健康素养十条》", "国家卫生健康委员会", now_iso()))
ins("INSERT INTO source_versions(source_id, version, content, created_at) VALUES(?,?,?,?)",
    (s1, 1, "心理健康是健康的重要组成部分；出现情绪问题应积极寻求专业帮助。", now_iso()))
s2 = ins("INSERT INTO sources(title, publisher, created_at) VALUES(?,?,?)",
         ("Depression (fact sheet)", "WHO", now_iso()))
ins("INSERT INTO source_versions(source_id, version, content, created_at) VALUES(?,?,?,?)",
    (s2, 3, "Depression is a common mental disorder; effective treatments exists.", now_iso()))

# 词表（命中仅为复核线索）
for t, c, n in [("自杀", "危机", "出现即需人工复核"),
                  ("自残", "危机", "出现即需人工复核"),
                  ("诊断", "表述", "避免面向个体的诊断性表述"),
                  ("治愈", "表述", "避免疗效承诺")]:
    ins("INSERT INTO glossary_terms(term, category, note) VALUES(?,?,?)", (t, c, n))

# 求助渠道（地区/有效期/核验依据）
ch1 = ins("""INSERT INTO help_channels(name, region, contact, valid_from, valid_until,
                                       verification_basis, maintainer, created_at)
             VALUES(?,?,?,?,?,?,?,?)""",
          ("全国心理援助热线", "中国大陆", "12356", "2026-01-01", "2026-12-31",
           "国家卫健委官网公示号码，维护者于 2026-09-20 电话核验", "维护者A", now_iso()))
ch2 = ins("""INSERT INTO help_channels(name, region, contact, valid_from, valid_until,
                                       verification_basis, maintainer, created_at)
             VALUES(?,?,?,?,?,?,?,?)""",
          ("北京心理危机研究与干预中心", "北京", "010-82951332", "2026-01-01", "2026-12-31",
           "机构官网公示，维护者于 2026-09-20 邮件核验", "维护者A", now_iso()))

# 示例项目
p = ins("INSERT INTO projects(title, audience, expression_limits, created_at) VALUES(?,?,?,?)",
        ("认识抑郁情绪（3分钟科普）", "18-35岁普通公众", "不作个体诊断；不承诺疗效；避免刺激性画面描述", now_iso()))
ins("INSERT INTO project_sources(project_id, source_id) VALUES(?,?)", (p, s1))
ins("INSERT INTO project_sources(project_id, source_id) VALUES(?,?)", (p, s2))
ins("INSERT INTO project_channels(project_id, channel_id) VALUES(?,?)", (p, ch1))

c1 = ins("INSERT INTO characters(project_id, name) VALUES(?,?)", (p, "小安"))
c2 = ins("INSERT INTO characters(project_id, name) VALUES(?,?)", (p, "小黎"))

segs = [
    ("subtitle", None, s1, 1, "抑郁情绪很常见，并不等于抑郁症。"),
    ("narration", None, s2, 3, "当低落情绪持续两周以上并影响生活时，建议寻求专业心理帮助。"),
    ("character_line", c1, s1, 1, "我最近总开心不起来，是不是病了？"),
    ("character_line", c2, s1, 1, "别急着给自己下结论，我们可以先了解一下什么是抑郁情绪。"),
]
for i, (k, ch, sid, sv, txt) in enumerate(segs):
    seg = ins("""INSERT INTO segments(project_id, position, kind, character_id, source_id, source_version,
                                     revision_no, text, updated_at)
                 VALUES(?,?,?,?,?,?,1,?,?)""", (p, i, k, ch, sid, sv, txt, now_iso()))
    ins("INSERT INTO segment_revisions(segment_id, revision_no, text, created_at) VALUES(?,?,?,?)",
        (seg, 1, txt, now_iso()))

conn.commit()
print("seed 完成：项目#%d，审阅人 #%d/#%d，来源 #%d/#%d，渠道 #%d/#%d" % (p, r1, r2, s1, s2, ch1, ch2))
conn.close()
