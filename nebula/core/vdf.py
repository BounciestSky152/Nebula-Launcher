"""Minimal parser for Valve's text KeyValues (VDF/ACF) format."""
from __future__ import annotations

from typing import Any

_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    buf.append(_ESCAPES.get(text[i + 1], text[i + 1]))
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            i += 1
            yield ("s", "".join(buf))
        elif c == "[":  # conditional like [$WIN32] - skip
            j = text.find("]", i)
            i = n if j < 0 else j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            yield ("s", text[i:j])
            i = j


def loads(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack = [root]
    key = None
    for tok in _tokens(text):
        if tok == "{":
            new: dict[str, Any] = {}
            stack[-1][key if key is not None else ""] = new
            stack.append(new)
            key = None
        elif tok == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            value = tok[1]
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    return root


def load(path) -> dict[str, Any]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return loads(fh.read())


def get(d: dict, *keys: str, default: Any = None) -> Any:
    """Case-insensitive nested lookup."""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k in cur:
            cur = cur[k]
            continue
        lk = k.lower()
        for ck, cv in cur.items():
            if ck.lower() == lk:
                cur = cv
                break
        else:
            return default
    return cur
