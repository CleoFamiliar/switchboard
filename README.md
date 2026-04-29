# Switchboard

**Human-first multi-repo orchestration for AI coding agents.**

Switchboard is a thin Python orchestration layer on top of [beads](https://github.com/gastownhall/beads) for coordinating AI coding agents across multiple repos — with human holds as a first-class primitive.

## What It Is

- **Jack graph via beads** (`bd` CLI) — don't reinvent what already works
- **Holds** block downstream jacks until an operator (Kale or Cleo) explicitly acks them
- **Cross-repo patch cord (dependency) tracking** via `triggers_update` relations
- **Qdrant** for semantic search over jack history and decisions
- **Session tracking** — who did what, when, Kale vs Cleo
- **TSO (Task State Object)** — structured context for resuming jacks mid-stream

## Design Philosophy

- Human oversight by default; agent automation available when earned
- Token-efficient: no always-on supervisors, no redundant state stores
- Single agent by default; parallelism requires explicit intent
- Scale down gracefully: works fine for 1 jack/week with zero overhead

## Install

```bash
pip install -e .
```

Requires `bd` (beads) in PATH. See [beads](https://github.com/gastownhall/beads) for install.

## Usage

```bash
# Workspace overview
sw status                          # summary: open/blocked/holds pending
sw tree                            # patch cord (dependency) tree with status
sw ready                           # jacks with no open blockers

# Working on jacks
sw update <id>                     # update a jack (wraps bd)
sw done <id> -m "msg" -d "diff"    # mark jack done, index in Qdrant

# Holds (checkpoints)
sw checkpoint list                 # list open holds
sw checkpoint ack <id> "notes"     # ack a hold, unblocking downstream

# Context and continuity
sw resume <id>                     # re-enter a jack with structured context (TSO)
sw state set <id> --goal "..."     # update a jack's TSO fields
sw state show <id>                 # view raw TSO YAML

# Sessions
sw session start                   # log session start
sw session end                     # log session end

# Search
sw search "auth refactor"          # semantic search via Qdrant
sw reindex                         # rebuild the Qdrant index

# Workspace mode
sw mode show                       # show current mode
sw mode set prototype              # auto-ack holds with requires:any
sw mode set deliberate             # require explicit human ack (default)
```

## Configuration

See [`repos.yaml`](repos.yaml) for workspace config: registered repos, hold policies, Qdrant settings, and session tracking.

Key settings:

- **`mode`** — `deliberate` (default) or `prototype`. Prototype mode auto-acks holds that allow `requires: any`.
- **`checkpoint_defaults`** — who can ack holds: `kale` (human), `cleo` (agent), or `any`.
- **`qdrant`** — host/port/collection for semantic search.

## Architecture

Switchboard wraps `bd` (beads CLI) for the jack graph and adds:

- `hold` jack type with ack workflow
- `triggers_update` patch cord relation for cross-repo artifact deps
- TSO (Task State Object) for structured jack context and resumption
- Session tracking (append-only JSONL)
- Qdrant integration for semantic search
- `sw tree` / `sw status` views across all repos
- `sw done` for indexing completion context
- `sw mode` for prototype vs deliberate workflow

It deliberately does **not** include: supervisor agents, automatic merge queue, branch isolation, chat bridge, or Kubernetes.

## Terminology

| Term | Meaning |
|------|---------|
| **Jack** | A unit of work (beads issue) |
| **Hold** | A checkpoint requiring human ack before proceeding |
| **Patch cord** | A cross-repo dependency (`triggers_update`) |
| **Kale** | Human operator |
| **Cleo** | AI agent operator |
| **TSO** | Task State Object — structured context for resuming a jack |

## License

See [LICENSE](LICENSE) for details.
