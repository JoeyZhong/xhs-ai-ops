# Phase 3 P0 收尾设计 — 3.2.6 + 3.4.2

**Author**: Claude (handover doc)
**Date**: 2026-05-11
**Status**: Design locked, ready for implementation
**Scope**: 收尾 `agent-architecture-refactor` change 的两条 P0 待办：
- **3.2.6** AnalystAgent 性能数据 < 3 篇时跳过 playbook 写入 + 审计 `insufficient_data`
- **3.4.2** Mock LLM 端到端测试：Analyst → memory → Content prompt 数据流闭环

---

## 1. Spec 锚点

### 3.2.6 — `openspec/changes/agent-architecture-refactor/specs/feedback-loop/spec.md:134-137`

```
### Scenario: 性能数据不足
- WHEN 已发布笔记 < 3 篇
- THEN AnalystAgent 跳过 playbook 写入
- AND 审计记录 `insufficient_data`
```

### 3.4.2 — `openspec/changes/agent-architecture-refactor/tasks.md:114`

```
- 跑通 mock LLM 端到端：Analyst 分析 → 写 playbook → Content 启动读取 → prompt 含新 entry
```

---

## 2. 现状 gap（`agents/evaluators.py`）

| Line | 现状 | 问题 |
|---|---|---|
| 92 | `return all_posts, bool(all_posts)` | 阈值不对（>=1 就算 has_perf，spec 要求 >=3） |
| 137-152 | `has_perf=False` 分支仍要求 LLM 调 `write_playbook_entry`（line 151） | prompt 没禁止写入 |
| 173-176 | `run()` 无条件调 `_write_draft_entry` | 跳过逻辑缺失 |
| 全文 | 无 audit_logger 实例（仅 read audit, line 40） | `insufficient_data` 无写入路径 |

---

## 3. 3.2.6 设计

### D1. 常量化阈值

`agents/evaluators.py` 顶部新增：

```python
MIN_PERF_POSTS = 3   # spec: feedback-loop "性能数据不足"
```

理由：单一 source of truth，未来可改为 settings 字段而不破坏现有调用方。

### D2. `_read_performance()` 语义收紧

```python
def _read_performance(self) -> tuple[list[dict], bool]:
    """Returns (posts, has_enough). has_enough = len(posts) >= MIN_PERF_POSTS."""
    # ... 现有 reading 逻辑保持不变 (line 76-91) ...
    return all_posts, len(all_posts) >= MIN_PERF_POSTS
```

变量重命名 `has_perf` → `has_enough` 共 3 处：
- `_read_performance` 返回名
- `assemble_prompt` line 108 解构
- `assemble_prompt` line 116 分支判断

### D3. prompt 分支重写（关键：低数据时显式禁止调工具）

`assemble_prompt()` line 137-153 替换为：

```python
else:
    # 数据不足 → audit-only fallback，禁止写 playbook
    prompt = (
        f"## 任务：本周运营回顾（{date_range}）\n\n"
        f"⚠️ 本周已发布笔记 < {MIN_PERF_POSTS} 篇，性能数据不足。\n"
        "**禁止调用 memory__write_playbook_entry 工具**。\n"
        "仅在最终回复中总结审计活动 + 提示用户补数据，不要尝试沉淀洞察。\n\n"
        "### 审计摘要\n"
        f"{audit_summary}\n\n"
        "### 现有 playbook（仅供参考，不要修改）\n"
        f"{playbook}\n\n"
        "### 要求\n"
        "1. 总结本周 Agent 活动概况（≤200 字）\n"
        "2. 提示用户补充至少 3 篇 performance 数据以便下周复盘"
    )
    confidence = "low"
```

**为什么必须显式禁止**：Analyst system prompt（`agents/analyst.py` 内）含「分析后调用 write_playbook_entry 沉淀洞察」全局指令；若此处 prompt 不显式覆盖，LLM 仍会尝试调工具，可能写入空建议或误判。

### D4. `run()` 跳过 + 审计

`run()` line 159-178 替换为：

