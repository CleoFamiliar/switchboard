"""
Switchboard — Human-first multi-repo orchestration for AI coding agents.

Thin orchestration layer on top of beads (bd CLI) for coordinating AI coding
agents across multiple repos with human holds as a first-class primitive.

Key primitives:
- jack: a unit of work (wraps beads issues)
- hold: blocks downstream jacks until an operator (Kale/Cleo) acks
- patch cord: cross-repo dependency relation (triggers_update)
- session: who did what, when (Kale vs Cleo, agent vs human)
- search: semantic search over jack history via Qdrant
"""

__version__ = "0.1.0"
