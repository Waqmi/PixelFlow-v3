"""ONNX Runtime session helpers shared by the local model assistants."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class PersonDetection:
    """One person candidate in original-image pixel coordinates."""

    bbox: tuple[float, float, float, float]
    confidence: float
    keypoints: tuple[tuple[float, float, float], ...] = ()


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ValueError("无效的 ONNX protobuf varint")


def _encode_varint(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _compat_opset_payload(payload: bytes) -> bytes:
    """Downgrade only the default ONNX opset marker for legacy runtimes."""
    fields: list[tuple[int, int, int, int, int, bytes]] = []
    offset = 0
    domain = ""
    version = 0
    while offset < len(payload):
        field_start = offset
        tag, offset = _read_varint(payload, offset)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value_start = offset
            value, offset = _read_varint(payload, offset)
            fields.append((field_start, value_start, offset, number, wire, payload[value_start:offset]))
            if number == 2:
                version = value
        elif wire == 2:
            length_start = offset
            length, content_start = _read_varint(payload, offset)
            offset = content_start + length
            fields.append((field_start, length_start, content_start, number, wire, payload[content_start:offset]))
            if number == 1:
                domain = payload[content_start:offset].decode("utf-8", errors="ignore")
        elif wire == 1:
            offset += 8
            fields.append((field_start, offset - 8, offset, number, wire, payload[offset - 8:offset]))
        elif wire == 5:
            offset += 4
            fields.append((field_start, offset - 4, offset, number, wire, payload[offset - 4:offset]))
        else:
            raise ValueError("不支持的 ONNX protobuf wire 类型")
    if domain not in {"", "ai.onnx"} or version <= 19:
        return payload
    output = bytearray()
    for field_start, value_start, field_end, number, wire, raw_value in fields:
        output.extend(payload[field_start:value_start])
        if number == 2 and wire == 0:
            output.extend(_encode_varint(19))
        else:
            output.extend(payload[value_start:field_end])
    return bytes(output)


def _legacy_onnx_model_bytes(model_path: Path) -> bytes | None:
    """Make an in-memory IR9/opset19 copy for Catalina's ORT 1.16 runtime."""
    data = model_path.read_bytes()
    output = bytearray()
    offset = 0
    changed = False
    while offset < len(data):
        field_start = offset
        tag, offset = _read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value_start = offset
            value, offset = _read_varint(data, offset)
            output.extend(data[field_start:value_start])
            if number == 1 and value == 10:
                output.extend(_encode_varint(9))
                changed = True
            else:
                output.extend(data[value_start:offset])
        elif wire == 2:
            length_start = offset
            length, content_start = _read_varint(data, offset)
            content_end = content_start + length
            content = data[content_start:content_end]
            if number == 8:
                updated = _compat_opset_payload(content)
                changed |= updated != content
                content = updated
            output.extend(data[field_start:content_start])
            output.extend(content)
            offset = content_end
        elif wire == 1:
            output.extend(data[field_start:offset + 8])
            offset += 8
        elif wire == 5:
            output.extend(data[field_start:offset + 4])
            offset += 4
        else:
            raise ValueError("不支持的 ONNX protobuf wire 类型")
    return bytes(output) if changed else None


def _create_onnx_session(model_path: str | Path):
    """Load an external model, with a non-destructive legacy-runtime fallback."""
    import onnxruntime as ort

    # The runtime's telemetry uploader thread can abort the process on exit.
    ort.disable_telemetry_events()
    path = Path(model_path)
    options = ort.SessionOptions()
    # Each source may run in its own worker thread; keep one session from
    # grabbing every core and starving the others.
    options.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 4)
    options.inter_op_num_threads = 1
    try:
        return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    except Exception as original_error:  # noqa: BLE001 - retry only a compatibility copy
        try:
            compatibility_bytes = _legacy_onnx_model_bytes(path)
        except Exception:
            raise original_error
        if compatibility_bytes is None:
            raise original_error
        try:
            return ort.InferenceSession(compatibility_bytes, sess_options=options, providers=["CPUExecutionProvider"])
        except Exception as compatibility_error:  # noqa: BLE001 - preserve both load errors
            raise RuntimeError(f"原始模型加载失败：{original_error}；兼容模式加载失败：{compatibility_error}") from compatibility_error
