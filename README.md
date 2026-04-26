# Switchyard

**Human-first multi-repo orchestration for AI coding agents.**

Switchyard is a thin Python orchestration layer on top of [beads](https://github.com/gastownhall/beads) for coordinating AI coding agents across multiple repos — with human checkpoints as a first-class primitive.

## What It Is

- **Task graph via beads** (`bd` CLI) — don't reinvent what already works
- **Checkpoints** block downstream work until a human (or Cleo) explicitly acks them
- **Cross-repo dependency tracking** via `triggers_update` relations
- **Qdrant** for semantic search over task history and decisions
- **Session tracking** — who did what, when, Kale vs Cleo

## Design Philosophy

- Human oversight by default; agent automation available when earned
- Token-efficient: no always-on supervisors, no redundant state stores
- Single agent by default; parallelism requires explicit intent
- Scale down gracefully: works fine for 1 task/week with zero overhead

## Usage

```bash
sw status             # summary: open/blocked/checkpoints pending
sw checkpoint ack <id> "decision notes"
sw ready              # tasks with no open blockers
sw update <id>        # update a task (wraps bd)
sw search "auth refactor"   # semantic search via Qdrant
sw tree               # dependency tree with status
```

## Configuration

See `repos.yaml` for example repo config.

## Architecture

Switchyard wraps `bd` (beads CLI) for the task graph and adds:
- `checkpoint` task type with ack workflow
- `triggers_update` dep relation for cross-repo artifact deps
- Session tracking (append-only JSONL)
- Qdrant integration for semantic search
- `sw tree` / `sw status` views across all repos

It deliberately does **not** include: supervisor agents, automatic merge queue, branch isolation, chat bridge, or Kubernetes.

## Install

```bash
pip install -e .
```

Requires `bd` (beads) in PATH. See [beads](https://github.com/gastownhall/beads) for install.
