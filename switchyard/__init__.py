"""
Switchyard — Human-first multi-repo orchestration for AI coding agents.

Thin orchestration layer on top of beads (bd CLI) for coordinating AI coding
agents across multiple repos with human checkpoints first-class.

Key primitives:
- checkpoint: blocks downstream work until human/agent acks
- triggers_update: cross-repo dependency relation
- session: who did what, when (Kale vs Cleo, agent vs human)
- search: semantic search over task history via Qdrant
"""

__version__ = "0.1.0"
