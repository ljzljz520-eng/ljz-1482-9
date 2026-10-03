# 心理科普短片策划平台

面向科普短片制作团队的策划与审阅协作平台。Web 端编辑专业内容、适用人群与表述限制；
后端以 SQLite 管理来源、审阅人与片段修订；后台工作器生成脚本预览与素材清单（导出包）。

> **边界声明**：本工具不作个体诊断，也不提供自动治疗建议。词表命中仅作为复核线索，
> **未命中不代表内容安全**。

## 运行

纯 Python 3 标准库实现，无第三方依赖。

```bash
python3 seed.py        # 可选：写入演示数据
python3 app.py         # 启动服务（含内嵌工作器线程） http://127.0.0.1:8000
# 或独立工作器： python3 worker.py
python3 -m unittest test_acceptance -v   # 验收测试（15 项）
```

## 核心规则设计

### 1. 同一审阅快照
字幕（subtitle）、旁白（narration）、角色台词（character_line）必须在**同一审阅快照**下完成审阅。
快照对「适用人群 + 表述限制 + 片段序列（含修订号/类型/角色）」计算语境哈希；
导出包中的脚本预览与素材清单只从该快照的固定内容生成。

### 2. 段落级审核 vs 整片审核
| | 段落级审核 | 整片审核 |
|---|---|---|
| 绑定对象 | 片段修订号 + 前后邻段（含邻段修订号） | 完整审阅快照哈希 |
| 失效触发 | 自身修改、**剪辑重排**、邻段修改、角色替换 | 任何片段变化、重排、适用人群/表述限制变更 |
| 用途 | 细粒度复核 | 整体语境把关 |

**导出要求两者同时有效。** 脱离语境的批准不得沿用：批准记录保存批准时的上下文签名，
语境变化后旧批准自动失效（不删除，标记为"已失效"供追溯）。

### 3. 失效规则（验收点）
| 事件 | 效果 |
|---|---|
| 适用人群/表述限制变更 | 整片批准失效；已排队/就绪导出失效 |
| 求助渠道过期 | 已排队/就绪导出失效；导出包只含当前有效渠道 |
| 角色替换 | 相关台词片段修订号提升 → 段落级与整片批准失效，需重审 |
| 审阅撤回 | 依赖该快照的已排队/就绪导出失效；已授权用户亦无法下载 |
| 剪辑重排 / 邻段修改 | 依赖语境的段落级批准失效，不得沿用 |
| 重复发布请求 | 按 (项目, 幂等键) 去重，返回既有发布记录 |

### 4. 词表：仅复核线索
词表命中只提示人工复核；接口与界面均声明"未命中不代表内容安全"，
未命中不减少任何待审范围，系统不提供"安全"标记。

### 5. 求助渠道
由维护者录入，必填：地区、有效期（valid_from/valid_until）、核验依据。
过期渠道计入"缺失信息"并触发导出失效。

### 6. 公开包边界与下载权限
- 导出包（脚本预览、素材清单、固定来源版本、有效渠道、公开审阅摘要、免责声明）
  **不含内部审阅意见**（comment_internal 永不序列化进包）。
- 已导出成果引用**固定来源版本**：来源后续出新版本不影响已导出包。
- 站内下载采用**可撤销授权**：按用户标识发放/撤销，撤销后立即拒绝下载。

### 7. 缺失信息与待审范围
项目概览页与 `GET /api/projects/<id>` 展示：缺失信息（受众/限制/来源/渠道/片段来源固定等）
与待审范围（哪些片段在当前语境下无有效批准、整片审核是否有效）。两者清零方可排队导出。

## 结构

```
app.py            后端（路由 + 领域规则 + 工作器 + 静态服务）
schema.sql        SQLite 模式（来源版本、审阅快照、修订、授权、幂等发布等）
worker.py         独立工作器（轮询 jobs 表）
seed.py           演示数据
static/           Web 前端（概览/片段/审阅/词表/渠道/导出 六个页签）
test_acceptance.py 验收测试（15 项，覆盖全部失效规则与公开包边界）
```

## 主要 API

```
GET/POST /api/projects            PATCH /api/projects/<id>（受众/限制变更触发失效）
GET/POST /api/sources             POST /api/sources/<id>/versions（不可变版本）
GET/POST /api/projects/<id>/segments   PATCH /api/segments/<id>（修改即升修订号）
POST /api/projects/<id>/reorder   剪辑重排 → 语境失效
POST /api/characters/<id>/replace 角色替换 → 台词重审
GET/POST /api/glossary            GET /api/projects/<id>/glossary（命中+免责声明）
GET/POST /api/help-channels       POST /api/projects/<id>/channels
POST /api/projects/<id>/snapshots 审阅快照
POST /api/reviews                 POST /api/reviews/<id>/withdraw
POST /api/projects/<id>/exports   GET /api/exports/<id>
POST /api/exports/<id>/grants     POST /api/exports/<id>/grants/<token>/revoke
GET  /api/exports/<id>/download?token=...
POST /api/projects/<id>/publish   幂等键去重
POST /api/worker/run              同步触发工作器（测试/演示用）
```
