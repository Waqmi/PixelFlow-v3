from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """Raised when a Worker message violates the versioned contract."""


@dataclass(frozen=True)
class Message:
    protocol: int
    request_id: str
    type: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "protocol": self.protocol,
                "request_id": self.request_id,
                "type": self.type,
                "payload": self.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> "Message":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProtocolError(f"invalid JSON: {error.msg}") from error
        if not isinstance(data, dict):
            raise ProtocolError("message must be an object")
        if data.get("protocol") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported protocol version")
        request_id = data.get("request_id")
        message_type = data.get("type")
        payload = data.get("payload")
        if not isinstance(request_id, str) or not request_id:
            raise ProtocolError("request_id must be a non-empty string")
        if not isinstance(message_type, str) or not message_type:
            raise ProtocolError("type must be a non-empty string")
        if not isinstance(payload, dict):
            raise ProtocolError("payload must be an object")
        return cls(PROTOCOL_VERSION, request_id, message_type, payload)

