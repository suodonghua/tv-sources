#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 tvbox.json 生成镜像备用配置 tvbox_fallback.json。

背景：raw.githubusercontent.com 在国内会被间歇性 TCP 阻断。TVBox 若填写
ghfast.top 之类的镜像地址，配置里的相对路径（./jar/x.jar、./list.txt）在
镜像域名下解析会错位，导致 spider 拉不到、站点全空。

所以备用配置把 spider / lives / site.jar 全部改写成「镜像 + 绝对 raw 地址」，
这样配置本体和所有依赖都走同一条镜像通道。

用法：
    python make_fallback.py            # 就地生成 tvbox_fallback.json
    MIRROR=https://gh-proxy.com/ python make_fallback.py
"""
import json
import os
import sys

REPO = os.environ.get("GITHUB_REPOSITORY", "yhc7scorpio/tv-sources")
REF = os.environ.get("GITHUB_REF_NAME", "main")
MIRROR = os.environ.get("MIRROR", "https://ghfast.top/")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{REF}/"

SRC = "tvbox.json"
DST = "tvbox_fallback.json"


def to_mirror(p):
    """相对路径 -> 镜像绝对地址；已带 http 的原样返回。保留 `;md5;xxx` 后缀。"""
    if not isinstance(p, str) or not p.strip():
        return p
    head = p.split(";")[0].strip()
    tail = p[len(head):]
    if head.lower().startswith(("http://", "https://")):
        return p
    rel = head.lstrip("./")
    return MIRROR + RAW_BASE + rel + tail


def main():
    if not os.path.exists(SRC):
        sys.exit(f"找不到 {SRC}，请先生成主配置")
    with open(SRC, encoding="utf-8") as f:
        d = json.load(f)

    changed = []
    if d.get("spider"):
        before = d["spider"]
        d["spider"] = to_mirror(before)
        if d["spider"] != before:
            changed.append(f"spider -> {d['spider']}")

    lives = d.get("lives")
    if isinstance(lives, str):
        d["lives"] = to_mirror(lives)
        changed.append("lives(str) 已改写")
    elif isinstance(lives, list):
        for lv in lives:
            if isinstance(lv, dict) and lv.get("url"):
                lv["url"] = to_mirror(lv["url"])
        changed.append(f"lives x{len(lives)} 已改写")

    n = 0
    for s in d.get("sites") or []:
        if isinstance(s.get("jar"), str) and not s["jar"].startswith("http"):
            s["jar"] = to_mirror(s["jar"])
            n += 1
    if n:
        changed.append(f"site.jar x{n} 已改写")

    d["_fallback_note"] = f"由 {REPO}@{REF} 自动生成，全部依赖走 {MIRROR}；主用请填 tvbox.json"
    with open(DST, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)

    print(f"已生成 {DST}  sites={len(d.get('sites') or [])}")
    for c in changed:
        print("  -", c)


if __name__ == "__main__":
    main()
