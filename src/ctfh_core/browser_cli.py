#!/usr/bin/python3
from __future__ import annotations

import argparse
import json
import socket
import sys

SOCKET_PATH = "/ctfh-browser-rpc/browser.sock"


def call(op: str, args: dict[str, object]) -> dict[str, object]:
    payload = {"op": op, "args": args}
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
        print(result.get("error", "browser service error"), file=sys.stderr)
        raise SystemExit(2)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctfh-browser")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    nav = sub.add_parser("navigate")
    nav.add_argument("url")
    nav.add_argument("--wait", choices=("load", "stable", "idle", "none"), default="load")

    extract = sub.add_parser("extract")
    extract.add_argument("--level", choices=("skeleton", "content", "full"), default="content")
    extract.add_argument("--selector")
    extract.add_argument("--content-boundary", action="store_true")

    click = sub.add_parser("click")
    click.add_argument("ref")
    click.add_argument("--snapshot", choices=("none", "diff", "full"), default="diff")

    typ = sub.add_parser("type")
    typ.add_argument("ref")
    typ.add_argument("text")
    typ.add_argument("--submit", action="store_true")
    typ.add_argument("--snapshot", choices=("none", "diff", "full"), default="diff")

    ev = sub.add_parser("eval")
    ev.add_argument("expr")

    sub.add_parser("url")
    sub.add_parser("back")
    sub.add_parser("forward")
    sub.add_parser("errors")
    sub.add_parser("tabs")
    sub.add_parser("close")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data: dict[str, object] = {}
    if args.command == "navigate":
        data = {"url": args.url, "wait": args.wait}
    elif args.command == "extract":
        data = {
            "level": args.level,
            "selector": args.selector,
            "content_boundary": args.content_boundary,
        }
    elif args.command == "click":
        data = {"ref": args.ref, "snapshot": args.snapshot}
    elif args.command == "type":
        data = {
            "ref": args.ref,
            "text": args.text,
            "submit": args.submit,
            "snapshot": args.snapshot,
        }
    elif args.command == "eval":
        data = {"expr": args.expr}

    result = call(args.command, data)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
