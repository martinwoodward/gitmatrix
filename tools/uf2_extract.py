#!/usr/bin/env python3
"""Unpack a Contribution Widget UF2 into its flash regions and files.

    python3 tools/uf2_extract.py firmware/contribution_widget-micropython-1.29-with-filesystem.uf2 /tmp/out

Writes one region_<addr>.bin per contiguous flash range, unpacks the ROMFS
(0x10200000) into rom/, and, if littlefs-python is installed, unpacks the
LittleFS user filesystem (0x10300000) into fs/.
"""

import os
import struct
import sys

UF2_MAGIC = (0x0A324655, 0x9E5D5157)
ROMFS_ADDR = 0x10200000
LFS_ADDR = 0x10300000
LFS_END = 0x11000000
LFS_BLOCK = 4096


def read_regions(path):
    data = open(path, "rb").read()
    blocks = []
    for i in range(0, len(data), 512):
        m0, m1, _flags, addr, size = struct.unpack_from("<5I", data, i)
        if (m0, m1) != UF2_MAGIC:
            raise ValueError(f"bad UF2 block at offset {i}")
        blocks.append((addr, data[i + 32:i + 32 + size]))
    blocks.sort()
    regions = []
    for addr, chunk in blocks:
        if regions and regions[-1][0] + len(regions[-1][1]) == addr:
            regions[-1][1].extend(chunk)
        else:
            regions.append((addr, bytearray(chunk)))
    return regions


def vuint(b, i):
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i


def unpack_romfs(b, out):
    # MicroPython ROMFS: records of (kind, length, payload); 4 = dir, 5 = file,
    # 2/3 = file data inside a file record.
    kind, i = vuint(b, 0)
    if kind != 0x14A6B1:
        raise ValueError("not a ROMFS image")
    length, i = vuint(b, i)

    def walk(i, end, path):
        while i < end:
            kind, i = vuint(b, i)
            length, i = vuint(b, i)
            payload_end = i + length
            if kind in (4, 5):
                nlen, j = vuint(b, i)
                name = os.path.join(path, b[j:j + nlen].decode())
                j += nlen
                if kind == 4:
                    os.makedirs(name, exist_ok=True)
                    walk(j, payload_end, name)
                else:
                    while j < payload_end:
                        k, j = vuint(b, j)
                        n, j = vuint(b, j)
                        if k in (2, 3):
                            with open(name, "wb") as f:
                                f.write(b[j:j + n])
                            print(name, n)
                        j += n
            i = payload_end

    os.makedirs(out, exist_ok=True)
    walk(i, i + length, out)


def unpack_littlefs(b, out):
    try:
        from littlefs import LittleFS
    except ImportError:
        print("littlefs-python not installed; skipping user filesystem (pip install littlefs-python)")
        return
    block_count = (LFS_END - LFS_ADDR) // LFS_BLOCK
    image = bytearray(b) + b"\xff" * (block_count * LFS_BLOCK - len(b))
    fs = LittleFS(block_size=LFS_BLOCK, block_count=block_count, mount=False)
    fs.context.buffer = image
    fs.mount()
    for root, dirs, files in fs.walk("/"):
        for d in dirs:
            os.makedirs(out + root.rstrip("/") + "/" + d, exist_ok=True)
        for name in files:
            p = root.rstrip("/") + "/" + name
            with fs.open(p, "rb") as f:
                data = f.read()
            os.makedirs(os.path.dirname(out + p), exist_ok=True)
            with open(out + p, "wb") as f:
                f.write(data)
            print(out + p, len(data))


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = sys.argv[1:]
    os.makedirs(out, exist_ok=True)
    for addr, data in read_regions(src):
        print(f"region 0x{addr:08x}-0x{addr + len(data):08x} ({len(data) // 1024} KB)")
        with open(os.path.join(out, f"region_{addr:08x}.bin"), "wb") as f:
            f.write(data)
        if addr == ROMFS_ADDR:
            unpack_romfs(data, os.path.join(out, "rom"))
        elif addr == LFS_ADDR:
            unpack_littlefs(data, os.path.join(out, "fs"))


if __name__ == "__main__":
    main()