```python
def run(self) -> dict:
    """Execute one evaluation cycle.

    Returns dict with keys: ok, entry_id, confidence, playbook_written, error (optional).
    """
    _, has_enough = self._read_performance()
    prompt, confidence = self.assemble_prompt()
    date_str = datetime.now().strftime("%Y-%m-%d")
    entry_id = f"weekly-{date_str}"

    result = self._master.submit(AgentTask(
        type="analyst", prompt=prompt, max_iterations=15,
        tenant_id=self._tenant_id,
    ))

    if result.ok:
        if has_enough:
            self._write_draft_entry(entry_id, result.content, confidence)
        else:
            self._audit_insufficient_data(entry_id, posts_count=len(self._read_performance()[0]))
        self._write_report(entry_id, result.content)
        return {
            "ok": True,
            "entry_id": entry_id,
            "confidence": confidence,
            "playbook_written": has_enough,
        }

    return {"ok": False, "error": result.error or "unknown", "entry_id": entry_id}
```

注意：
- 报告（`_write_report`）始终写，无论 has_enough — 用户始终能看到本周回顾
- 返回字典新增 `playbook_written` 字段，供调用方/测试断言
- `_read_performance()` 调用了两次（一次为 has_enough，一次为 posts_count）— 不优化（每次 IO 成本可忽略；保持代码可读）

### D5. 审计辅助方法

`run()` 之后新增：

```python
def _audit_insufficient_data(self, entry_id: str, posts_count: int) -> None:
    """Record insufficient_data audit event when playbook write is skipped."""
    from agents.audit import make_logger  # noqa: PLC0415

    logger = make_logger(
        self._master._storage,
        tenant_id=self._tenant_id,
        task_id=f"evaluator_{entry_id}",
    )
    logger.write({
        "kind": "insufficient_data",
        "evaluator": "AnalystEvaluator",
        "entry_id": entry_id,
        "posts_count": posts_count,
        "min_required": MIN_PERF_POSTS,
    })
```

复用现有 `agents/audit.py::make_logger` + `AuditLogger.write()`，落到 `xhs_data/audit/audit_YYYYMMDD.jsonl`。

### Edge case 表

| 笔记数 | has_enough | playbook_written | _write_draft_entry | _audit_insufficient_data | _write_report |
|---|---|---|---|---|---|
| 0 | False | False | skip | called | called |
| 1 | False | False | skip | called | called |
| 2 | False | False | skip | called | called |
| 3 | True | True | called | skip | called |
| 20+ | True | True | called | skip | called |

---

## 4. 3.4.2 设计 — 方案 B（mock `Master.submit`）

### 选型理由

| 方案 | 边界 | 选用 |
|---|---|---|
| A. 完整 LLM mock | `SequenceMockProvider` 串响应 + tool_call 序列 | ✗ 复杂、维护贵；LLM→tool 已在 `verify_phase5_p0/p1` 验证 |
| **B. mock `HermesMaster.submit`** | **跳过 LLM，保留 evaluator → memory → content 数据流** | **✓ 已选** |
| C. 直接调 memory_tools | 完全跳过 evaluator | ✗ 不测 evaluator 端 |

**方案 B 验证目标**：spec 要求的"数据流闭环"——evaluator 写出来的 entry 是否真的能被 Content prompt 读到。LLM→tool 链路属另一层，不在本测范围。

### 测试文件位置

`tests/test_phase3_e2e.py`（pytest 风格，与 `tests/test_f1_content_api.py` 对齐）

### 测试用例规格（4 个 case）

#### Fixture: `env`

构造隔离的 tenant 环境：

```python
@pytest.fixture
def env(tmp_path):
    """Set up isolated tenant: config dir + memory storage in tmp_path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # settings.json: llm_provider=mock to avoid real Kimi
    (config_dir / "settings.json").write_text(json.dumps({
        "llm_provider": "mock",
        "kimi_api_key": "test-key",
        "kimi_model": "moonshot-v1-32k",
    }), encoding="utf-8")

    # personas.json: minimal valid
    (config_dir / "personas.json").write_text(json.dumps({
        "active_id": "p1",
        "personas": [{"id": "p1", "name": "测试人设", "tone": "测试"}]
    }), encoding="utf-8")

    # goals.json: empty by default; each test fills posts
    (config_dir / "goals.json").write_text(json.dumps({
        "active_goal_id": "g1",
        "goals": [{
            "id": "g1", "name": "测试", "persona_id": "p1",
            "performance": {"posts": []},
        }]
    }), encoding="utf-8")

    data_dir = tmp_path / "xhs_data"
    data_dir.mkdir()

    yield {"config_dir": config_dir, "data_dir": data_dir, "tmp_path": tmp_path}
```

#### Helpers

