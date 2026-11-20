import argparse
import re
import time
from pathlib import Path

import grpc


RUNNING_DIR = Path.home() / "AppData/Local/Temp/avd/running"
SERVICE = "/android.emulation.control.EmulatorController/"


def varint(value: int) -> bytes:
    output = bytearray()
    while value > 0x7F:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def field_varint(number: int, value: int) -> bytes:
    return varint(number << 3) + varint(value)


def field_bytes(number: int, value: bytes) -> bytes:
    return varint((number << 3) | 2) + varint(len(value)) + value


def runtime() -> tuple[grpc.Channel, tuple[tuple[str, str], ...]]:
    candidates = sorted(RUNNING_DIR.glob("pid_*.ini"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("No running emulator metadata found")
    content = candidates[0].read_text(encoding="utf-8")
    token = re.search(r"^grpc\.token=(.+)$", content, re.MULTILINE)
    port = re.search(r"^grpc\.port=(\d+)$", content, re.MULTILINE)
    if not token or not port:
        raise RuntimeError("Emulator gRPC metadata is incomplete")
    channel = grpc.insecure_channel(f"127.0.0.1:{port.group(1)}")
    return channel, (("authorization", f"Bearer {token.group(1)}"),)


def call(method: str, payload: bytes = b"") -> bytes:
    channel, metadata = runtime()
    rpc = channel.unary_unary(
        SERVICE + method,
        request_serializer=lambda value: value,
        response_deserializer=lambda value: value,
    )
    return rpc(payload, metadata=metadata, timeout=10)


def key(name: str) -> None:
    payload = field_varint(2, 2) + field_bytes(4, name.encode("utf-8"))
    call("sendKey", payload)


def touch(x: int, y: int, pressure: int) -> None:
    point = field_varint(1, x) + field_varint(2, y) + field_varint(4, pressure)
    call("sendTouch", field_bytes(1, point))


def tap(x: int, y: int) -> None:
    touch(x, y, 1)
    time.sleep(0.08)
    touch(x, y, 0)


def swipe(x1: int, y1: int, x2: int, y2: int, steps: int = 12) -> None:
    touch(x1, y1, 1)
    for step in range(1, steps + 1):
        x = x1 + (x2 - x1) * step // steps
        y = y1 + (y2 - y1) * step // steps
        touch(x, y, 1)
        time.sleep(0.02)
    touch(x2, y2, 0)


def extract_length_field(payload: bytes, target: int) -> bytes:
    index = 0
    while index < len(payload):
        key_value = 0
        shift = 0
        while True:
            current = payload[index]
            index += 1
            key_value |= (current & 0x7F) << shift
            if current < 0x80:
                break
            shift += 7
        field = key_value >> 3
        wire = key_value & 7
        if wire == 0:
            while payload[index] & 0x80:
                index += 1
            index += 1
            continue
        if wire != 2:
            raise RuntimeError(f"Unsupported protobuf wire type: {wire}")
        length = 0
        shift = 0
        while True:
            current = payload[index]
            index += 1
            length |= (current & 0x7F) << shift
            if current < 0x80:
                break
            shift += 7
        value = payload[index : index + length]
        index += length
        if field == target:
            return value
    raise RuntimeError(f"Field {target} not found")


def screenshot(path: Path) -> None:
    path.write_bytes(extract_length_field(call("getScreenshot"), 4))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    key_parser = subparsers.add_parser("key")
    key_parser.add_argument("name")
    tap_parser = subparsers.add_parser("tap")
    tap_parser.add_argument("x", type=int)
    tap_parser.add_argument("y", type=int)
    swipe_parser = subparsers.add_parser("swipe")
    swipe_parser.add_argument("x1", type=int)
    swipe_parser.add_argument("y1", type=int)
    swipe_parser.add_argument("x2", type=int)
    swipe_parser.add_argument("y2", type=int)
    screenshot_parser = subparsers.add_parser("screenshot")
    screenshot_parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "key":
        key(args.name)
    elif args.command == "tap":
        tap(args.x, args.y)
    elif args.command == "swipe":
        swipe(args.x1, args.y1, args.x2, args.y2)
    elif args.command == "screenshot":
        screenshot(args.path)


if __name__ == "__main__":
    main()
