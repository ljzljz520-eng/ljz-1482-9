# 心理科普短片策划平台

一个可运行的 Web + SQLite + 后台工作器示例，用于管理心理科普短片的专业内容、适用人群、表述限制、固定专业来源、人工审阅、片段修订、脚本预览、素材清单、导出包与发布队列。

> **工具边界**：平台只组织一般心理科普内容，不做个体诊断，不生成自动治疗建议。求助渠道由维护者录入，并必须记录地区、有效期、核验依据。

## 运行

无需第三方 Python 依赖（标准库 + SQLite）：

```bash
python3 server.py
# 默认 http://127.0.0.1:8000
```

另开终端运行工作器：

```bash
python3 worker.py        # 持续轮询
python3 worker.py --once # 处理一次
```

环境变量：

- `PORT`：HTTP 端口，默认 `8000`
- `APP_DB`：SQLite 数据库路径，默认 `data/app.db`
- `PACKAGE_DIR`：导出包目录，默认 `data/packages`

验收测试：

```bash
# 终端 1
APP_DB=/tmp/psych-test/test.db PACKAGE_DIR=/tmp/psych-test/packages PORT=8011 python3 server.py
# 终端 2
APP_DB=/tmp/psych-test/test.db PACKAGE_DIR=/tmp/psych-test/packages python3 tests/acceptance.py
APP_DB=/tmp/psych-test/test.db PACKAGE_DIR=/tmp/psych-test/packages python3 worker.py --once
```

## 数据模型要点

- `sources` / `source_versions`：专业来源及不可变版本，保存版本号、出版日期、URL、归档文本与内容哈希。
- `reviewers`：审阅人；审阅人停用后不能新增批准。
- `help_channels` / `help_channel_versions`：求助渠道，记录地区、联系方式、有效期、核验依据、核验日期。
- `projects`：专业内容、适用人群、表述限制、固定来源版本、固定渠道版本和当前快照。
- `characters`：角色；角色替换会停用旧角色、迁移台词并产生新快照。
- `segments` / `segment_revisions`：字幕、旁白、台词、标题、备注，以及修订人和修订原因。
- `review_snapshots` / `snapshot_segments`：不可变审阅快照和当时每段内容、角色、顺序、上下文备注、内容哈希。
- `reviews`：整片或段落级批准/修改请求/撤回。
- `review_invalidations`：上下文变化、重排、角色替换、撤回等导致的失效审计事件。
- `vocabularies` / `vocabulary_terms` / `vocabulary_hits`：词表只产生复核线索。
- `jobs`：脚本预览与素材清单生成任务。
- `exports`：导出队列、包哈希、固定引用和失败原因；可撤销。
- `download_grants`：站内下载令牌，可过期、可撤销。
- `publications`：发布请求；按 `(project, snapshot, channel, request_key)` 对未完成/已发布请求幂等去重。

## 不可脱离语境的审阅规则

系统明确比较两种审核：

1. **段落级审核**：可批准某一段，但批准属于具体快照，并包含前后段落与整片语境。
2. **整片审核**：只覆盖当时的不可变快照，不自动复制到后续快照，也不是单段永久授权。

剪辑重排后的规则：

- 段落 `position`、相邻内容、适用人群、表述限制、角色、来源版本或渠道版本变化，都会产生新指纹。
- 新快照不得沿用旧快照的批准。
- 即使旧快照当时没有批准，也会记录上下文失效事件，避免“无批准可撤回”造成审计断档。
- 已排队导出/发布在工作器执行前重新校验；受众变更、渠道过期、角色替换或审阅撤回会令其失败或置为 `invalidated`。

## 词表策略

词表命中只作为人工复核线索：

- 命中会提示审阅人检查，不自动阻断或放行。
- **未命中不能证明内容安全**；每次 readiness 都返回该警示。
- 最终判断来自绑定快照的人工审阅。

## 同快照一致性

字幕、旁白、角色台词和素材清单均从同一个 `review_snapshot` 复制：

- 脚本预览结果带 `snapshot_id` 与 `fingerprint`。
- 素材清单为每段记录 `text_source_snapshot`。
- 已排队任务不允许绑定过期快照；编辑器变更后需重新生成快照和任务。
- 公开包只包含快照 ID、指纹、公共轨道文本、固定来源引用和渠道核验信息。
- `internal_comment`、审阅人姓名等内部审阅意见不会写入公开包。

## 导出、下载与发布

- 导出执行前重新执行 readiness，而不是相信排队瞬间的状态。
- 已导出成果引用固定 `source_version_id` 与内容哈希。
- 下载必须持有站内令牌；令牌可设置 TTL，可单独撤销。
- 维护者可撤销整个导出，撤销会同时停用其有效下载授权。
- 重复发布请求使用相同 `request_key` 返回既有请求或 `409 duplicate=true`，不会重复创建。
- 工作器在真正发布前再次复验；已排队但上下文改变的请求变为 `invalidated`。

## 主要 API

- `GET/POST /api/sources`，`POST /api/sources/{id}/versions`
- `GET/POST /api/reviewers`
- `GET/POST /api/channels`，`POST /api/channels/{id}/versions`
- `GET/POST /api/projects`，`PATCH /api/projects/{id}`
- `POST /api/projects/{id}/segments`，`PATCH /api/projects/{id}/segments/{sid}`
- `POST /api/projects/{id}/reorder`
- `POST /api/projects/{id}/characters`，`PATCH /api/projects/{id}/characters/{old}/replace`
- `POST /api/projects/{id}/snapshots`
- `POST /api/snapshots/{id}/reviews`，`PATCH /api/reviews/{id}/withdraw`
- `POST /api/projects/{id}/jobs/{script_preview|material_list}`
- `POST /api/projects/{id}/exports`，`PATCH /api/exports/{id}/revoke`
- `POST /api/exports/{id}/grant`，`PATCH /api/downloads/{token}/revoke`
- `GET /api/exports/{id}/download?token=...`
- `POST /api/projects/{id}/publish`
