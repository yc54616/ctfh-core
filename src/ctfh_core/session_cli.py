#!/usr/bin/python3
from __future__ import annotations

import argparse
import json
import socket
import sys

SOCKET_PATH = "/ctfh-session-rpc/session.sock"


def call(payload: dict[str, object]) -> dict[str, object]:
    encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(SOCKET_PATH)
        sock.sendall(encoded)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if chunk.endswith(b"\n"):
                break
    result = json.loads(b"".join(chunks).decode() or "{}")
    if not result.get("ok", False):
        print(result.get("error", "session service error"), file=sys.stderr)
        raise SystemExit(2)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctfh-session")
    sub = parser.add_subparsers(dest="command", required=True)

    op = sub.add_parser("open")
    op.add_argument("--cwd")
    op.add_argument("argv", nargs=argparse.REMAINDER)

    send = sub.add_parser("send")
    send.add_argument("session_id")
    send.add_argument("data")

    read = sub.add_parser("read")
    read.add_argument("session_id")
    read.add_argument("--since", type=int, default=0)
    read.add_argument("--max-bytes", type=int, default=65536)
    read.add_argument("--wait-ms", type=int, default=0)
    read.add_argument("--until-quiescent", action="store_true")

    sig = sub.add_parser("signal")
    sig.add_argument("session_id")
    sig.add_argument("signal", type=int)

    close = sub.add_parser("close")
    close.add_argument("session_id")

    sub.add_parser("list")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "open":
        argv = list(args.argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        result = call({"op": "open", "argv": argv, "cwd": args.cwd})
    elif args.command == "send":
        result = call({
            "op": "send",
            "session_id": args.session_id,
            "data": args.data,
        })
    elif args.command == "read":
        result = call({
            "op": "read",
            "session_id": args.session_id,
            "since_offset": args.since,
            "max_bytes": args.max_bytes,
            "wait_ms": args.wait_ms,
            "until_quiescent": args.until_quiescent,
        })
    elif args.command == "signal":
        result = call({
            "op": "signal",
            "session_id": args.session_id,
            "signal": args.signal,
        })
    elif args.command == "close":
        result = call({"op": "close", "session_id": args.session_id})
    else:
        result = call({"op": "list"})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
