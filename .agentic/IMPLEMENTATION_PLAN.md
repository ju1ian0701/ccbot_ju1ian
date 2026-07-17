# Полный agentic pipeline для ccbot_ju1ian

**Репозиторий (форк):** https://github.com/ju1ian0701/ccbot_ju1ian  
**Локальный клон:** `D:\CCbot_tmux\ccbot\ccbot_ju1ian\`  
**Knowledge graph:** `D:\CCbot_tmux\ccbot\ccbot_ju1ian\.understand-anything\knowledge-graph.json`  
**Результат рефакторинга:** `D:\GROK\reports\ccbot-refactoring-backlog.md`  
**Дата плана:** 2026-07-15  
**Статус:** каркас pipeline уже создан в клоне; ниже — полный исполняемый план + интеграция Grok + sub-agents/MCP + оставшиеся шаги.

---

## 0. Цель и границы

### Цель

Автоматизировать цикл:

```text
knowledge-graph.json + refactoring backlog
        → структурированные атомарные задачи
        → Grok (plan / context / review) + implement-agent (code)
        → изменения в Python (локальный клон / PR)
        → ruff + pyright + pytest
        → auto-review
        → human merge (обязательная точка)
```

### Уровень автоматизации

| Этап | Авто | Человек |
|------|:----:|:-------:|
| Обновление knowledge graph (`/understand`) | частично | да (первый full-run, review ignore) |
| Analyze hotspots + rank backlog | **да** | нет |
| Выбор следующей задачи | **да** (алгоритм) | override через `task_id` |
| Генерация implement-prompt | **да** | нет |
| Применение правок в Python | **да** (agent) | при `blocked` |
| Quality gates (ruff/pyright/pytest) | **да** | нет |
| Draft PR | **да** | нет |
| Code review (Grok/Claude) | **да** (комментарии) | **да** (approve/merge) |
| Merge в `main` | нет | **обязательно** |
| Секреты / токены / policy change | нет | **обязательно** |

### Стек

- **Python 3.12+** — продукт + toolkit pipeline  
- **Shell / PowerShell** — glue, CI steps  
- **uv** — env, ruff, pyright, pytest  
- **GitHub Actions** — оркестрация  
- **Grok (xAI API)** — analyze enrich / plan narrative / PR review  
- **Implement-agent** — применение diff (Claude Code Action *или* локальный Grok/CLI agent)  
- **MCP tasks** — трекинг long-running job’ов (опционально)  
- **understand-anything** — knowledge graph  

---

## 1. Текущее состояние (что уже есть)

### Источники

| Артефакт | Путь | Назначение |
|----------|------|------------|
| Knowledge graph | `.understand-anything/knowledge-graph.json` | 530 nodes, 555 edges, 10 layers (commit `9d7ab97…`) |
| Refactoring backlog (human report) | `D:\GROK\reports\ccbot-refactoring-backlog.md` | 5 HIGH + 4 MEDIUM findings |
| Structured tasks | `.agentic/backlog/tasks.json` | `REF-001`…`REF-009` |
| Pipeline config | `.agentic/config.json`, `policies.json` | weights, guardrails |
| Toolkit | `scripts/agentic/*.py` | analyze/plan/select/validate/sync |
| Workflows (каркас) | `.github/workflows/agentic-*.yml` | analyze/plan/implement/orchestrator |
| Existing CI | `check.yml` | ruff + pyright + pytest |
| Existing agents | `claude.yml`, `claude-code-review.yml` | @claude + PR review |

### Локальная проверка toolkit (выполнено)

```text
python scripts/agentic/cli.py run --skip-quality
→ selected=REF-001
→ .agentic/out/{analysis-report,plan,selected-task,implement-prompt}.md|json
uv run pytest tests/agentic -q  →  8 passed
```

### Чего ещё нет (нужно доделать по этому плану)

1. **Grok-интеграция** (xAI API) — analyze enrich, plan narrative, PR review  
2. Workflow **`agentic-grok-review.yml`**  
3. **Atomic task packs** (разбиение REF-* на subtasks `REF-001.a`, …)  
4. Context packer: **subgraph slice** из knowledge-graph → prompt  
5. Опционально: **MCP server** для backlog/graph tools  
6. Push каркаса в remote + secrets `XAI_API_KEY`  
7. Первый end-to-end implement `REF-001` в локальном клоне  

---

## 2. Структуризация задач (agentic-ready)

### 2.1. Принцип: от finding → epic → atomic task

```text
ccbot-refactoring-backlog.md  (finding, prose)
        │  + knowledge-graph hotspots
        ▼
REF-00N  (epic / deliverable, 1 draft PR)
        │  depends_on, files, acceptance_criteria
        ▼
REF-00N.a / .b / .c  (atomic steps, 1 agent turn each)
        │
        ▼
selected-task.json + context-pack.json → agent prompt
```

### 2.2. Формат хранения (канон)

**Файл:** `.agentic/backlog/tasks.json` (уже есть).

Минимальная schema одной задачи:

```json
{
  "id": "REF-001",
  "title": "Extract shared auth + session resolution helper",
  "priority": "high",
  "status": "ready",
  "categories": ["dry", "solid"],
  "files": ["src/ccbot/bot.py", "src/ccbot/session.py"],
  "related_nodes": ["file:src/ccbot/bot.py", "file:src/ccbot/session.py"],
  "depends_on": [],
  "problem": "...",
  "solution": "...",
  "acceptance_criteria": [
    "At least 8 handlers use the shared helper",
    "No behavior change for unauthorized users",
    "Unit tests cover authorized/unauthorized/unbound/missing-window"
  ],
  "estimated_risk": "medium",
  "labels": ["priority:high", "category:dry", "agentic"],
  "atomic_steps": [
    {
      "id": "REF-001.a",
      "title": "Add SessionContext dataclass + require_session helper",
      "files": ["src/ccbot/session.py", "tests/ccbot/test_session.py"],
      "done_when": ["helper exists", "unit tests green"]
    },
    {
      "id": "REF-001.b",
      "title": "Migrate command handlers to helper",
      "files": ["src/ccbot/bot.py"],
      "done_when": ["≥4 command handlers use helper"]
    },
    {
      "id": "REF-001.c",
      "title": "Migrate message/callback handlers + cleanup duplicates",
      "files": ["src/ccbot/bot.py"],
      "done_when": ["≥8 handlers total", "no auth boilerplate left in migrated paths"]
    }
  ]
}
```

**Правила атомарности:**

| Правило | Значение |
|---------|----------|
| 1 PR = 1 `REF-00N` | epic, reviewable |
| 1 agent turn ≤ 1–3 atomic_steps | если epic большой — несколько commits в одной ветке |
| `depends_on` | жёсткий DAG (REF-002 ждёт REF-001) |
| `files` | allow-list scope для guardrails |
| `related_nodes` | ключи в knowledge-graph для context packer |
| `status` | `planned` → `ready` → `in_progress` → `done` / `blocked` |

### 2.3. Передача агенту (контракт)

Агент **не** читает prose backlog целиком. Он получает bundle:

```text
.agentic/out/
  selected-task.json      # задача + ranking
  context-pack.json       # slice графа + excerpts
  implement-prompt.md     # полный prompt
  pr-body.md              # шаблон PR
  policies snippet        # must / must_not
```

**Генерация (уже):**

```bash
python scripts/agentic/cli.py analyze
python scripts/agentic/cli.py select --task REF-001
# → .agentic/out/implement-prompt.md
```

**Доделать:** `scripts/agentic/build_context_pack.py` — вырезка subgraph:

```python
# Псевдокод (рабочий каркас для реализации)
def build_context_pack(graph: dict, task: dict) -> dict:
    node_ids = set(task.get("related_nodes") or [])
    # + соседи imports/calls на 1 hop
    # + file summaries, complexity, tested_by
    # + layer names
    return {
        "task_id": task["id"],
        "nodes": [...],
        "edges": [...],
        "hotspot_scores": {...},
        "constraints_from_agents_md": [...],
    }
```

### 2.4. Маппинг backlog → tasks (источник истины)

| Finding (report) | Task ID | Atomic split (рекомендация) |
|------------------|---------|-----------------------------|
| DRY auth/resolve | REF-001 | a helper · b commands · c messages |
| bot.py god module | REF-002 | a extract commands · b messages · c callbacks (после 001) |
| except Exception | REF-003 | a sender · b queue · c tmux (отдельные PR при желании) |
| session migration | REF-004 | a doc model · b isolate migrate · c tests |
| global mutable state | REF-005 | a MessageQueueManager · b wire shutdown · c tests |
| callback_data splits | REF-006 | a builders · b migrate producers · c tests |
| wid/tid names | REF-007 | после 001; mechanical rename PR |
| markdown fallback DRY | REF-008 | a unified send · b migrate queue |
| test gaps | REF-009 | a bot hotspots · b tmux · c queue |

---

## 3. Цикл автоматизации (triggers + human gates)

```text
┌──────────────────────────────────────────────────────────────────────┐
│  A. GRAPH REFRESH                                                    │
│     trigger: manual / after large merge                              │
│     tool: /understand  →  .understand-anything/knowledge-graph.json  │
│     human: confirm .understandignore (first run)                     │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  B. ANALYZE (auto)                                                   │
│     trigger: schedule Mon 06:00 · push graph/backlog · workflow_dispatch│
│     job: agentic-analyze.yml                                         │
│     steps: cli.py analyze → hotspots; cli.py plan → ranking          │
│     Grok (optional enrich): narrative summary of hotspots            │
│     artifacts: analysis-report.{json,md}, plan.{json,md}             │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  C. PLAN / SELECT (auto)                                             │
│     trigger: workflow_dispatch · orchestrator                        │
│     job: agentic-plan.yml                                            │
│     steps: select task (deps OK, max open agent PRs)                 │
│     Grok: optional “why this task / risk notes”                      │
│     optional: sync-issues → GitHub Issues                            │
│     human gate (soft): review ranking if score ties / high risk      │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  D. IMPLEMENT (auto, draft only)                                     │
│     trigger: workflow_dispatch(task_id) · orchestrator if enabled    │
│     job: agentic-implement.yml                                       │
│     branch: agentic/REF-00N-slug                                     │
│     agent: Claude Code Action  OR  local Grok implement loop         │
│     inputs: implement-prompt.md + context-pack + policies            │
│     writes: Python under src/ccbot, tests/                           │
│     never: push to main                                              │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  E. VALIDATE (auto, hard gate)                                       │
│     trigger: end of implement · every PR (check.yml)                 │
│     ruff check + ruff format --check                                 │
│     pyright src/ccbot/                                               │
│     pytest                                                           │
│     agentic validate: path allow/deny lists                          │
│     fail → agent retry once OR mark blocked                          │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  F. REVIEW (auto comments)                                           │
│     trigger: pull_request opened/synchronize                         │
│     jobs: agentic-grok-review.yml  (+ optional claude-code-review)   │
│     Grok: summary, must-fix, verdict (comment only)                  │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  G. HUMAN MERGE (обязательно)                                        │
│     human: read draft PR + Grok review + CI green                    │
│     human: merge / request changes                                   │
│     after merge: update backlog status → done; optional graph refresh│
└──────────────────────────────────────────────────────────────────────┘
```

### Точки обязательного human intervention

1. **Merge PR** — всегда.  
2. **Первый full knowledge-graph build** — confirm ignore list.  
3. **High-risk tasks** (`estimated_risk: high`: REF-002, REF-004, REF-005) — approve перед implement (label `agentic:approved` или manual dispatch).  
4. **Secrets** (`XAI_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`).  
5. **Policy change** (ослабление guardrails / topic-only / auth).  
6. **Blocked agent** — `.agentic/out/blocked.md`.  

---

## 4. Интеграция Grok (xAI) — где, что, как

### 4.1. Роль Grok в pipeline (разделение обязанностей)

| Роль | Кто | Почему |
|------|-----|--------|
| **Planner / narrative / risk** | **Grok** | сильный reasoning по графу + backlog |
| **Context compression** | **Grok** | сжать 530 nodes → task-relevant pack |
| **PR review** | **Grok** | structured review comment |
| **Code edit apply** | Implement-agent (Claude Code Action *или* local coding agent) | tool-use loop + git/PR |
| **Deterministic rank** | Python toolkit | без LLM, воспроизводимо |

> Grok **не** должен молча merge’ить в `main`.  
> Grok **должен** получать **уже отфильтрованный** context-pack, а не весь `knowledge-graph.json` (экономия токенов + меньше галлюцинаций).

### 4.2. Точки вызова Grok

```text
[ANALYZE]  scripts/agentic/grok_client.py  enrich_analysis()
              in: analysis-report.json (top hotspots + layers)
              out: analysis-grok.md (recommendations narrative)

[PLAN]     grok_client.py  enrich_plan()
              in: plan.json + selected-task + context-pack
              out: plan-rationale.md

[REVIEW]   .github/workflows/agentic-grok-review.yml
              in: PR diff + task id from PR body + policies
              out: PR comment (must-fix / should-fix / verdict)

[OPTIONAL IMPLEMENT via Grok]
           local loop: grok_client.py chat + apply_patch tool
           (если нет Claude token — fallback implement)
```

### 4.3. Клиент Grok (рабочий пример)

**Файл:** `scripts/agentic/grok_client.py`

```python
"""Minimal xAI Grok client for agentic pipeline stages."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

XAI_API_URL = os.environ.get("XAI_API_URL", "https://api.x.ai/v1/chat/completions")
DEFAULT_MODEL = os.environ.get("XAI_MODEL", "grok-3")


def grok_chat(
    messages: list[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not set")

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        XAI_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def enrich_analysis(analysis: dict[str, Any]) -> str:
    system = (
        "You are a senior Python architect. Given a knowledge-graph analysis "
        "of the ccbot Telegram/tmux bridge, produce a concise Russian report: "
        "top risks, recommended REF task order, and what NOT to refactor yet. "
        "Respect topic-only architecture and auth invariants."
    )
    user = json.dumps(
        {
            "stats": analysis.get("stats"),
            "hotspots": (analysis.get("hotspots") or [])[:12],
            "recommendations": analysis.get("recommendations"),
        },
        ensure_ascii=False,
        indent=2,
    )
    return grok_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )


def review_pr(diff: str, task: dict[str, Any], policies: dict[str, Any]) -> str:
    system = (
        "Review this PR for ccbot. Output Markdown with sections: "
        "Summary, Must-fix, Should-fix, Nits, Verdict "
        "(approve|request_changes|comment). Fail if topic-only or auth is weakened."
    )
    user = json.dumps(
        {"task": task, "policies": policies, "diff": diff[:120000]},
        ensure_ascii=False,
    )
    return grok_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=6000,
    )
```

### 4.4. Как Grok получает контекст из knowledge graph

```text
knowledge-graph.json
        │
        ▼
analyze_graph.py  →  path_scores, hotspots, layers
        │
        ▼
build_context_pack.py
        │  filter by task.related_nodes + 1-hop imports/calls
        │  attach file summaries, complexity, tested_by
        ▼
context-pack.json  (≤ ~30–80 KB, не мегабайты)
        │
        ▼
Grok messages[]  (system policies + user pack + task JSON)
```

**Не делать:** пихать весь graph (530 nodes) в один prompt.  
**Делать:** hotspot table + subgraph вокруг `files[]` + AGENTS.md constraints.

### 4.5. Встраивание в GitHub Actions

**Secret:** `XAI_API_KEY` (repo secret).  
**Optional env:** `XAI_MODEL=grok-3`, `XAI_API_URL=https://api.x.ai/v1/chat/completions`.

Фрагмент для `agentic-analyze.yml` (добавить step):

```yaml
      - name: Grok enrich analysis
        if: ${{ secrets.XAI_API_KEY != '' }}
        env:
          XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
          XAI_MODEL: ${{ vars.XAI_MODEL || 'grok-3' }}
        run: |
          python scripts/agentic/cli.py analyze
          python - <<'PY'
          import json
          from pathlib import Path
          import sys
          sys.path.insert(0, "scripts/agentic")
          from grok_client import enrich_analysis
          analysis = json.loads(Path(".agentic/out/analysis-report.json").read_text(encoding="utf-8"))
          text = enrich_analysis(analysis)
          Path(".agentic/out/analysis-grok.md").write_text(text, encoding="utf-8")
          print(text[:2000])
          PY
```

**Workflow PR review:** `.github/workflows/agentic-grok-review.yml` — полный пример в §6.3.

### 4.6. Grok vs Claude в этом репо

| Сценарий | Рекомендация |
|----------|--------------|
| Есть только `XAI_API_KEY` | Grok plan+review; local implement через Grok Build / CLI agent |
| Есть `CLAUDE_CODE_OAUTH_TOKEN` | Claude implement (уже в `agentic-implement.yml`); Grok review |
| Есть оба | **Grok plan/review + Claude implement** (лучшее разделение) |

---

## 5. GitHub Workflows — полный набор

### 5.1. Карта workflows

| Файл | Trigger | Назначение | Agent |
|------|---------|------------|-------|
| `check.yml` | push/PR | ruff + pyright + pytest | — |
| `agentic-analyze.yml` | cron / push graph / manual | hotspots + rank | Python + **Grok enrich** |
| `agentic-plan.yml` | manual | select task, optional issues | Python + **Grok rationale** |
| `agentic-implement.yml` | manual / callable | apply code, draft PR | **Claude/Grok implement** |
| `agentic-orchestrator.yml` | manual / weekly | chain A→C→(D) | orchestration |
| `agentic-grok-review.yml` | **создать** PR | Grok review comment | **Grok** |
| `claude.yml` | @claude | ad-hoc | Claude |
| `claude-code-review.yml` | PR | optional second reviewer | Claude |

### 5.2. Permissions matrix

| Workflow | contents | pull-requests | issues | id-token | actions |
|----------|----------|---------------|--------|----------|---------|
| analyze | read | — | — | — | — |
| plan | read | — | write | — | — |
| implement | **write** | **write** | write | write | read |
| grok-review | read | **write** | — | — | — |
| check | read | — | — | — | — |

### 5.3. Quality gates (единый контракт)

```bash
uv sync --all-extras
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/ccbot/
uv run pytest --tb=short -q
python scripts/agentic/cli.py validate --base-ref origin/main
```

---

## 6. Полный список файлов + примеры

### 6.1. Inventory

```text
# УЖЕ СОЗДАНО в локальном клоне
.agentic/
  README.md
  config.json
  policies.json
  backlog/tasks.json
  backlog/source-report.md
  prompts/{analyze,plan,implement,review,pr-body}.md
  out/                    # gitignored, regenerated

scripts/agentic/
  cli.py
  analyze_graph.py
  prioritize.py
  render_prompt.py
  validate_changes.py
  sync_issues.py
  update_backlog_status.py
  paths.py
  __init__.py
  README.md

.github/workflows/
  agentic-analyze.yml
  agentic-plan.yml
  agentic-implement.yml
  agentic-orchestrator.yml
  check.yml
  claude.yml
  claude-code-review.yml

tests/agentic/test_pipeline.py

# НУЖНО СОЗДАТЬ / ДОПИЛИТЬ
scripts/agentic/grok_client.py              # §4.3
scripts/agentic/build_context_pack.py       # subgraph packer
scripts/agentic/apply_local_task.sh         # local implement glue (bash)
scripts/agentic/apply_local_task.ps1        # Windows glue
.github/workflows/agentic-grok-review.yml   # §6.3
.agentic/prompts/grok-review.md
.agentic/prompts/grok-plan.md
.agentic/mcp/server.py                     # optional MCP
.agentic/backlog/tasks.json                 # + atomic_steps fields
```

### 6.2. Пример: `apply_local_task.ps1` (Windows, локальный клон)

```powershell
# scripts/agentic/apply_local_task.ps1
param(
  [string]$TaskId = "",
  [switch]$SkipQuality
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..\..

python scripts/agentic/cli.py analyze
if ($TaskId) {
  python scripts/agentic/cli.py select --task $TaskId
} else {
  python scripts/agentic/cli.py select
}

Write-Host "=== Feed this prompt to Grok Build / coding agent ==="
Write-Host (Resolve-Path .agentic\out\implement-prompt.md)

# After agent edits:
if (-not $SkipQuality) {
  uv sync --all-extras
  uv run ruff check src/ tests/
  uv run ruff format src/ tests/
  uv run pyright src/ccbot/
  uv run pytest --tb=short -q
  python scripts/agentic/cli.py validate
}
```

### 6.3. Пример: `agentic-grok-review.yml`

```yaml
name: Agentic Grok Review

on:
  pull_request:
    types: [opened, synchronize, ready_for_review, reopened]

permissions:
  contents: read
  pull-requests: write

jobs:
  grok-review:
    if: startsWith(github.head_ref, 'agentic/') || contains(github.event.pull_request.labels.*.name, 'agentic')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Collect PR context
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          git fetch origin "${{ github.base_ref }}"
          git diff --unified=3 "origin/${{ github.base_ref }}"...HEAD > /tmp/pr.diff
          mkdir -p .agentic/out
          # try extract task id from PR body marker
          python - <<'PY'
          import os, re, json, pathlib
          body = """${{ github.event.pull_request.body }}"""
          m = re.search(r"REF-\d+", body or "")
          task_id = m.group(0) if m else ""
          pathlib.Path(".agentic/out/review-meta.json").write_text(
              json.dumps({"task_id": task_id}, indent=2), encoding="utf-8"
          )
          print("task_id=", task_id)
          PY

      - name: Grok review
        env:
          XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
        run: |
          python - <<'PY'
          import json, pathlib, sys
          sys.path.insert(0, "scripts/agentic")
          from grok_client import review_pr
          from paths import load_json, find_repo_root
          root = find_repo_root()
          policies = load_json(root / ".agentic" / "policies.json")
          meta = json.loads(pathlib.Path(".agentic/out/review-meta.json").read_text())
          tasks = load_json(root / ".agentic" / "backlog" / "tasks.json")["tasks"]
          task = next((t for t in tasks if t["id"] == meta.get("task_id")), {"id": meta.get("task_id")})
          diff = pathlib.Path("/tmp/pr.diff").read_text(encoding="utf-8", errors="replace")
          text = review_pr(diff, task, policies)
          pathlib.Path(".agentic/out/grok-review.md").write_text(text, encoding="utf-8")
          print(text[:3000])
          PY

      - name: Comment on PR
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh pr comment ${{ github.event.pull_request.number }} \
            --body-file .agentic/out/grok-review.md
```

### 6.4. Пример prompt: `.agentic/prompts/grok-review.md`

```markdown
# Grok PR Review — ccbot agentic

## Inputs
- PR diff
- selected task JSON (REF-*)
- policies.json
- AGENTS.md constraints (topic-only, auth, MarkdownV2 safe_*)

## Output (Markdown only)
1. **Summary** (2–4 sentences)
2. **Must-fix** (blocking)
3. **Should-fix**
4. **Nits**
5. **Verdict**: `approve` | `request_changes` | `comment`

## Fail conditions (always Must-fix)
- reintroduces non-topic mode
- weakens is_user_allowed / auth
- keys routing by window name instead of @id
- silences exceptions on user-visible send paths without logging
```

### 6.5. Пример `config.json` (ключевые поля — уже в репо)

```json
{
  "knowledge_graph": {
    "path": ".understand-anything/knowledge-graph.json"
  },
  "backlog": { "path": ".agentic/backlog/tasks.json" },
  "implementation": {
    "branch_prefix": "agentic/",
    "draft_pr": true,
    "require_tests": true,
    "require_ruff": true,
    "require_pyright": true
  },
  "guardrails": {
    "allowed_path_globs": ["src/ccbot/**/*.py", "tests/**", ".agentic/**", "scripts/agentic/**"],
    "blocked_path_globs": [".env", "**/*secret*", ".github/workflows/check.yml"]
  }
}
```

---

## 7. Sub-agents и MCP — полный список

### 7.1. Sub-agents (логические роли)

| ID | Роль | Реализация | Input | Output | Tools |
|----|------|------------|-------|--------|-------|
| `graph-analyzer` | hotspots из KG | `analyze_graph.py` (+ optional Grok enrich) | knowledge-graph.json | analysis-report.* | read files |
| `task-planner` | rank/select | `prioritize.py` + Grok rationale | backlog + analysis | plan.*, selected-task | read |
| `context-packer` | subgraph slice | `build_context_pack.py` (todo) | graph + task | context-pack.json | read |
| `implementer` | code edits | Claude Code Action / Grok coding agent | implement-prompt | git diff, branch | Edit, Bash(uv/git/gh) |
| `validator` | gates | `validate_changes.py` + check.yml | diff | validation.json | ruff/pyright/pytest |
| `reviewer-grok` | PR review | agentic-grok-review.yml | diff + task | PR comment | gh, XAI API |
| `reviewer-claude` | optional 2nd | claude-code-review.yml | PR | review comments | Claude Action |
| `issue-syncer` | backlog→issues | `sync_issues.py` | tasks.json | GitHub Issues | gh |
| `understand-*` | graph build | skill `/understand` | codebase | knowledge-graph.json | subagents scanner/analyzer/… |

**Grok Build TUI subagents (локально, при работе в Grok):**

| subagent_type | Когда |
|---------------|-------|
| `explore` | быстро найти handlers / auth patterns перед implement |
| `plan` | спроектировать split bot.py (REF-002) |
| `general-purpose` | multi-step implement + tests |
| `review` skill | локальный review diff перед push |

### 7.2. MCP servers

| MCP server | Tools | Назначение в pipeline |
|------------|-------|------------------------|
| **tasks** (уже в `D:\GROK\mcps\tasks`) | `create`, `list`, `update`, `get_results`, `pause`, `delete` | трекинг long-running analyze/implement jobs в Grok session |
| **github** (опционально, GitHub MCP) | issues/PRs/contents | вместо `gh` CLI, если настроен |
| **filesystem** (built-in agent tools) | Read/Write/Glob | apply patches в локальном клоне |
| **custom `ccbot-agentic` MCP** (рекомендуется добавить) | см. ниже | единый API к backlog/graph для агента |

#### Custom MCP tool surface (проектный)

```text
ccbot-agentic MCP (scripts or .agentic/mcp/server.py)
  list_tasks(status?)
  get_task(task_id)
  select_next_task(force_id?)
  get_context_pack(task_id)
  get_analysis()
  mark_task_status(task_id, status)
  run_validate(base_ref?)
