from __future__ import annotations

import sys

from .contracts import Message, ProtocolError, PROTOCOL_VERSION


SUPPORTED_REQUESTS = {
    "ping",
    "probe_models",
    "classify_materials",
    "start_generation",
    "cancel_generation",
    "retry_failed",
    "clear_cache",
    "shutdown",
}


def _event(request_id: str, event_type: str, payload: dict) -> str:
    return Message(PROTOCOL_VERSION, request_id, event_type, payload).to_json()


def handle_message(message: Message) -> tuple[str, bool]:
    """Handle the protocol shell; image logic is added after v2.6 baselining."""
    if message.type not in SUPPORTED_REQUESTS:
        return _event(message.request_id, "error", {"code": "unknown_request", "message": message.type}), False
    if message.type == "shutdown":
        return _event(message.request_id, "done", {"action": "shutdown"}), True
    if message.type == "ping":
        return _event(message.request_id, "ready", {"worker": "pixelflow-v3", "protocol": PROTOCOL_VERSION}), False
    return _event(
        message.request_id,
        "status",
        {"state": "scaffold", "message": "v2.6 core integration is pending baseline completion"},
    ), False


def main() -> int:
    print(_event("worker", "ready", {"worker": "pixelflow-v3", "protocol": PROTOCOL_VERSION}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = Message.from_json(line)
            response, should_exit = handle_message(message)
        except ProtocolError as error:
            response = _event("unknown", "error", {"code": "invalid_message", "message": str(error)})
            should_exit = False
        print(response, flush=True)
        if should_exit:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

