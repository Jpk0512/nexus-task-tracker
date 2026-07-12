"""Real capability-packet production — R5-T03 N48 (plans/15-r5-dag.yaml).

See `producer.py` for the production entry point (`produce_packet`) and why
it stays separate from `broker.daemon.packet_store`'s serving surface.
"""
from __future__ import annotations

from broker.packets.producer import (
    FROZEN_V1_FIELDS,
    PacketProductionError,
    produce_packet,
)

__all__ = ["FROZEN_V1_FIELDS", "PacketProductionError", "produce_packet"]