```

Минимальный контракт `list_tasks`:

```json
{
  "name": "list_tasks",
  "arguments": { "status": "ready" },
  "result": {
    "tasks": [
      { "id": "REF-001", "title": "...", "score": 120, "priority": "high" }
    ]
  }
}
```

#### Existing tasks MCP (Grok)

```text
D:\GROK\mcps\tasks\tools\
  create.json   — создать job
  list.json     — список
  update.json   — обновить статус
  get_results.json
  pause.json
  delete.json
```

Использование: при `agentic-implement` длительностью >15 мин — `tasks.create` с prompt «implement REF-001», poll `get_results`.

### 7.3. Внешние actions / APIs

| Компонент | Secret / auth |
|-----------|----------------|
| xAI Grok API | `XAI_API_KEY` |
| Claude Code Action | `CLAUDE_CODE_OAUTH_TOKEN` |
| GitHub | `GITHUB_TOKEN` (Actions) / `gh auth` locally |

---

## 8. Пошаговый план реализации (исполняемый)

### Фаза 0 — Подготовка (1 раз)

| # | Действие | Команда / результат | Авто/Human |
|---|----------|---------------------|------------|
| 0.1 | Убедиться, что клон на `main` и graph на месте | `Test-Path ...\knowledge-graph.json` | H |
| 0.2 | Прочитать backlog report | `D:\GROK\reports\ccbot-refactoring-backlog.md` | H |
| 0.3 | Проверить toolkit | `python scripts/agentic/cli.py run --skip-quality` | A |
| 0.4 | Прогнать unit tests toolkit | `uv run pytest tests/agentic -q` | A |
| 0.5 | Добавить secrets в GitHub | `XAI_API_KEY`, при необходимости Claude token | **H** |

### Фаза 1 — Довести data layer

| # | Действие | Детали |
|---|----------|--------|
| 1.1 | Добавить `atomic_steps` в каждый REF-* | см. §2.2 |
| 1.2 | Реализовать `build_context_pack.py` | 1-hop subgraph + summaries |
| 1.3 | Реализовать `grok_client.py` | §4.3 |
| 1.4 | Промпты `grok-plan.md`, `grok-review.md` | §6.4 |
| 1.5 | CLI: `python scripts/agentic/cli.py context --task REF-001` | пишет context-pack.json |
| 1.6 | CLI: `python scripts/agentic/cli.py grok-enrich` | analysis-grok.md |

### Фаза 2 — CI workflows

| # | Действие | Детали |
|---|----------|--------|
| 2.1 | В `agentic-analyze.yml` добавить Grok enrich step | if secret present |
| 2.2 | Создать `agentic-grok-review.yml` | §6.3 |
| 2.3 | В implement: pre-step `build_context_pack` + attach to prompt | |
| 2.4 | Для high-risk: require label `agentic:approved` | `if: contains(labels…)` |
| 2.5 | Push branch `feat/agentic-pipeline` → PR (не сразу в main, по желанию) | **H merge** |

### Фаза 3 — Первый end-to-end на Python-коде (локально)

| # | Действие | Детали |
|---|----------|--------|
| 3.1 | `python scripts/agentic/cli.py select --task REF-001` | prompt ready |
| 3.2 | Implement **REF-001** в локальном клоне | helper `require_session` / `SessionContext` |
| 3.3 | Тесты: unauthorized / unbound / missing window | `tests/ccbot/` |
| 3.4 | `uv run ruff … && pyright && pytest` | hard gate |
| 3.5 | `python scripts/agentic/update_backlog_status.py REF-001 done` | after merge |
| 3.6 | Commit на `agentic/REF-001-…`, draft PR | A/H |

### Фаза 4 — Автоматизация PR review

| # | Действие |
|---|----------|
| 4.1 | Открыть PR с label `agentic` |
| 4.2 | `check.yml` + `agentic-grok-review.yml` |
| 4.3 | Human: merge only if Grok verdict ≠ request_changes **or** human overrides with comment |
| 4.4 | Optional: second pass Claude review |

### Фаза 5 — Оркестратор «полный автомат» (осторожно)

| # | Действие |
|---|----------|
| 5.1 | `AGENTIC_AUTO_IMPLEMENT=false` по умолчанию |
| 5.2 | Weekly: analyze only |
| 5.3 | Включить auto-implement **только** для `priority=medium` + `risk=low` |
| 5.4 | High-risk всегда manual dispatch |

---

## 9. Пошаговый журнал уже выполненных действий

> Этот раздел — audit trail того, что сделано до/в рамках подготовки pipeline.

### 2026-07-11 — Refactoring analysis

1. Проанализирован upstream/local ccbot с опорой на knowledge graph.  
2. Сформирован `D:\GROK\reports\ccbot-refactoring-backlog.md` (5 HIGH, 4 MEDIUM).  

### 2026-07-14 — Knowledge graph

1. Построен/обновлён `.understand-anything/knowledge-graph.json` (530 nodes / 555 edges / 10 layers).  
2. `meta.json` → commit `9d7ab9721f09d748db242c22d04aa494cbebc72d`.  

### 2026-07-15 — Agentic scaffold (локальный клон форка)

1. Создан каталог `.agentic/` (config, policies, prompts, backlog).  
2. Структурированы задачи `REF-001`…`REF-009` в `tasks.json`.  
3. Написан Python toolkit `scripts/agentic/` (analyze/plan/select/validate/sync).  
4. Добавлены workflows:  
   - `agentic-analyze.yml`  
   - `agentic-plan.yml`  
   - `agentic-implement.yml`  
   - `agentic-orchestrator.yml`  
5. Добавлены unit tests `tests/agentic/test_pipeline.py` → **8 passed**.  
6. Локальный dry-run: **selected = REF-001**.  
7. Guardrails + ignored paths (`.understand-anything/`) настроены.  
8. Исходный markdown backlog скопирован в `.agentic/backlog/source-report.md`.  
9. **Не сделано:** push в GitHub, Grok client, context packer, grok-review workflow, implement REF-001 в product code.  

### 2026-07-15 — Этот документ

1. Составлен полный план: `D:\GROK\reports\ccbot-agentic-pipeline-plan.md`.  
2. Зафиксированы: task format, Grok integration points, workflows, sub-agents, MCP, human gates.  

### 2026-07-15 — Custom MCP `ccbot-agentic`

1. Реализован stdio MCP server: `scripts/agentic/mcp_server.py` (stdlib JSON-RPC).  
2. Service layer: `scripts/agentic/service.py` + context packer `build_context_pack.py`.  
3. Project config: `.grok/config.toml` → `[mcp_servers.ccbot-agentic]`.  
4. Tools: `list_tasks`, `get_task`, `select_next_task`, `get_analysis`, `get_context_pack`, `get_implement_prompt`, `mark_task_status`, `run_analyze`, `run_validate`, `pipeline_status`.  
5. Smoke test: `scripts/agentic/mcp_smoke_test.py`.  
6. Docs: `.agentic/mcp/README.md`.  

---

## 10. Рекомендуемый порядок «завтра утром»

```powershell
cd D:\CCbot_tmux\ccbot\ccbot_ju1ian