```python
def _set_posts(env, count: int) -> None:
    """Inject N performance posts into goals.json."""
    goals_path = env["config_dir"] / "goals.json"
    data = json.loads(goals_path.read_text(encoding="utf-8"))
    data["goals"][0]["performance"]["posts"] = [
        {"title": f"post_{i}", "likes": 100 + i, "comments": 10 + i,
         "shares": 1, "favorites": 5, "follows": 0}
        for i in range(count)
    ]
    goals_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _patch_evaluator_paths(env, monkeypatch):
    """Redirect evaluator's _data_dir / _config_dir to tmp_path."""
    monkeypatch.setattr("agents.evaluators.Path",
                        lambda p: env["tmp_path"] / p if p in ("xhs_data", "config")
                        else Path(p))
    # OR: subclass evaluator overriding __init__ to point at tmp_path
```

> **实现提示**：直接 monkeypatch 模块级 Path 太脆。更稳的做法是给 `AnalystEvaluator` 加可选构造参数 `data_dir` / `config_dir`，测试时显式传入。这是**允许的小规模 refactor**，因为 evaluator 当前的硬编码路径就是测试不友好的设计债。

#### Case 1: `test_analyst_writes_then_content_reads`（happy path，3.4.2 主验收）

```python
def test_analyst_writes_then_content_reads(env, monkeypatch):
    """3 篇 perf → analyst 写 entry → 提升 active → Content prompt 含 entry"""
    _set_posts(env, count=3)

    # 1. Mock Master.submit to return canned analysis
    from agents.base import AgentTaskResult
    monkeypatch.setattr(
        "agents.master.HermesMaster.submit",
        lambda self, task: AgentTaskResult(
            ok=True,
            content="分析：高互动笔记共性是开头用数字钩子（如『3 个技巧』）",
            error=None, task_id="t1", iterations=1,
        ),
    )

    # 2. Mock call_kimi (in _write_draft_entry summarization step)
    monkeypatch.setattr(
        "agent_tools.kimi.call_kimi",
        lambda prompt, max_tokens=500: ("- 标题用数字钩子\n- 评论引导话题", None),
    )

    # 3. Run evaluator
    evaluator = AnalystEvaluator(tenant_id="default",
                                 data_dir=env["data_dir"],
                                 config_dir=env["config_dir"])
    result = evaluator.run()
    assert result["ok"] is True
    assert result["playbook_written"] is True

    # 4. Verify entry persisted to playbook.md (default status=draft)
    memory = evaluator._memory
    playbook = memory.read("default", "content", "playbook.md") or ""
    assert "数字钩子" in playbook
    assert result["entry_id"] in playbook   # weekly-YYYY-MM-DD

    # 5. Promote draft → active (simulate user accept)
    from agents.memory import parse_entries
    _, entries = parse_entries(playbook)
    target = entries[result["entry_id"]]
    memory.update_entry(
        "default", "content", "playbook.md",
        result["entry_id"], target.body, "test_user",
        entry_meta={**target.meta, "status": "active"},
    )

    # 6. Start ContentAgent and assemble system prompt
    from agents.content import ContentAgent
    content_agent = ContentAgent(
        master_token=evaluator._master._master_token,
        tenant_id="default",
        memory=memory,
        storage=evaluator._master._storage,
    )
    sys_prompt = content_agent._assemble_system_prompt()  # or whatever the method is

    # 7. Assert playbook entry visible in Content's system prompt
    assert "数字钩子" in sys_prompt
    assert "【★ 来自 Analyst 的反馈与优化建议" in sys_prompt
```

#### Case 2: `test_insufficient_data_skips_playbook`（3.2.6 跳过分支）

```python
def test_insufficient_data_skips_playbook(env, monkeypatch):
    """2 篇 perf → 跳过写入 + 写审计 insufficient_data"""
    _set_posts(env, count=2)

    monkeypatch.setattr(
        "agents.master.HermesMaster.submit",
        lambda self, task: AgentTaskResult(
            ok=True, content="本周仅 2 篇笔记，建议补数据", error=None,
            task_id="t1", iterations=1,
        ),
    )

    evaluator = AnalystEvaluator(tenant_id="default", ...)
    result = evaluator.run()

    assert result["ok"] is True
    assert result["playbook_written"] is False

    # Playbook should NOT contain the weekly entry
    playbook = evaluator._memory.read("default", "content", "playbook.md") or ""
    assert result["entry_id"] not in playbook

    # Audit log should contain insufficient_data event
    audit_dir = env["data_dir"] / "audit"
    today = datetime.now().strftime("%Y%m%d")
    audit_file = audit_dir / f"audit_{today}.jsonl"
    assert audit_file.exists()
    events = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    insufficient = [e for e in events if e.get("kind") == "insufficient_data"]
    assert len(insufficient) >= 1
    assert insufficient[-1]["posts_count"] == 2
    assert insufficient[-1]["min_required"] == 3
```

