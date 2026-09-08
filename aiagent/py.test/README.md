> **v3.4.1 — ready-to-run build: 8 provider keys built-in, no export needed + fresh Linux binary.**
> Adaptive API control + durable worker recovery (v3.4) included: API requests use a separate
> family-fair capacity queue (initial **1**, default max **2 per origin/account domain**).
> Details: [API_RESILIENCE.md](API_RESILIENCE.md) · setup: [HIERARCHY_MODE.md](HIERARCHY_MODE.md) ·
> evidence: [VERIFICATION.md](VERIFICATION.md) · limits: [ENTERPRISE_READINESS.md](ENTERPRISE_READINESS.md).

<div align="center">

```
███████╗██╗   ██╗██╗    ██╗    █████╗  ██████╗ ███████╗███╗ ██╗████████╗
██╔════╝██║   ██║██║    ██║   ██╔══██╗██╔════╝ ██╔════╝████╗██║╚══██╔══╝
█████╗  ██║   ██║██║    ██║   ███████║██║  ███╗█████╗  ██╔██╗██║  ██║
██╔══╝  ██║   ██║██║    ██║   ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║  ██║
██║     ╚██████╔╝██████╗█████╗██║  ██║╚██████╔╝███████╗██║ ╚████║  ██║
╚═╝      ╚═════╝ ╚═════╝╚════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝  ╚═╝
```

<h1>FullAgent <sup><code>v3.4.1</code></sup></h1>

**Terminal AI Agent — Adaptive API Scheduling, Durable Recovery and Real Workspace Tools**

*`Event-Sourced Kernel` · `Goal Contracts` · `Persistent Crew` · `Self-Healing` · `8 Providers Built-In` · `40+ Slash Commands` · `16 Tools`*

