#!/usr/bin/env python3
"""
Sets every player-start entity's "sounds" field (AirQuake repurposes this
as a 10-bit vehicle-availability bitmask -- 6 aircraft in bits 0-5, 4
ground vehicles in bits 6-9; 1023 = all 10 available) to 1023 in a Quake 1
.bsp file, and rewrites the file with correctly adjusted lump offsets
(the entities lump's byte length changes, so every lump after it in the
file's actual layout must shift too -- this is NOT a same-length in-place
text patch).

Usage: patch_vehicle_bitmask.py <input.bsp> <output.bsp>
"""
import re
import struct
import sys

NUM_LUMPS = 15
ENTITIES_LUMP_INDEX = 0

STARTER_CLASSNAMES = ("info_player_deathmatch", "info_player_start")


def patch_entity_text(text: str) -> tuple[str, int]:
    blocks = re.findall(r"\{[^{}]*\}", text)
    patched = 0

    def fix_block(block: str) -> str:
        nonlocal patched
        if not any(f'"classname" "{c}"' in block for c in STARTER_CLASSNAMES):
            return block
        if re.search(r'"sounds"\s+"\d+"', block):
            new_block = re.sub(r'"sounds"\s+"\d+"', '"sounds" "1023"', block)
        else:
            # Insert right after the opening brace.
            new_block = block.replace("{", '{\n"sounds" "1023"', 1)
        if new_block != block:
            patched += 1
        return new_block

    out_parts = []
    pos = 0
    for m in re.finditer(r"\{[^{}]*\}", text):
        out_parts.append(text[pos:m.start()])
        out_parts.append(fix_block(m.group(0)))
        pos = m.end()
    out_parts.append(text[pos:])
    return "".join(out_parts), patched


def patch_bsp(in_path: str, out_path: str) -> int:
    with open(in_path, "rb") as f:
        data = bytearray(f.read())

    version = struct.unpack_from("<i", data, 0)[0]
    lumps = [struct.unpack_from("<ii", data, 4 + i * 8) for i in range(NUM_LUMPS)]

    ent_offset, ent_length = lumps[ENTITIES_LUMP_INDEX]
    raw = bytes(data[ent_offset:ent_offset + ent_length])
    # Entity text is NUL-terminated/padded within its lump; keep whatever
    # trails the parsed text (there usually isn't any, but don't assume).
    if b"\x00" in raw:
        text, trailer = raw.split(b"\x00", 1)
        text = text.decode("latin-1")
    else:
        text, trailer = raw.decode("latin-1"), b""

    new_text, patched = patch_entity_text(text)
    new_raw = new_text.encode("latin-1") + b"\x00" + trailer

    # Rebuild: lay out all 15 lumps in their ORIGINAL relative file order
    # (offsets aren't necessarily index-ordered), substituting the new
    # entities bytes for the old ones, and recompute every lump's offset
    # from scratch as we go.
    order = sorted(range(NUM_LUMPS), key=lambda i: lumps[i][0])
    lump_bytes = {}
    for idx in order:
        off, length = lumps[idx]
        lump_bytes[idx] = new_raw if idx == ENTITIES_LUMP_INDEX else bytes(data[off:off + length])

    header_size = 4 + NUM_LUMPS * 8
    out = bytearray()
    out += struct.pack("<i", version)
    out += b"\x00" * (NUM_LUMPS * 8)  # placeholder, filled in below

    new_lumps = [None] * NUM_LUMPS
    cursor = header_size
    for idx in order:
        b = lump_bytes[idx]
        new_lumps[idx] = (cursor, len(b))
        out += b
        cursor += len(b)

    for i, (off, length) in enumerate(new_lumps):
        struct.pack_into("<ii", out, 4 + i * 8, off, length)

    with open(out_path, "wb") as f:
        f.write(out)

    return patched


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: patch_vehicle_bitmask.py <input.bsp> <output.bsp>", file=sys.stderr)
        raise SystemExit(1)
    n = patch_bsp(sys.argv[1], sys.argv[2])
    print(f"{sys.argv[1]}: patched {n} spawn point(s) -> {sys.argv[2]}")