# 1) sanity
python scripts/agentic/cli.py analyze
python scripts/agentic/cli.py plan

# 2) (после добавления grok_client.py + XAI_API_KEY)
# $env:XAI_API_KEY = "..."
# python scripts/agentic/cli.py grok-enrich

# 3) локальный implement REF-001 через Grok Build / coding agent
python scripts/agentic/cli.py select --task REF-001
# открыть .agentic\out\implement-prompt.md → agent edits src/

# 4) gates
uv sync --all-extras
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pyright src/ccbot/
uv run pytest --tb=short -q

# 5) commit / PR (когда готовы)
# git checkout -b agentic/REF-001-auth-session-helper
# git add ...
# git commit -m "refactor(agentic): REF-001 extract require_session helper"
# git push -u origin HEAD
# gh pr create --draft ...
```

---

## 11. Риски и mitigations

| Риск | Mitigation |
|------|------------|
| Agent ломает auth/topic routing | policies.json + Grok must-fix + human merge |
| Слишком большой diff | max_files_changed=40, atomic_steps, 1 REF per PR |
| Graph stale | re-run `/understand` after big merges |
| Grok hallucinates file paths | context-pack only from real graph node ids |
| CI cost / token cost | weekly analyze; implement manual; compress context |
| Secret leak to Claude subprocess | existing env scrubbing; never commit `.env` |

---

## 12. Definition of Done (pipeline)

Pipeline считается **готовым**, когда:

- [x] Structured backlog `REF-*` существует  
- [x] Analyze/plan/select/validate работают локально  
- [x] Agentic workflows-каркас на месте  
- [ ] Grok client + review workflow  
- [ ] Context packer из knowledge graph  
- [ ] Atomic steps в tasks.json  
- [ ] Secrets в GitHub  
- [ ] Первый successful draft PR `REF-001` с green `check.yml`  
- [ ] Human merge process задокументирован (этот файл)  

Pipeline считается **production-ready**, когда дополнительно:

- [ ] ≥3 agentic PR прошли human merge без откатов  
- [ ] High-risk gate (`agentic:approved`) работает  
- [ ] Backlog statuses синхронизируются после merge  

---

## 13. Нужна ли доп. информация?

Для бесшовного запуска implement в CI уточни (если есть):

1. **`XAI_API_KEY`** уже есть в GitHub secrets форка?  
2. Предпочтительный implementer: **Claude Code Action**, **Grok only**, или **оба**?  
3. Можно ли пушить ветку `feat/agentic-pipeline` в `origin`?  
4. Нужен ли **custom MCP server** сразу, или хватит CLI + workflows?  

Без ответов можно продолжать **локальный** цикл (фаза 3) полностью оффлайн от secrets, кроме Grok enrich.

---

## Приложение A — Быстрый reference CLI

```bash
python scripts/agentic/cli.py analyze
python scripts/agentic/cli.py plan [--task REF-001]
python scripts/agentic/cli.py select [--task REF-001]
python scripts/agentic/cli.py validate [--base-ref origin/main] [--skip-quality]
python scripts/agentic/cli.py sync-issues [--apply]
python scripts/agentic/cli.py run [--task REF-001] [--skip-quality]
python scripts/agentic/update_backlog_status.py REF-001 done
```

## Приложение B — Связанные документы

| Документ | Путь |
|----------|------|
| Этот план | `D:\GROK\reports\ccbot-agentic-pipeline-plan.md` |
| Refactoring backlog | `D:\GROK\reports\ccbot-refactoring-backlog.md` |
| Pipeline README | `D:\CCbot_tmux\ccbot\ccbot_ju1ian\.agentic\README.md` |
| Knowledge graph | `D:\CCbot_tmux\ccbot\ccbot_ju1ian\.understand-anything\knowledge-graph.json` |
| AGENTS.md / CLAUDE.md | корень клона |
