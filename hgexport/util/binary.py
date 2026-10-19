"""Bounds-checked little-endian reads over an immutable byte buffer."""
from __future__ import annotations

import struct


class FormatError(ValueError):
    """Raised when a file does not match the expected native layout."""


_CACHE: dict[str, struct.Struct] = {}


def _st(fmt: str) -> struct.Struct:
    s = _CACHE.get(fmt)
    if s is None:
        s = _CACHE[fmt] = struct.Struct("<" + fmt)
    return s


class Reader:
    """Random-access reader. Every read is checked against the buffer end
    (or an explicit ``limit``) so corrupt offsets fail loudly instead of
    silently decoding unrelated bytes."""

    __slots__ = ("data", "name", "limit")

    def __init__(self, data: bytes, name: str = "<data>", limit: int | None = None):
        self.data = data
        self.name = name
        self.limit = len(data) if limit is None else limit

    def require(self, offset: int, size: int, what: str = "field") -> None:
        if offset < 0 or size < 0 or offset + size > self.limit:
            raise FormatError(
                f"{self.name}: {what} at 0x{offset:X}+0x{size:X} is outside 0x{self.limit:X} bytes")

    def unpack(self, fmt: str, offset: int, what: str = "field") -> tuple:
        s = _st(fmt)
        self.require(offset, s.size, what)
        return s.unpack_from(self.data, offset)

    def iter_unpack(self, fmt: str, offset: int, count: int, what: str = "array") -> list[tuple]:
        s = _st(fmt)
        self.require(offset, s.size * count, what)
        return [s.unpack_from(self.data, offset + i * s.size) for i in range(count)]

    def u8(self, o: int) -> int:
        self.require(o, 1)
        return self.data[o]

    def u16(self, o: int) -> int:
        return self.unpack("H", o)[0]

    def i16(self, o: int) -> int:
        return self.unpack("h", o)[0]

    def u32(self, o: int) -> int:
        return self.unpack("I", o)[0]

    def i32(self, o: int) -> int:
        return self.unpack("i", o)[0]

    def f32(self, o: int) -> float:
        return self.unpack("f", o)[0]

    def bytes(self, o: int, size: int, what: str = "bytes") -> bytes:
        self.require(o, size, what)
        return self.data[o:o + size]

    def cstring(self, o: int, size: int) -> str:
        raw = self.bytes(o, size).split(b"\0", 1)[0]
        return raw.decode("ascii", "replace")


def align(value: int, boundary: int = 16) -> int:
    return (value + boundary - 1) & ~(boundary - 1)
