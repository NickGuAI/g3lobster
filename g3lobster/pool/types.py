"""Pool agent types."""

from enum import Enum


class AgentState(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    STUCK = "stuck"
    DEAD = "dead"
    STOPPED = "stopped"
