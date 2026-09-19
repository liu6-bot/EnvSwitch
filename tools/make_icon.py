#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成应用图标（icon.png / icon.ico）。

纯标准库实现：逐像素画一个「两个反向箭头」的切换标志，再手写 PNG 与 ICO 容器。
这样仓库里不用塞二进制图片，也不用为了生成图标装 Pillow。

    python tools/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "assets"
SIZES = (256, 128, 64, 48, 32, 16)
SS = 3  # 每像素超采样次数（抗锯齿）


# --------------------------------------------------------------------------- #
# 绘图
# --------------------------------------------------------------------------- #

def rounded_rect(u: float, v: float, r: float = 0.24) -> float:
    """返回 (u, v) 到圆角矩形内部的近似有符号距离（<=0 表示在内部）。"""
    cx = min(max(u, r), 1.0 - r)
    cy = min(max(v, r), 1.0 - r)
    return ((u - cx) ** 2 + (v - cy) ** 2) ** 0.5 - r


def arrow(u: float, v: float, x0: float, x1: float, yc: float,
          thick: float, head_len: float, head_h: float, right: bool) -> float:
    """一条带三角形箭头的横条。返回 <=0 表示在形状内。"""
    if not right:
        u = 1.0 - u
    # 箭杆
    if x0 <= u <= x1 and abs(v - yc) <= thick / 2:
        return -1.0
    # 箭头（三角形）
    if x1 < u <= x1 + head_len:
        t = (x1 + head_len - u) / head_len
        return abs(v - yc) - (head_h / 2) * t
    return 1.0


def coverage(dist: float) -> float:
    """把距离映射成 0~1 的覆盖率（边缘柔化一像素）。"""
    if dist <= -0.006:
        return 1.0
    if dist >= 0.006:
        return 0.0
    return (0.006 - dist) / 0.012


def render(size: int) -> bytes:
    """渲染 size×size 的 RGBA 像素数据（按行排列）。"""
    top = (0x3B, 0x82, 0xF6)     # 渐变起点
    bottom = (0x1E, 0x3A, 0x8A)  # 渐变终点
    white = (0xFF, 0xFF, 0xFF)

    rows = bytearray()
    step = 1.0 / (size * SS)
    for py in range(size):
        row = bytearray()
        for px in range(size):
            ar = ag = ab = aa = 0.0
            n = 0
            for sy in range(SS):
                for sx in range(SS):
                    u = (px * SS + sx + 0.5) * step
                    v = (py * SS + sy + 0.5) * step
                    n += 1

                    # 背景圆角矩形
                    a_bg = coverage(rounded_rect(u, v))
                    # 前景箭头
                    d1 = arrow(u, v, 0.16, 0.60, 0.36, 0.11, 0.24, 0.30, True)
                    d2 = arrow(u, v, 0.16, 0.60, 0.64, 0.11, 0.24, 0.30, False)
                    a_fg = max(coverage(d1), coverage(d2)) * a_bg

                    if a_bg <= 0:
                        ar += 0; ag += 0; ab += 0; aa += 0
                        continue

                    r = top[0] + (bottom[0] - top[0]) * v
                    g = top[1] + (bottom[1] - top[1]) * v
                    b = top[2] + (bottom[2] - top[2]) * v
                    # 前景覆盖到背景上
                    r = r * (1 - a_fg) + white[0] * a_fg
                    g = g * (1 - a_fg) + white[1] * a_fg
                    b = b * (1 - a_fg) + white[2] * a_fg

                    ar += r; ag += g; ab += b; aa += a_bg
            if n:
                ar /= n; ag /= n; ab /= n; aa /= n
            row += bytes((int(ar), int(ag), int(ab), int(aa * 255)))
        rows += b"\x00" + bytes(row)   # PNG 每行前面加一个 filter 类型字节
    return bytes(rows)


# --------------------------------------------------------------------------- #
# PNG / ICO 封装
# --------------------------------------------------------------------------- #

def png_bytes(size: int) -> bytes:
    raw = render(size)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico_bytes(sizes=SIZES) -> bytes:
    images = [(s, png_bytes(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, data in images:
        entries += struct.pack("<BBBBHHII",
                               s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png = OUT_DIR / "icon.png"
    ico = OUT_DIR / "icon.ico"
    png.write_bytes(png_bytes(256))
    ico.write_bytes(ico_bytes())
    print(f"已生成 {png} ({png.stat().st_size} B)")
    print(f"已生成 {ico} ({ico.stat().st_size} B)")


if __name__ == "__main__":
    main()
