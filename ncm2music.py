#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NCM 音乐格式转换器（命令行批量版，零依赖）
用法:
  python ncm2music.py <文件或目录...>              # 转换到同目录
  python ncm2music.py -o 输出目录 <文件或目录...>   # 转换到指定目录
  python ncm2music.py -r <目录>                    # 递归处理子目录
  python ncm2music.py --no-tags <文件...>           # 不写标签和封面
  python ncm2music.py -m <文件或目录...>            # 成功后删除源 ncm
格式: 网易云 NCM -> MP3 / FLAC（自动识别），写入歌名/艺术家/专辑/封面。
仅供个人已下载音乐的格式转换与备份使用。
"""
import os
import sys
import json
import base64
import argparse
import platform

VERSION = "1.0.0"

# ================= AES-128-ECB（纯实现，FIPS-197 自检） =================
def _gf_mul(a, b):
    p = 0
    while b:
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p

def _make_sboxes():
    inv = bytearray(256)
    for x in range(1, 256):
        for y in range(1, 256):
            if _gf_mul(x, y) == 1:
                inv[x] = y
                break
    s = bytearray(256)
    si = bytearray(256)
    for x in range(256):
        i = inv[x]
        r = 0
        for b in range(8):
            bit = (i >> b) & 1
            nb = bit ^ ((i >> ((b + 4) % 8)) & 1) ^ ((i >> ((b + 5) % 8)) & 1) \
                ^ ((i >> ((b + 6) % 8)) & 1) ^ ((i >> ((b + 7) % 8)) & 1) ^ ((0x63 >> b) & 1)
            r |= nb << b
        s[x] = r
        si[r] = x
    return bytes(s), bytes(si)

_SBOX, _INVSBOX = _make_sboxes()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]

def _expand_key(key):
    w = [list(key[4*i:4*i+4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i-1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[t[0]], _SBOX[t[1]], _SBOX[t[2]], _SBOX[t[3]]]
            t[0] ^= _RCON[i//4 - 1]
        w.append([w[i-4][j] ^ t[j] for j in range(4)])
    return [bytes(b for col in w[4*r:4*r+4] for b in col) for r in range(11)]

def _aes_decrypt_block(roundkeys, src):
    st = bytearray(src)
    def add_rk(k):
        for i in range(16):
            st[i] ^= k[i]
    def inv_shift():
        for r in range(1, 4):
            row = [st[r], st[r+4], st[r+8], st[r+12]]
            for c in range(4):
                st[r + 4*c] = row[(c - r) % 4]
    def inv_sub():
        for i in range(16):
            st[i] = _INVSBOX[st[i]]
    def inv_mix():
        for c in range(4):
            o = 4 * c
            s0, s1, s2, s3 = st[o], st[o+1], st[o+2], st[o+3]
            st[o]   = _gf_mul(s0, 14) ^ _gf_mul(s1, 11) ^ _gf_mul(s2, 13) ^ _gf_mul(s3, 9)
            st[o+1] = _gf_mul(s0, 9)  ^ _gf_mul(s1, 14) ^ _gf_mul(s2, 11) ^ _gf_mul(s3, 13)
            st[o+2] = _gf_mul(s0, 13) ^ _gf_mul(s1, 9)  ^ _gf_mul(s2, 14) ^ _gf_mul(s3, 11)
            st[o+3] = _gf_mul(s0, 11) ^ _gf_mul(s1, 13) ^ _gf_mul(s2, 9)  ^ _gf_mul(s3, 14)
    add_rk(roundkeys[10])
    for r in range(9, 0, -1):
        inv_shift(); inv_sub(); add_rk(roundkeys[r]); inv_mix()
    inv_shift(); inv_sub(); add_rk(roundkeys[0])
    return bytes(st)

def aes_ecb_decrypt(key, data):
    """AES-128-ECB 解密 + C++ 版兼容 unpad（pad>16 视为无 pad）"""
    rks = _expand_key(key)
    if len(data) % 16:
        raise ValueError("AES 输入长度非 16 倍数")
    out = b"".join(_aes_decrypt_block(rks, data[i:i+16]) for i in range(0, len(data), 16))
    if not out:
        return out
    pad = out[-1]
    if pad > 16:
        pad = 0
    return out[:len(out) - pad]

def _selftest():
    key = bytes(range(16))
    out = _aes_decrypt_block(_expand_key(key), bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a"))
    assert out == bytes.fromhex("00112233445566778899aabbccddeeff"), "AES 自检失败"

# ================= NCM 解析 =================
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")  # hzHRAmso5kInbaxW（小写k）
MODIFY_KEYS = [
    bytes.fromhex("2331346C6A6B5F215C5D2630553C2728"),  # taurusxin 版
    bytes.fromhex("2331346C6A6B5F215C5D26302938025E"),  # 旧流传版
]

def build_key_box(key):
    box = list(range(256))
    swap = c = last = koff = 0
    klen = len(key)
    for i in range(256):
        swap = box[i]
        c = (swap + last + key[koff]) & 0xFF
        koff = 0 if koff + 1 >= klen else koff + 1
        box[i] = box[c]
        box[c] = swap
        last = c
    return box

def build_stream(box):
    s = bytearray(256)
    for i in range(256):
        j = (i + 1) & 0xFF
        s[i] = box[(box[j] + box[(box[j] + j) & 0xFF]) & 0xFF]
    return bytes(s)

def parse_ncm_header(buf):
    """返回 (rc4_stream, meta, image, audio_offset)"""
    if buf[:8] != b"CTENFDAM":
        raise ValueError("不是有效的 NCM 文件")
    off = 10
    key_len = int.from_bytes(buf[off:off+4], "little"); off += 4
    if key_len <= 0 or key_len > 0x100000:
        raise ValueError("NCM 头损坏")
    key_data = bytes(b ^ 0x64 for b in buf[off:off+key_len]); off += key_len
    dec = aes_ecb_decrypt(CORE_KEY, key_data)
    if dec[:17] != b"neteasecloudmusic":
        raise ValueError("core key 解密失败（文件可能损坏）")
    rc4_key = dec[17:]
    stream = build_stream(build_key_box(rc4_key))

    meta = None
    meta_len = int.from_bytes(buf[off:off+4], "little"); off += 4
    if 0 < meta_len < 0x100000:
        mod = bytes(b ^ 0x63 for b in buf[off:off+meta_len]); off += meta_len
        b64 = mod[22:]  # 跳过 "163 key(Don't modify):"
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception:
            raw = None
        if raw:
            for mk in MODIFY_KEYS:
                try:
                    txt = aes_ecb_decrypt(mk, raw).decode("utf-8", "ignore")
                    if txt.startswith("music:"):
                        meta = json.loads(txt[6:])
                        break
                except Exception:
                    continue

    off += 5  # crc32(4) + version(1)
    cover_frame_len = int.from_bytes(buf[off:off+4], "little"); off += 4
    img_len = int.from_bytes(buf[off:off+4], "little"); off += 4
    image = None
    if img_len > 0:
        image = bytes(buf[off:off+img_len]); off += img_len
    off += max(0, cover_frame_len - img_len)
    return stream, meta, image, off

def xor_chunk(enc, stream, base_off):
    """分段 XOR：base_off 为本段在音频流内的起始偏移"""
    start = base_off & 0xFF
    ks = (stream * (len(enc) // 256 + 2))[start:start + len(enc)]
    x = int.from_bytes(enc, "little") ^ int.from_bytes(ks, "little")
    return x.to_bytes(len(enc), "little")

# ================= 元数据与标签 =================
def pick_meta(meta):
    if not meta:
        return None
    artists = []
    for a in meta.get("artist") or []:
        if isinstance(a, list) and a:
            first = a[0]
            if isinstance(first, str):
                artists.append(first)
            elif isinstance(first, dict) and isinstance(first.get("name"), str):
                artists.append(first["name"])
        elif isinstance(a, dict) and isinstance(a.get("name"), str):
            artists.append(a["name"])
        elif isinstance(a, str):
            artists.append(a)
    return {
        "title": meta.get("musicName") if isinstance(meta.get("musicName"), str) else "",
        "artist": "、".join(artists),
        "album": meta.get("album") if isinstance(meta.get("album"), str) else "",
    }

def sanitize(s):
    bad = set('\\/:*?"<>|')
    out = "".join("_" if (c in bad or ord(c) < 32) else c for c in (s or ""))
    return out.strip() or "未知"

def mime_of(image):
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"

def build_id3v3(m, image):
    def text_frame(fid, text):
        body = b"\x01\xff\xfe" + text.encode("utf-16-le")
        return fid.encode() + len(body).to_bytes(4, "big") + b"\x00\x00" + body
    frames = b""
    if m.get("title"):
        frames += text_frame("TIT2", m["title"])
    if m.get("artist"):
        frames += text_frame("TPE1", m["artist"])
    if m.get("album"):
        frames += text_frame("TALB", m["album"])
    if image:
        mime = mime_of(image).encode()
        body = b"\x00" + mime + b"\x00\x03\x00" + image
        frames += b"APIC" + len(body).to_bytes(4, "big") + b"\x00\x00" + body
    if not frames:
        return None
    tag_size = len(frames) + 1024
    header = b"ID3\x03\x00\x00" + bytes([
        (tag_size >> 21) & 0x7F, (tag_size >> 14) & 0x7F, (tag_size >> 7) & 0x7F, tag_size & 0x7F])
    return header + frames + b"\x00" * 1024

def build_flac_meta(m, image):
    blocks = b""
    vendor = b"ncm2music"
    fields = []
    for k, v in (("TITLE", m.get("title")), ("ARTIST", m.get("artist")), ("ALBUM", m.get("album"))):
        if v:
            fields.append(f"{k}={v}".encode())
    vc = len(vendor).to_bytes(4, "little") + vendor
    vc += len(fields).to_bytes(4, "little")
    for f in fields:
        vc += len(f).to_bytes(4, "little") + f
    blocks += bytes([4]) + len(vc).to_bytes(3, "big") + vc
    if image:
        mime = mime_of(image).encode()
        pic = (3).to_bytes(4, "big") + len(mime).to_bytes(4, "big") + mime
        pic += (0).to_bytes(4, "big") + b"\x00" * 16 + len(image).to_bytes(4, "big") + image
        blocks += bytes([0x86]) + len(pic).to_bytes(3, "big") + pic
    else:
        blocks = blocks[:1] + blocks[1:]  # 无图时 VORBIS_COMMENT 打 last
        blocks = bytes([0x84]) + blocks[1:]
    return blocks

# ================= 转换主流程 =================
def sniff(first_bytes, meta):
    if first_bytes[:3] == b"ID3":
        return "mp3"
    if len(first_bytes) >= 2 and first_bytes[0] == 0xFF and (first_bytes[1] & 0xE0) == 0xE0:
        return "mp3"
    if first_bytes[:4] == b"fLaC":
        return "flac"
    if first_bytes[:4] == b"OggS":
        return "ogg"
    if isinstance(meta, dict) and isinstance(meta.get("format"), str):
        return meta["format"]
    return None

def convert_file_full(path, out_dir, embed_tags=True, delete_src=False):
    """完整转换：flac 的标签注入需要改写头部，走全量路径"""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(min(size, 2 * 1024 * 1024))
        stream, meta, image, audio_off = parse_ncm_header(head)
        if audio_off > len(head):
            f.seek(0)
            head = f.read(audio_off + 1024)
            stream, meta, image, audio_off = parse_ncm_header(head)

        m = pick_meta(meta)
        base = os.path.splitext(os.path.basename(path))[0]
        os.makedirs(out_dir, exist_ok=True)
        f.seek(audio_off)
        audio = f.read()  # 读入音频部分（NCM 一般在 1~300MB，可接受）
    data = xor_chunk(audio, stream, 0)
    fmt = sniff(data, meta)
    if not fmt:
        raise ValueError("无法识别音频格式（文件可能损坏）")
    if m and m["title"]:
        name = (sanitize(m["artist"]) + " - " + sanitize(m["title"])) if m["artist"] else sanitize(m["title"])
    else:
        name = sanitize(base)
    out_path = os.path.join(out_dir, name + "." + fmt)

    with open(out_path, "wb") as out:
        if embed_tags and m and fmt == "mp3" and data[:3] != b"ID3":
            tag = build_id3v3(m, image)
            if tag:
                out.write(tag)
        elif embed_tags and m and fmt == "flac" and data[:4] == b"fLaC":
            newdata = inject_flac(data, m, image)
            data = newdata if newdata is not None else data
        out.write(data)

    if delete_src and os.path.exists(out_path):
        os.remove(path)
    return out_path, fmt, m

def inject_flac(data, m, image):
    p = 4
    last = 0
    has_tag = has_pic = False
    last_hdr_at = -1
    while p + 4 <= len(data) and not last:
        h = data[p]
        last = h & 0x80
        t = h & 0x7F
        ln = int.from_bytes(data[p+1:p+4], "big")
        if t == 4:
            has_tag = True
        if t == 6:
            has_pic = True
        last_hdr_at = p
        p += 4 + ln
    mm = {k: ("" if has_tag else v) for k, v in m.items()}
    has_new = (not has_tag and (mm.get("title") or mm.get("artist") or mm.get("album"))) or \
              (not has_pic and image)
    if not has_new:
        return None
    meta_block = build_flac_meta(mm, None if has_pic else image)
    head = bytearray(data[:p])
    if last_hdr_at >= 0:
        head[last_hdr_at] &= 0x7F
    return bytes(head) + meta_block + data[p:]

# ================= CLI =================
def human(n):
    return f"{n/1048576:.1f} MB" if n >= 1048576 else f"{n/1024:.1f} KB"

def main():
    _selftest()  # AES 引擎自检，失败立即退出
    ap = argparse.ArgumentParser(description="NCM → MP3/FLAC 批量转换（零依赖）")
    ap.add_argument("targets", nargs="+", help="NCM 文件或包含 NCM 的目录")
    ap.add_argument("-o", "--out", default=None, help="输出目录（默认与源文件同目录）")
    ap.add_argument("-r", "--recursive", action="store_true", help="递归处理子目录")
    ap.add_argument("-m", "--delete-src", action="store_true", help="成功后删除源 NCM 文件")
    ap.add_argument("--no-tags", action="store_true", help="不写入标签和封面")
    args = ap.parse_args()

    files = []
    for t in args.targets:
        if os.path.isdir(t):
            if args.recursive:
                for root, _, names in os.walk(t):
                    for n in names:
                        if n.lower().endswith(".ncm"):
                            files.append(os.path.join(root, n))
            else:
                files.extend(os.path.join(t, n) for n in os.listdir(t) if n.lower().endswith(".ncm"))
        elif os.path.isfile(t):
            files.append(t)
        else:
            print(f"! 跳过不存在路径: {t}")
    files = [f for f in sorted(set(files))]

    if not files:
        print("没有找到 NCM 文件")
        return 1

    print(f"NCM 转换器 v{VERSION} | 共 {len(files)} 个文件 | {'写入标签' if not args.no_tags else '不写标签'}"
          f"{' | 递归' if args.recursive else ''}{' | 删除源文件' if args.delete_src else ''}")
    ok = fail = 0
    for i, p in enumerate(files, 1):
        out_dir = args.out or os.path.dirname(p)
        try:
            out_path, fmt, m = convert_file_full(p, out_dir, embed_tags=not args.no_tags, delete_src=args.delete_src)
            ok += 1
            info = (m["artist"] + " - " + m["title"]) if m and (m.get("artist") or m.get("title")) else ""
            print(f"[{i}/{len(files)}] ✓ {os.path.basename(p)} -> {os.path.basename(out_path)} ({fmt.upper()}, {human(os.path.getsize(out_path))}) {info}")
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(files)}] ✗ {os.path.basename(p)}: {e}")
    print(f"\n完成: 成功 {ok}, 失败 {fail}" + ("" if fail == 0 else "（失败文件已跳过）"))
    return 0 if fail == 0 else 2

if __name__ == "__main__":
    sys.exit(main())
