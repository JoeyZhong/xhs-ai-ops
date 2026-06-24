# 采集 Sidecar 契约（lead-intent-radar V1 · Phase 1）

> 采集层是**可替换的盒子**：主平台只认下面这个 HTTP 契约，背后换 MediaCrawler-CDP /
> ReaJason-xhs / 自建，主代码与 agent 零改动。对应「解耦 + 共享契约」原则。

## 选择采集器（env）

| env | 值 | 说明 |
|---|---|---|
| `XHS_COLLECTOR` | `fixture`（默认） | 离线，内置/JSON 样本，让流水线现在就能端到端跑 |
| | `sidecar` | HTTP 调真实采集 sidecar（见下契约） |
| `XHS_SIDECAR_URL` | `http://localhost:8800` | sidecar 基址 |
| `XHS_SIDECAR_TIMEOUT` | `30` | 秒 |

fixture 文件（可选，覆盖内置样本）：`config/xhs_signal_fixtures.json`，内容为 Signal 数组。

## HTTP 契约（sidecar 模式）

**请求** `POST {XHS_SIDECAR_URL}/collect`
```json
{ "source": "xhs", "keyword": "审计报告", "limit": 20 }
```

**响应 200**
```json
{ "items": [
  { "source_url": "https://www.xiaohongshu.com/explore/<note_id>",
    "author": "@昵称",
    "posted_at": "2026-06-17T12:00:00",
    "post_text": "原帖全文…" }
] }
```

字段归一在主平台侧完成（`collect_xhs_intent._normalize`），故 sidecar 只需尽量给
`source_url / author / posted_at / post_text`（缺失可空）。`signal_key` 主平台按
url 自动生成（去重键）。

sidecar 不可达 / 非 200 / 异常 → 主平台采集返回 `[]`（雷达降级，不崩溃）。

## ⚠️ License 闸门（决策 B）

- V1 = `fixture`（无外部依赖，本地验证整条流水线）。
- 接真实采集前：
  - **MediaCrawler 免费版禁止商用** —— 仅本地技术 spike，不得用于真实获客。
  - 上线真实获客前必须切到 **ReaJason/xhs（MIT，免费商用，自带 `comment_note` 写路径）**
    或购买 **MediaCrawlerPro**。
- sidecar 进程独立（建议配合 `xhs_utils/safe_run.py` 沙箱），崩溃不拖垮主平台。

## 写路径（V1 之后）

V1 只读 + 人工发。写路径（自动发评论）在 V1 之后，且与 License 切换同步：
读写同源（同一 CDP Chrome / 同一 ReaJason client）保持单指纹，详见 proposal §2.3。
