# Third-party notices

Tandem is licensed under the Apache License 2.0 (see `LICENSE`).

## Copied code

**None.** No source code from other projects is copied into this repository. Every file under
`src/`, `frontend/src/`, `tests/` and `examples/` was written for Tandem. If code from a
permissively licensed project (MIT, Apache-2.0, BSD) is ever adapted, it is listed here with its
copyright line, license and the files it lives in, and the original license text is kept.

## Inspiration

- **Claude Duet** by Anthropic inspired the product idea: one proactive assistant thread with
  check-ins, a morning brief and approvals. Tandem is an independent, unaffiliated project; it uses
  no Anthropic code, logos or marketing copy.
- Ideas (not code) from other open-source projects are credited in `docs/PLAN.md` §4 and
  `docs/SPEC.md` §14: NanoClaw and OpenClaw (MIT), LangGraph executive-ai-assistant and
  agent-inbox (MIT), Letta/MemGPT (Apache-2.0), OpenHands (MIT), Leon (MIT), LangChain Deep Agents
  (MIT), and Open Interpreter and Khoj (AGPL-3.0: ideas only, no code).

## Dependencies

Installed by your package manager, not vendored. Licenses as published by each project.

### Python, always installed

| Package | License |
|---|---|
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| sse-starlette | BSD-3-Clause |
| pydantic, pydantic-settings | MIT |
| SQLAlchemy | MIT |
| httpx | BSD-3-Clause |
| PyYAML | MIT |
| python-multipart | Apache-2.0 |
| python-docx | MIT |
| openpyxl | MIT |
| python-pptx | MIT |
| markdown-it-py | MIT |
| tzdata (Windows only) | Apache-2.0 |

### Python, optional extras

| Extra | Packages | License |
|---|---|---|
| `postgres` | psycopg | LGPL-3.0 (used as an unmodified library) |
| `mcp` | mcp | MIT |
| `caldav` | caldav | Apache-2.0 / GPL-3.0 (dual) |
| `deepagents` | deepagents, langchain, langchain-mcp-adapters, langchain-anthropic, langchain-openai | MIT |
| `claude-sdk` | claude-agent-sdk | MIT (requires the separately licensed Claude Code runtime) |
| `openai-agents` | openai-agents | MIT |

### Frontend

The PWA has no runtime npm dependencies; the Svelte runtime (MIT) is compiled into the bundle in
`src/tandem/server/static/`. Build tools: Svelte, @sveltejs/vite-plugin-svelte, Vite, Vitest
(MIT); TypeScript (Apache-2.0); svelte-check (MIT).

### Container image

Based on `python:3.13-slim` (Debian; see the image's own notices) and `node:22-alpine` for the
build stage only; `uv` (MIT/Apache-2.0) is copied in from `ghcr.io/astral-sh/uv`.
