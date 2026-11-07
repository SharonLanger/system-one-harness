"""Versioned log envelope and immutable invocation context."""
from dataclasses import dataclass, field
from typing import Any

from .vocabulary import ComponentName, ComponentType, FlowName, HookEvent, LogEvent, LogLevel


@dataclass(frozen=True)
class LogContext:
    session_id: str | None = None
    correlation_id: str | None = None
    hook_event: HookEvent | None = None
    flow_name: FlowName | None = None


@dataclass(frozen=True)
class ComponentRef:
    component_type: ComponentType | None
    component: ComponentName | None


@dataclass(frozen=True)
class LogRecord:
    schema_version: int
    timestamp: str
    level: LogLevel
    message: str
    event: LogEvent
    component_type: ComponentType | None
    component: ComponentName | None
    hook_event: HookEvent | None
    session_id: str | None
    correlation_id: str | None
    flow_name: FlowName | None
    pid: int
    details: dict[str, Any] = field(default_factory=dict)
