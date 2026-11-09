"""Provisional resource limits, injectable in tests; times are cooperative."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Limits:
    request_bytes: int = 64 * 1024
    block_bytes: int = 64 * 1024
    advance_bytes: int = 32 * 1024 * 1024
    advance_seconds: float = .100
    line_bytes: int = 4 * 1024 * 1024
    detail_bytes: int = 8 * 1024 * 1024
    response_bytes: int = 256 * 1024
    inventory_entries: int = 100_000
    inventory_bytes: int = 32 * 1024 * 1024
    diagnostic_entries: int = 50
    diagnostic_preview_bytes: int = 16 * 1024
    handles: int = 1000
    active_queries: int = 4
    idle_seconds: int = 900
    message_preview: int = 256
    field_preview: int = 64
    client_pages: int = 5
