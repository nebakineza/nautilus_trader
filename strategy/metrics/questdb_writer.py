from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass
class QuestDbConfig:
    host: str
    port: int
    enabled: bool

    @classmethod
    def from_env(cls) -> "QuestDbConfig":
        host = os.getenv("QUESTDB_ILP_HOST", "127.0.0.1")
        port = int(os.getenv("QUESTDB_ILP_PORT", "9009"))
        enabled = os.getenv("QUESTDB_ENABLED", "false").lower() == "true"
        return cls(host=host, port=port, enabled=enabled)


class QuestDbILPWriter:
    def __init__(self, cfg: QuestDbConfig, source: str) -> None:
        self.cfg = cfg
        self.source = source
        self._sock: socket.socket | None = None

    @classmethod
    def from_env(cls, source: str) -> "QuestDbILPWriter":
        return cls(QuestDbConfig.from_env(), source)

    def send(
        self,
        table: str,
        tags: dict[str, Any],
        fields: dict[str, Any],
        ts_ns: int | None = None,
    ) -> None:
        if not self.cfg.enabled:
            return

        line = self._format_line(table, tags, fields, ts_ns)
        if line is None:
            return

        try:
            sock = self._get_socket()
            sock.sendall(line)
        except Exception:
            self._close_socket()

    def _format_line(
        self,
        table: str,
        tags: dict[str, Any],
        fields: dict[str, Any],
        ts_ns: int | None,
    ) -> bytes | None:
        if not fields:
            return None

        tag_parts = [f"source={self._escape_tag(self.source)}"]
        for key, value in tags.items():
            if value is None:
                continue
            tag_parts.append(f"{self._escape_tag(key)}={self._escape_tag(value)}")

        field_parts = []
        for key, value in fields.items():
            if value is None:
                continue
            formatted = self._format_field(value)
            if formatted is None:
                continue
            field_parts.append(f"{self._escape_field_key(key)}={formatted}")

        if not field_parts:
            return None

        timestamp = ts_ns if ts_ns is not None else int(time.time() * 1_000_000_000)
        line = f"{table},{','.join(tag_parts)} {','.join(field_parts)} {timestamp}\n"
        return line.encode("utf-8")

    def _format_field(self, value: Any) -> str | None:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return f"{value}i"
        if isinstance(value, Decimal):
            return f"{float(value)}"
        if isinstance(value, float):
            return f"{value}"
        if isinstance(value, str):
            return f'"{value.replace("\"", "\\\"")}"'
        return None

    def _escape_tag(self, value: Any) -> str:
        return str(value).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")

    def _escape_field_key(self, value: Any) -> str:
        return str(value).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")

    def _get_socket(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.create_connection((self.cfg.host, self.cfg.port), timeout=2.0)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return self._sock

    def _close_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