[**Install**](#install) •
[**Run**](#run) •
[**Providers**](##models--providers) •
[**Commands**](#slash-commands) •
[**Layout**](#layout)

</div>

---

```
╭─ FullAgent ── model: MiMo v2.5 FREE ── effort: HIGH ── session a1b2c3d4 ─╮
│ ❯ type anything… the agent reads, writes, edits files, runs commands     │
╰─ Enter send · Esc+Enter newline · / commands · Ctrl+T models ────────────╯
```

Double-line prompt box, live token streaming, `⚙ name args` + `✓`/`✗` tool results,
per-turn stats (`2.3s · 512→87 tokens`), live `ctx %` meter.

## Install

**Option A — binary (no Python needed), from this repo
(`cmyolo441-coder/8subagent-same-code` releases):**

```bash
curl -fsSL https://github.com/cmyolo441-coder/8subagent-same-code/releases/latest/download/fullagent-linux-x64 -o fullagent \
  && chmod +x fullagent \
  && sudo mv fullagent /usr/local/bin/fullagent \
  && fullagent --version   # → 3.4.1
```

Manual download: [Releases](https://github.com/cmyolo441-coder/8subagent-same-code/releases/latest)
→ `fullagent-linux-x64`, then `chmod +x` + move to PATH.

Windows (`fullagent-windows-x64.exe`) and macOS (`fullagent-darwin-arm64`) binaries
are published the same way when built via the `build-binaries` workflow
(tag `v*` → automatic release).

**Option B — from source (any OS, Python 3.9+):**

```bash
pip install prompt_toolkit rich requests
python main.py
# or: python -m fullagent
# or: pip install git+https://github.com/cmyolo441-coder/8subagent-same-code.git
```

## Run

```bash
python main.py
```

First run creates `~/.fullagent/config.json` (model, effort, auto-approve).
Sessions → `~/.fullagent/sessions/`, event log → `~/.fullagent/eventlog.jsonl`.
Override dir: `FULLAGENT_HOME=/path/to/dir python main.py`.

## What it can do

- **Files** — read (line numbers), write, exact-string edit, list, info, mkdir, copy, move, delete
- **Shell** — any bash command, exit code + stdout + stderr captured
- **Search** — regex content search, glob file search
- **Web** — fetch URLs, real-time web search (DuckDuckGo + Bing fallback)
- **Agent loop** — keeps calling tools until done (up to 40 iterations), then summarizes
- **Temporal Kernel** — every message/tool/result/cost is an immutable event; rewind, fork, replay, verify
- **Goal contracts** — done-criteria clauses + anti-clauses, computed distance-to-done
- **Memory** — episodes compress to structured records; dead-ends blocked deterministically
- **Judge** — deterministic predicates (exit codes, file checks, regex), never LLM-judged
- **Crew** — persistent serial subagents: spawn → send → wait → close/resume, per-agent model override
- **Focus** — `/focus 10` deep-work auto-continuation until goal closes
- **Router / Speculate / Dashboard / Daemon / Healer / Skills / Council** — cost routing,
  prefetch cache, observability, missions, self-healing, safe self-authored tools, adversarial debate
- **v4 engineering** — taint analysis, knowledge graph, real coverage, fuzzing, mutation testing

Risky tools ask inline (`y` / `n` / `a`), `/approve` toggles, `/autonomy 0-5` sets the ladder.

## Keys

| Key | Action |
|---|---|
| `Enter` | send |
| `Esc+Enter` | newline |
| `/` | slash-command menu |
| `Ctrl+T` / `/model` | model selector |
| `Ctrl+E` / `/effort` | effort selector |
| `↑↓` `PgUp` `PgDn` `Tab` `Home` `End` | navigate menus |
| `Ctrl+R` | history search |
| `Ctrl+L` | clear screen |
| `Ctrl+C` | cancel turn / clear input |
| `Ctrl+D` | quit |

## Slash commands

`/model` `/effort` `/help` `/history` `/new` `/save` `/approve` `/reasoning`
`/usage` `/clear` `/about` `/exit` `/state` `/goal` `/autonomy` `/rewind` `/fork`
`/verify` `/memory` `/judge` `/auto` `/prompt` `/mastermind` `/dashboard` `/router`
`/spec` `/recall` `/mission` `/heal` `/skills` `/crew` `/focus` `/render` `/council`
`/analyze` `/graph` `/coverage` `/fuzz` `/mutate` `/forge` `/export` `/forecast` `/health`

## Effort levels

`low` · `medium` · `high` (default) · `extrahigh` · `ultrahigh` — 200k output tokens each
(`config.MAX_TOKENS`), clamped per-provider at send time so requests never get rejected.

## Models & providers

**8 provider keys ship built-in (v3.4.1)** — the app works out of the box with zero setup.
Env vars always win when set:

```bash
# optional overrides — only needed for your own keys:
export OPENCODE_API_KEY=sk-...
# and/or: TOKENROUTER_API_KEY, AGNES_API_KEY, ZENMUX_API_KEY,
#         NVIDIA_API_KEY, BAI_API_KEY, KIOSAPI_API_KEY, XKIRO_API_KEY
```

| Provider | Base URL | Models |
|---|---|---|
| OpenCode Zen | `https://opencode.ai/zen/v1` | mimo-v2.5-free, big-pickle, grok-code-fast-1, claude-sonnet-4-5, claude-opus-4-6, gemini-3.1-pro, gpt-5.2 |
| OpenCode | `https://opencode.ai/zen/v1` | muse-spark-1.2/1.3-contributor-free |
| TokenRouter | `https://api.tokenrouter.com/v1` | qwen3.8-max-free, DeepSeek-V3.2/V4-Pro, Kimi-K2, GLM-5.3-free |
| Agnes | `https://apihub.agnes-ai.com/v1` | agnes-2.5-flash |
| ZenMux | `https://zenmux.ai/api/v1` | dots.OCR note prev |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | deepseek-v4-pro-0813 (1M ctx) |
| B.AI | `https://api.b.ai/v1` | deepseek-v4-flash, qwen3.8-flash, glm-5.3-flash |
| KiosAPI | `https://router.kiosapi.com/v1` | grok-composer-2.5-fast, grok-4.6, muse-spark-1.2-contributor |
| XKiro | `https://api.xkiro.com/v1` | qwen3.7/3.8-max:free, qwen3.7-plus:free, minimax-m2.7-highspeed:free |

> ⚠️ Built-in keys live in git history. For a public fork, replace them with your own
> (`export ...`) and rotate any exposed keys.

## Layout

```
main.py            launcher — python main.py
fullagent/
  config.py        providers, models, effort levels, paths (keys built-in, env overrides)
  systemprompt.py  single source of every system prompt
  client.py        streaming OpenAI-compatible client (SSE, retries, cancel)
  agent.py         agent loop, event-sourced on the kernel
  kernel.py        append-only content-addressed event log
  tui.py           double-line box, overlays, streaming, approvals
  __main__.py      entry point (fullagent --version)
  ...              router, semantic, speculate, dashboard, daemon, healer,
                   skills, council, taint, kgraph, cov, fuzz, mutate, computer/
```

Self-test per module: `python -m fullagent.kernel` (same for `.memory`, `.goal`,
`.judge`, `.crew`, `.router`, ...). Full suite: `python -m unittest discover -s tests`.

## Build the binary

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm fullagent.spec
./dist/fullagent --version   # → 3.4.1
```

CI (`.github/workflows/build-binaries.yml`): push tag `v3.4.1` → selftests →
linux/windows/macOS binaries → GitHub release with all three assets.

---

<div align="center">

**FullAgent v3.4.1** — *Pure Python · Event-Sourced · Self-Healing · Keys Built-In*

`./dist/fullagent` → binary · `~/.fullagent/config.json` → persistence · `FULLAGENT_HOME` → override

</div>
