# Switchboard

**Human-first multi-repo orchestration for AI coding agents.**

Switchboard is a thin Python orchestration layer on top of [beads](https://github.com/gastownhall/beads) for coordinating AI coding agents across multiple repos — with human holds as a first-class primitive.

## What It Is

- **Jack graph via beads** (`bd` CLI) — don't reinvent what already works
- **Holds** block downstream jacks until an operator (Kale or Cleo) explicitly acks them
- **Cross-repo patch cord (dependency) tracking** via `triggers_update` relations
- **Qdrant** for semantic search over jack history and decisions
- **Session tracking** — who did what, when, Kale vs Cleo

## Design Philosophy

- Human oversight by default; agent automation available when earned
- Token-efficient: no always-on supervisors, no redundant state stores
- Single agent by default; parallelism requires explicit intent
- Scale down gracefully: works fine for 1 jack/week with zero overhead

## Usage

```bash
sw status             # summary: open/blocked/holds pending
sw checkpoint ack <id> "decision notes"
sw ready              # jacks with no open blockers
sw update <id>        # update a jack (wraps bd)
sw done <id>          # mark jack done, index in Qdrant
sw search "auth refactor"   # semantic search via Qdrant
sw tree               # patch cord (dependency) tree with status
sw mode set prototype # switch to prototype mode (auto-ack holds with requires:any)
```

## Configuration

See `repos.yaml` for example repo config.

## Architecture

Switchboard wraps `bd` (beads CLI) for the jack graph and adds:
- `hold` jack type with ack workflow
- `triggers_update` patch cord relation for cross-repo artifact deps
- Session tracking (append-only JSONL)
- Qdrant integration for semantic search
- `sw tree` / `sw status` views across all repos
- `sw done` for indexing completion context
- `sw mode` for prototype vs deliberate workflow

It deliberately does **not** include: supervisor agents, automatic merge queue, branch isolation, chat bridge, or Kubernetes.

## Install

```bash
pip install -e .
```

Requires `bd` (beads) in PATH. See [beads](https://github.com/gastownhall/beads) for install.