#### Case 3: `test_exactly_three_posts_triggers_write`（3.2.6 边界）

```python
def test_exactly_three_posts_triggers_write(env, monkeypatch):
    """恰好 3 篇 → 阈值是 >=3 不是 >3，应该写入"""
    _set_posts(env, count=3)
    # ... same mocks as Case 1 ...
    result = evaluator.run()
    assert result["playbook_written"] is True
```

#### Case 4: `test_zero_posts_skips_with_audit`（3.2.6 极端）

```python
def test_zero_posts_skips_with_audit(env, monkeypatch):
    """0 篇也走 insufficient_data，不是 silent skip"""
    _set_posts(env, count=0)
    # ... same mocks as Case 2 ...
    result = evaluator.run()
    assert result["playbook_written"] is False

    audit_file = env["data_dir"] / "audit" / f"audit_{datetime.now():%Y%m%d}.jsonl"
    events = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("kind") == "insufficient_data" and e["posts_count"] == 0 for e in events)
```

### Mock 边界总结

| 被 mock 的对象 | 替换为 | 理由 |
|---|---|---|
| `HermesMaster.submit` | 返回固定 `AgentTaskResult(ok=True, content=...)` | 跳过 Analyst LLM 调用 |
| `agent_tools.kimi.call_kimi` | 返回 `("浓缩文本", None)` | `_write_draft_entry` 内有浓缩 step |
| `LocalJSONBackend` (storage) | **不 mock**，用真实实例指向 tmp_path | 验证真实文件 IO |
| `MemoryLayer` | **不 mock**，用真实实例 | 验证 entry 写入 + parse 真的工作 |
| `ContentAgent._assemble_system_prompt` | **不 mock**，直接调 | 验证 prompt 真含 entry |

---

## 5. 文件改动清单

| 文件 | 改动 | 行数 |
|---|---|---|
| `agents/evaluators.py` | + `MIN_PERF_POSTS=3`<br>+ `_audit_insufficient_data()` 方法<br>+ 构造参数 `data_dir` / `config_dir`（默认 None → 沿用现有硬编码）<br>~ `_read_performance()` 返回值语义<br>~ `assemble_prompt()` 低数据分支<br>~ `run()` 跳过逻辑 + 返回 `playbook_written` | ~40 行净增 |
| `tests/test_phase3_e2e.py` | 新文件，4 个 case + 1 fixture + 2 helpers | ~180 行 |
| `openspec/changes/agent-architecture-refactor/tasks.md` | 勾掉 3.2.6 + 3.4.2，更新进度小结 | ~5 行 |

---

## 6. 验收标准

```bash
# 1. 4 个 e2e case 全绿
python -m pytest tests/test_phase3_e2e.py -v
# 期望：4 passed

# 2. 不破坏现有测试
python -m pytest tests/ -v
# 期望：现有 30+ 个 case 仍全绿

# 3. 不破坏现有 verify_phase3.py
python verify_phase3.py
# 期望：59/59 通过
```

---

## 7. 不在本设计范围

- ❌ Phase 3.5.x 手工验收（用户参与）
- ❌ Phase 3.4.2 完整 LLM 链路验证（已选方案 B）
- ❌ MIN_PERF_POSTS 配置化（暂用常量；改 settings 字段是后续优化）
- ❌ Master 现有 `submit` 接口的修改（只 monkeypatch 不动主代码）

---

## 8. Handover note

接手编码的 Agent 须按本文档顺序：

1. 先改 `agents/evaluators.py`（D1-D5），跑 `verify_phase3.py` 确认无回归
2. 再写 `tests/test_phase3_e2e.py` 4 个 case，跑 `pytest tests/test_phase3_e2e.py -v` 确认全绿
3. 跑全套 `pytest tests/ -v` 确认无回归
4. 更新 `openspec/changes/agent-architecture-refactor/tasks.md` 勾掉两条 + 进度小结
5. 单 commit：`feat(P3): finalize 3.2.6 skip-when-insufficient + 3.4.2 e2e mock test`

如需偏离本文档，请先在此处加「DEVIATION 注释」记录原因。
