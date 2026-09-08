<div align="center">

```
███████╗██╗   ██╗██╗    ██╗    █████╗  ██████╗ ███████╗███╗   ██╗████████╗
██╔════╝██║   ██║██║    ██║   ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
█████╗  ██║   ██║██║    ██║   ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
██╔══╝  ██║   ██║██║    ██║   ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
██║     ╚██████╔╝██████╗██║   ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
╚═╝      ╚═════╝ ╚═════╝╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
```

# FullAgent `v3.4.0`

**Terminal AI Agent — Event-Sourced Kernel · Goal Contracts · Persistent Crew · Self-Healing**

`40+ Slash Commands` · `16 Tools` · `9 Providers` · `Real-time Web` · `Crew & Daemon`

[Install](#-install) · [Run](#-run) · [Features](#-features) · [Commands](#-slash-commands) · [Providers](#-providers--models) · [Architecture](#-architecture)

</div>

---

## 📦 Install

### Option 1 — Standalone binary (no Python needed)

```bash
pip install pyinstaller
pyinstaller fullagent.spec --noconfirm
# → dist/fullagent  (single-file executable, ~15 MB)

./dist/fullagent              # interactive TUI
./dist/fullagent --help       # headless subcommands
```

### Option 2 — From source

```bash
git clone https://github.com/cmyolo441-coder/8subagent-same-code
cd 8subagent-same-code/aiagent/py.test
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

> Requires **Python 3.9+**. Works on Linux, macOS, Windows and Termux.

## ▶️ Run

```bash
python main.py        # or: python -m fullagent
```

You'll see the banner and the persistent prompt box:

```
╭─ FullAgent ── model: MiMo v2.5 FREE ── effort: HIGH ── session a1b2c3d4 ─╮
│ ❯ type anything… the agent reads, writes, edits files, runs commands     │
╰─ Enter send · Esc+Enter newline · / commands · Ctrl+T models ────────────╯
```

While the model works, the bottom border shows a live animated status
(`⠹ thinking… · Ctrl+C cancel`), tokens stream in real time, tool calls
appear as `⚙ name args` with `✓`/`✗` results, and each turn ends with a
stats line (`2.3s · 512→87 tokens`).

## ✨ Features

- **Files & Shell** — read/write/edit files, run bash commands, regex + glob search, copy/move/delete
- **Web** — fetch URLs and search the web (DuckDuckGo + Bing fallback), results stamped with retrieval time
- **Agent loop** — the model keeps calling tools until the task is genuinely done (up to 40 iterations)
- **Temporal Kernel** — every message, tool call, result and cost is an immutable, content-addressed event; rewind, fork, replay and Merkle-verify the timeline
- **Goal contracts** — goals are structured objects with machine-checkable done-criteria; progress is a computed number, not a vibe
- **Persistent Crew** — Codex-style background subagents: spawn, message, wait, close, resume — serial execution, per-agent model override
- **Judge** — claims verified with deterministic predicates (exit codes, file checks, regex), never the model's own word
- **Memory** — episodic memory + a dead-end ledger that deterministically blocks failed approaches; semantic (meaning-based) recall
- **AutoPilot** — the agent self-routes each turn: goal mode for verifiable missions, real-time web when live data is needed
- **Provider failover** — automatic switch to a fallback model on 429/5xx outages
- **Self-healing** — tool errors are classified by root cause, fixed, re-checked and sealed as lessons
- **Skill forge** — the agent can author new tools, gated by parse → shape → safety (AST scan) → its own tests
- **Focus mode** — `/focus 10` auto-continues turn after turn until the goal closes
- **Workflows** — saved multi-step pipelines with phased orchestration and `expect` predicates
- **Audit export** — `/export md|html` writes a self-contained session report
- **Safety** — risky tools ask for approval in-app (`y`/`n`/`a`); the autonomy ladder (`/autonomy 0-5`) scales from read-only observer to fully autonomous

## ⌨️ Keys

| Key | Action |
|---|---|
| `Enter` | send |
| `Esc+Enter` | newline inside the box |
| `Ctrl+T` / `/model` | model selector |
| `Ctrl+E` / `/effort` | effort selector |
| `↑↓` `Tab` `Home` `End` | navigate selectors & completion menu |
| `Ctrl+R` | search input history |
| `Ctrl+L` | clear screen |
| `Ctrl+C` | cancel running turn / clear input |
| `Ctrl+D` | quit |

## 💬 Slash commands

**Core:** `/model` `/effort` `/help` `/history` `/new` `/save` `/approve` `/reasoning` `/usage` `/clear` `/about` `/exit`

**Kernel & goals:**
`/goal set <statement> | <clause1> | <clause2>` · `/goal status` · `/autonomy <0-5>` · `/state` · `/rewind <seq>` · `/fork` · `/verify` · `/judge` · `/memory` · `/recall <q>`

**Intelligence:**
`/auto` · `/router` · `/spec` · `/dashboard` · `/council <q>` · `/mastermind` · `/prompt [main|master]`

**Engineering:**
`/analyze <path>` · `/graph [index|query|impact]` · `/coverage` · `/fuzz` · `/mutate <file> <suite-cmd>`

**Operations:**
`/crew spawn|send|wait|close|resume` · `/focus <1-20>` · `/mission` · `/heal` · `/skills` · `/workflow run <name>` · `/export md|html` · `/notify` · `/health` · `/render on|off`

## 🔌 Providers & Models

Nine OpenAI-compatible providers are built in:

| Provider | Base URL | Example models |
|---|---|---|
| **OpenCode Zen** | `opencode.ai/zen/v1` | mimo-v2.5-free, claude-sonnet-4-5, gemini-3.1-pro, gpt-5.2 |
| **OpenCode** | `opencode.ai/zen/v1` | muse-spark-1.2/1.3-contributor-free |
| **TokenRouter** | `api.tokenrouter.com/v1` | qwen3.8-max-free, DeepSeek-V3.2, Kimi-K2 |
| **Agnes** | `apihub.agnes-ai.com/v1` | agnes-2.5-flash |
| **ZenMux** | `zenmux.ai/api/v1` | dots3-note-prev |
| **NVIDIA NIM** | `integrate.api.nvidia.com/v1` | deepseek-v4-pro-0813 (1M ctx) |
| **B.AI** | `api.b.ai/v1` | deepseek-v4-flash, qwen3.8-flash, glm-5.3-flash |
| **KiosAPI Router** | `router.kiosapi.com/v1` | grok-composer-2.5-fast, grok-4.6 |
| **XKiro** | `api.xkiro.com/v1` | qwen3.7/3.8-max:free, minimax-m2.7-highspeed |

### API keys

Set your keys as environment variables — **never commit them to the repo**:

```bash
export OPENCODE_API_KEY=sk-...       # OpenCode Zen / OpenCode
export TOKENROUTER_API_KEY=sk-...
export AGNES_API_KEY=sk-...
export ZENMUX_API_KEY=sk-...
export NVIDIA_API_KEY=nvapi-...
export BAI_API_KEY=sk-...
export KIOSAPI_API_KEY=sk-...
export XKIRO_API_KEY=sk-...
```

> ⚠️ **Security:** keep keys out of source control. Use a `.env` file
> (git-ignored) or your shell profile. If a key was ever committed,
> rotate it immediately.

You can also hot-load custom providers/models at runtime via
`~/.fullagent/models.json` — no code changes, no restart.

Config persists in `~/.fullagent/config.json`; sessions in
`~/.fullagent/sessions/`. Override the state directory with
`FULLAGENT_HOME`. A read-only home degrades gracefully to `$TMPDIR`.

## 🏗️ Architecture

```
main.py            launcher
fullagent/
  config.py        providers, models, effort levels, paths
  systemprompt.py  the ONE home of every system prompt (single source)
  mastermind.py    prompt coherence: sealed vault, gate, composer, lineage
  tools.py         16 tools: files, shell, search, real-time web
  client.py        streaming OpenAI-compatible client (SSE, retries, cancel)
  agent.py         agent loop: LLM <-> tools, event-sourced on the kernel
  kernel.py        Temporal Kernel: append-only, content-addressed event log
  memory.py        episodic memory + dead-end ledger
  goal.py          goal contracts with machine-checkable done-criteria
  judge.py         deterministic verification predicates
  team.py          shared subagent substrate (roles, reports, write lock)
  crew.py          persistent subagents — serial execution
  workflows.py     saved multi-step pipelines
  autopilot.py     self-routing: auto goal mode / real-time web
  router.py        smart model routing — cheapest capable model per task
  semantic.py      semantic vector memory — meaning-based recall
  speculate.py     speculative prefetch of read-only tool calls
  dashboard.py     live observability ledger
  daemon.py        mission control — resumable long-running missions
  healer.py        self-healing — root-cause capture, fix, retry
  skills.py        skill forge — self-authored, safety-gated tools
  council.py       adversarial debate — thesis/antithesis + blind judge
  taint.py         static analysis — taint flows, complexity, cycles
  kgraph.py        knowledge graph — entities, relations, impact
  cov.py           real line coverage (sys.settrace)
  fuzz.py          property-based fuzzing with crash shrinking
  mutate.py        mutation testing — can your tests catch bugs?
  tui.py           persistent double-line box, overlays, streaming
  __main__.py      entry point
```

Every module ships a self-test: `python -m fullagent.kernel`
(and `.memory`, `.goal`, `.judge`, `.crew`, `.router`, …).

### Design principles

1. **Event-sourced everything** — state is a pure fold of the append-only log; nothing can drift.
2. **Deterministic verification** — claims are checked with predicates, never by asking the model.
3. **Single source of truth** — one prompt file, one delivery path (`systemprompt.with_system()`), sealed with sha256 fingerprints.
4. **Pure stdlib** — no heavyweight dependencies; `prompt_toolkit`, `rich`, `requests` only.
5. **Safety gates** — approvals, autonomy ladder, AST-scanned skills, read-only speculation whitelist.

## 📚 Further reading

- [API_RESILIENCE.md](API_RESILIENCE.md) — adaptive API scheduling & durable worker recovery
- [HIERARCHY_MODE.md](HIERARCHY_MODE.md) — 8 leads + 64 children topology
- [VERIFICATION.md](VERIFICATION.md) — evidence & self-tests
- [ENTERPRISE_READINESS.md](ENTERPRISE_READINESS.md) — limits & honest constraints

---

<div align="center">

**FullAgent** — *Pure Python · Event-Sourced · Self-Healing*

</div>
