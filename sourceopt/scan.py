#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源择优引擎：扫描候选 GitHub 仓库 -> 实测验证 -> 综合打分 -> 选出最优源 -> 镜像到 out/

只依赖 Python 标准库，PC 与 GitHub Actions 均可直接跑。
用法：
    python scan.py            # 完整扫描
    python scan.py --quick    # 跳过播放连通性抽样
    python scan.py --json     # 只输出 JSON 报告
"""
import json
import math
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
OUT_DIR = os.path.join(HERE, "out")

CFG = json.load(open(CFG_PATH, encoding="utf-8"))
STATE_PATH = os.path.join(HERE, "state.json")
SWITCH_MARGIN = float(CFG.get("switch_margin", 0.15))
FORCE_SWITCH = os.environ.get("FORCE_SWITCH") == "1"
NET = CFG["net"]
W = CFG["weights"]
TH = CFG["thresholds"]
BLOCK = [k.lower() for k in CFG["blocklist_keywords"]]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def log(msg):
    if not QUIET:
        print(msg, flush=True)


QUIET = "--json" in sys.argv
QUICK = "--quick" in sys.argv
ONLY = None
if "--only" in sys.argv:
    i = sys.argv.index("--only")
    if i + 1 < len(sys.argv):
        ONLY = sys.argv[i + 1].lower()


def http_get(url, timeout=None, binary=False, allow_proxy=True):
    """带 UA / 超时 / 代理开关的 GET，返回 (status, bytes)"""
    timeout = timeout or NET["timeout"]
    req = urllib.request.Request(url, headers={"User-Agent": NET["ua"]})
    handler = urllib.request.ProxyHandler({}) if not allow_proxy else urllib.request.ProxyHandler()
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


CACHE_DIR = os.path.join(HERE, "cache")
CACHE_TTL = 12 * 3600   # 12 小时内复用，省 API 配额
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def cache_path(key):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:150]
    return os.path.join(CACHE_DIR, safe + ".json")


def api(path, ttl=CACHE_TTL):
    """GitHub API，带本地缓存 + token + 代理降级"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = cache_path(path)
    if ttl and os.path.exists(cp) and time.time() - os.path.getmtime(cp) < ttl:
        try:
            return json.load(open(cp, encoding="utf-8"))
        except Exception:
            pass
    url = NET["api_base"] + path
    headers = {"User-Agent": NET["ua"], "Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    for allow_proxy in (False, True):
        try:
            req = urllib.request.Request(url, headers=headers)
            handler = urllib.request.ProxyHandler({}) if not allow_proxy else urllib.request.ProxyHandler()
            opener = urllib.request.build_opener(handler)
            with opener.open(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            return data
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                log("    !! API 限速或拒绝" + ("（建议设置 GITHUB_TOKEN）" if not TOKEN else ""))
                return None
            if e.code == 404:
                return None
        except Exception:
            continue
    return None


def search_repo(full_name):
    """用 Search API 拿单仓元数据（配额独立于 core，10/min），core 限速时的兜底"""
    d = api("/search/repositories?q=repo:" + urllib.parse.quote(full_name, safe="/"))
    if isinstance(d, dict) and d.get("items"):
        return d["items"][0]
    return None


def repo_meta(full_name):
    return api(f"/repos/{full_name}") or search_repo(full_name)


def raw_url(repo, branch, path):
    """生成候选 raw 地址（镜像 + 直连），路径做 URL 编码以支持中文文件名"""
    safe_path = urllib.parse.quote(path, safe="/")
    tail = f"{repo}/{branch}/{safe_path}"
    mirrors = list(NET["raw_mirrors"])
    if os.environ.get("RAW_DIRECT"):      # GitHub Actions 环境优先直连
        mirrors.reverse()
    return [m + "https://raw.githubusercontent.com/" + tail for m in mirrors]


def fetch_raw(repo, branch, path, timeout=None):
    """按镜像顺序拉取文件，返回 (bytes, 实际使用的 url)"""
    for url in raw_url(repo, branch, path):
        st, data = http_get(url, timeout=timeout, binary=True)
        if st == 200 and data:
            return data, url
    return None, None


def days_since(iso):
    if not iso:
        return 9999
    try:
        t = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).days
    except Exception:
        return 9999


def blocked(text):
    low = str(text).lower()
    return any(k in low for k in BLOCK)


# ---------------- 候选文件发现 ----------------

MOVIE_NAME_HINT = re.compile(r"(api|config|tvbox|接口|影视|仓|\.json$)", re.I)
LIVE_NAME_HINT = re.compile(r"(\.m3u8?$|\.txt$)", re.I)

SKIP_DIRS = {".git", "node_modules", "jar", "jars", "image", "images", "img", "pic",
             "pics", "screenshot", "screenshots", "docs", "doc", ".github", "js", "lib"}


def list_dir(repo, branch, path=""):
    d = api(f"/repos/{repo}/contents/{path}?ref={branch}")
    if not isinstance(d, list):
        return []
    return d


def walk_files(repo, branch, max_depth=2, max_dirs=9):
    """优先用 Trees API 一次拿全仓库文件树（省配额），失败退回逐层列目录"""
    tree = api(f"/repos/{repo}/git/trees/{urllib.parse.quote(branch, safe='')}?recursive=1")
    if isinstance(tree, dict) and isinstance(tree.get("tree"), list):
        files = [(it["path"], it.get("size") or 0)
                 for it in tree["tree"] if it.get("type") == "blob" and it.get("path")]
        log(f"    trees: {len(files)} 个文件")
        return files
    files, queue, used = [], [("", 0)], 0
    while queue and used < max_dirs:
        path, depth = queue.pop(0)
        items = list_dir(repo, branch, path)
        used += 1
        for it in items:
            if it.get("type") == "file":
                files.append((it["path"], it.get("size", 0)))
            elif it.get("type") == "dir" and depth < max_depth:
                if it["name"].lower() in SKIP_DIRS:
                    continue
                queue.append((it["path"], depth + 1))
    return files


def discover_movie_files(repo, branch):
    """找 TVBox 配置 JSON（根目录优先，其次浅层子目录）"""
    files = walk_files(repo, branch, max_depth=2, max_dirs=8)
    cands = [(p, s) for p, s in files
             if p.lower().endswith(".json") and not blocked(p) and 1500 <= s <= 3_000_000]
    def rank(item):
        p, s = item
        depth = p.count("/")
        named = 0 if re.search(r"(api|config|tvbox|接口|0826|0825|0827|json)", p, re.I) else 1
        return (named, depth, s)
    cands.sort(key=rank)
    return [p for p, _ in cands[:6]]


def discover_live_files(repo, branch):
    """找直播源文件（m3u/m3u8/txt）"""
    files = walk_files(repo, branch, max_depth=2, max_dirs=9)
    cands = [(p, s) for p, s in files
             if LIVE_NAME_HINT.search(p.lower()) and not blocked(p) and 800 <= s <= 8_000_000]
    # 名字里带 live/iptv/tv/m3u 的优先，然后按体积（频道多）
    def rank(item):
        p, s = item
        hint = 0 if re.search(r"(live|iptv|tv|m3u|result|best)", p, re.I) else 1
        return (hint, -s)
    cands.sort(key=rank)
    return [p for p, _ in cands[:6]]


# ---------------- 验证与打分 ----------------

def score_fresh(days, max_age):
    if days > max_age:
        return 0.0
    return max(0.0, 1.0 - days / max_age)


def score_community(star, fork):
    s = min(1.0, math.log10(1 + star) / 4.5)   # 3万 star ≈ 1.0
    f = min(1.0, math.log10(1 + fork) / 4.0)   # 1万 fork ≈ 1.0
    return 0.6 * s + 0.4 * f


def verify_movie(repo, branch, path):
    data, url = fetch_raw(repo, branch, path)
    if not data:
        return None, "拉取失败"
    try:
        cfg = json.loads(data.decode("utf-8", "ignore"))
    except Exception as e:
        return None, f"JSON 解析失败: {e}"
    if not isinstance(cfg, dict):
        return None, "不是对象"

    if "sites" in cfg and isinstance(cfg["sites"], list):
        sites = cfg["sites"]
        if len(sites) < TH["movie_min_sites"]:
            return None, f"站点太少({len(sites)})"
        bad = [s.get("name", "") for s in sites if blocked(s.get("name", "")) or blocked(s.get("key", ""))]
        if len(bad) > max(2, len(sites) * 0.05):
            return None, f"含违规站点 {len(bad)} 个"
        types = {}
        for s in sites:
            types[s.get("type")] = types.get(s.get("type"), 0) + 1
        spider = str(cfg.get("spider", ""))
        spider_ok = 0.0
        if spider:
            if spider.startswith("http"):
                st, _ = http_get(spider, timeout=12)
                spider_ok = 1.0 if st == 200 else 0.3
            else:
                # 相对路径，按配置所在位置解析
                base = path.rsplit("/", 1)[0] if "/" in path else ""
                rel = spider.split(";")[0].lstrip("./")
                rel = os.path.normpath(os.path.join(base, rel)).replace("\\", "/")
                _, surl = fetch_raw(repo, branch, rel, timeout=20)
                spider_ok = 1.0 if surl else 0.2
        return {
            "kind": "movie", "repo": repo, "branch": branch, "path": path,
            "url": url, "form": "single",
            "sites": len(sites), "types": types,
            "lives": len(cfg.get("lives") or []),
            "spider": spider, "spider_ok": spider_ok,
            "size": len(data), "_raw": data,
        }, None

    if "urls" in cfg and isinstance(cfg["urls"], list):
        urls = cfg["urls"]
        if not urls:
            return None, "多仓为空"
        # 递归验证子仓：合并可用子仓的站点数，避免"空壳多仓"拿高分
        ok_children, total_sites, child_info = 0, 0, []
        for u in urls[:5]:
            child = u.get("url") if isinstance(u, dict) else str(u)
            if not child or not child.startswith("http"):
                continue
            st, body = http_get(child, timeout=15, binary=True)
            if st != 200 or not body:
                continue
            try:
                cc = json.loads(body.decode("utf-8", "ignore"))
            except Exception:
                continue
            csites = cc.get("sites") if isinstance(cc, dict) else None
            if isinstance(csites, list) and len(csites) >= 5:
                ok_children += 1
                total_sites += len(csites)
                child_info.append(f"{u.get('name', '?')[:10]}:{len(csites)}")
        if ok_children == 0:
            return None, f"多仓 {len(urls)} 个子仓全部拉不到内容"
        return {
            "kind": "movie", "repo": repo, "branch": branch, "path": path,
            "url": url, "form": "multi", "urls": len(urls),
            "ok_children": ok_children, "sites": total_sites,
            "types": {}, "lives": 0, "spider": "", "spider_ok": 0.5,
            "children": child_info[:5],
            "size": len(data), "_raw": data,
        }, None

    return None, "既无 sites 也无 urls"


M3U_LINE = re.compile(r"^#EXTINF:.*?,(.*)$", re.M)


def probe_url(u, timeout):
    """探测单条流是否可连（只连不下载）"""
    try:
        p = urllib.parse.urlparse(u)
        if p.scheme not in ("http", "https"):
            return None  # 非 http 协议不判（rtp/rtsp/udp 等）
        host = p.hostname
        port = p.port or (443 if p.scheme == "https" else 80)
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def verify_live(repo, branch, path):
    data, url = fetch_raw(repo, branch, path)
    if not data:
        return None, "拉取失败"
    text = data.decode("utf-8", "ignore")
    if "#EXTM3U" in text[:400]:
        names = M3U_LINE.findall(text)
        urls = re.findall(r"^((?:https?|rtmp|rtsp|rtp|udp)://\S+)$", text, re.M)
        groups = len(re.findall(r'group-title="([^"]*)"', text))
        form = "m3u"
    elif re.search(r"^(?:[^,\n]+,#genre#|#genre#)", text, re.M):
        # TVBox live.txt 格式：频道名,地址 / 分组名,#genre#
        names, urls = [], []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ",#genre#" in line:
                continue
            if "," in line:
                nm, u = line.rsplit(",", 1)
                if u.startswith(("http://", "https://", "rtmp", "rtsp", "rtp", "udp", "p3p", "mitv", "itv")):
                    names.append(nm)
                    urls.append(u)
        groups = len(re.findall(r",#genre#", text))
        form = "txt"
    else:
        return None, "既不是 m3u 也不是 txt 直播列表"

    chans = len(names)
    if chans < TH["live_min_channels"]:
        return None, f"频道太少({chans})"
    bad = [n for n in names if blocked(n)]
    if len(bad) > max(2, chans * 0.05):
        return None, f"含违规频道 {len(bad)} 个"

    alive_rate = None
    sample_n = 0
    if not QUICK and urls:
        http_urls = [u for u in urls if u.startswith("http")]
        step = max(1, len(http_urls) // TH["live_alive_sample"])
        sample = http_urls[::step][:TH["live_alive_sample"]]
        sample_n = len(sample)
        ok = sum(1 for u in sample if probe_url(u, TH["live_alive_timeout"]) is True)
        alive_rate = ok / sample_n if sample_n else None
    return {
        "kind": "live", "repo": repo, "branch": branch, "path": path,
        "url": url, "form": form, "channels": chans, "groups": groups,
        "alive_rate": alive_rate, "alive_sample": sample_n,
        "size": len(data), "_raw": data,
    }, None


# ---------------- 主流程 ----------------

def scan_repo(cand):
    repo = cand["repo"]
    kind = cand["kind"]
    log(f"\n[{kind}] {repo}")
    meta = repo_meta(repo)
    if meta:
        branch = meta.get("default_branch") or "main"
        star = meta.get("stargazers_count", 0)
        fork = meta.get("forks_count", 0)
        days = days_since(meta.get("pushed_at"))
        log(f"    star={star} fork={fork} push={str(meta.get('pushed_at'))[:10]} ({days}天前)")
    else:
        # API 不可用：用配置里的分支或猜，元数据置中性值
        branch = cand.get("branch") or "master"
        star, fork, days = cand.get("star", 0), cand.get("fork", 0), cand.get("days", 30)
        log(f"    !! 元数据不可用，按配置降级 star={star} fork={fork} branch={branch}")

    paths = cand.get("paths") or []
    if not paths:
        paths = discover_movie_files(repo, branch) if kind == "movie" else discover_live_files(repo, branch)
    if not paths:
        return None, "未找到候选配置文件"
    log(f"    候选文件: {paths[:4]}")

    # 分支不确定时同时试 master/main（API 限速降级后 branch 可能猜错）
    branches = [branch]
    for b in ("main", "master"):
        if b not in branches:
            branches.append(b)
    if meta:
        branches = branches[:1]   # 有元数据时分支可信，不多花请求

    best, best_raw, errs = None, None, []

    def evaluate(p, br):
        r, err = (verify_movie if kind == "movie" else verify_live)(repo, br, p)
        if not r:
            return None, err
        r.update({"star": star, "fork": fork, "days": days, "note": cand.get("note", "")})
        if kind == "movie":
            richness = min(1.0, r["sites"] / 120) if r["form"] == "single" else min(1.0, r["urls"] / 6)
            w = W["movie"]
            r["score"] = (w["fresh"] * score_fresh(days, TH["movie_max_age_days"])
                          + w["community"] * score_community(star, fork)
                          + w["richness"] * richness
                          + w["spider_ok"] * r["spider_ok"])
            r["detail"] = f"sites={r['sites']} spider_ok={r['spider_ok']:.2f} form={r['form']}"
        else:
            richness = min(1.0, r["channels"] / 400)
            alive = r["alive_rate"] if r["alive_rate"] is not None else 0.5
            w = W["live"]
            r["score"] = (w["fresh"] * score_fresh(days, TH["live_max_age_days"])
                          + w["community"] * score_community(star, fork)
                          + w["richness"] * richness
                          + w["alive"] * alive)
            r["detail"] = f"channels={r['channels']} alive={r['alive_rate']}({r['alive_sample']}抽样)"
        return r, None

    def try_paths(path_list, br):
        nonlocal best, best_raw
        for p in path_list:
            r, err = evaluate(p, br)
            if r:
                log(f"      OK {p}@{br}  {r['detail']}  score={r['score']:.3f}")
                if not best or r["score"] > best["score"]:
                    best, best_raw = r, p
            else:
                errs.append(f"{p}@{br}: {err}")
                log(f"      -- {p}@{br}  {err}")
            if best:
                return True
        return False

    # 先按配置/发现的路径试，失败再换另一个分支整体重试
    used_paths = list(paths)
    for i, b in enumerate(branches):
        if try_paths(used_paths, b):
            branch = b
            break
        # 首轮全失败且没有可用元数据时，用目录发现补一批路径
        if i == 0 and meta:
            alt = [p for p in (discover_movie_files(repo, b) if kind == "movie"
                               else discover_live_files(repo, b)) if p not in used_paths]
            if alt:
                log(f"    目录发现补充: {alt[:3]}")
                used_paths += alt

    if not best:
        return None, "; ".join(errs[:3])[:200]
    return best, None


def load_state():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def decide_switch(kind, new, state):
    """新源明显更优才切换，避免小幅波动来回跳"""
    cur = state.get(kind)
    if FORCE_SWITCH:
        return True, "强制模式，直接采用本轮最优"
    if not cur:
        return True, "首次选定"
    same = (new["repo"] == cur.get("repo") and new.get("path") == cur.get("path"))
    if same:
        return True, "同一源，刷新内容"
    cur_s = float(cur.get("score") or 0)
    if new["score"] > cur_s * (1 + SWITCH_MARGIN):
        return True, f"更优 {new['score']:.3f} > 现有 {cur_s:.3f} × {1 + SWITCH_MARGIN:.2f}"
    return False, f"保持现有（新 {new['score']:.3f} 需超过 {cur_s * (1 + SWITCH_MARGIN):.3f} 才换）"


def build_movie(w, outdir):
    """写 TVBox 配置，并把相对路径的 spider / 直播列表 / 站点 jar 一起镜像下来，做到自包含"""
    notes = []
    cfg = json.loads(w["_raw"].decode("utf-8", "ignore"))

    def mirror(relpath, label):
        rel = relpath.split(";")[0].strip().lstrip("./")
        if not rel or rel.startswith("http"):
            return False
        data, _ = fetch_raw(w["repo"], w["branch"], rel, timeout=60)
        if not data:
            notes.append(f"!! {label} 下载失败: {rel}")
            return False
        dst = os.path.join(outdir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst) or outdir, exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
        notes.append(f"{label} 已镜像 {rel} ({len(data) // 1024}KB)")
        return True

    spider = str(cfg.get("spider", ""))
    if spider:
        if spider.startswith("http"):
            notes.append("spider 为远程地址，未镜像")
        else:
            mirror(spider, "spider")

    # lives 里的相对路径（./list.txt 之类）也要落盘，否则直播页会报错
    for lv in (cfg.get("lives") or []):
        u = str(lv.get("url") or "")
        if u and not u.startswith("http"):
            mirror(u, "live列表")

    # 站点自定义 jar/js（少数配置按站点覆盖）
    seen = set()
    for s in (cfg.get("sites") or []):
        j = str(s.get("jar") or "")
        if j and not j.startswith("http") and j not in seen:
            seen.add(j)
            mirror(j, "站点jar")

    dst = os.path.join(outdir, "tvbox.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    notes.append(f"tvbox.json {os.path.getsize(dst) // 1024}KB, sites={len(cfg.get('sites') or [])}")
    return notes


def build_live(w, outdir):
    ext = ".txt" if w.get("form") == "txt" else ".m3u"
    dst = os.path.join(outdir, "live" + ext)
    with open(dst, "wb") as f:
        f.write(w["_raw"])
    # 同时留一份 .m3u 名字，方便不同播放器认
    if ext == ".txt":
        with open(os.path.join(outdir, "live.m3u"), "wb") as f:
            f.write(w["_raw"])
    return [f"live{ext} {os.path.getsize(dst) // 1024}KB, {w.get('channels')} 频道"]


def write_markdown(report, state):
    L = ["# 源择优报告", "",
         f"生成时间：{report['generated_at']}", "",
         "## 当前生效", ""]
    for kind, label in (("movie", "电影聚合"), ("live", "网络直播")):
        s = state.get(kind)
        if s:
            L.append(f"- **{label}**：`{s['repo']}` / `{s.get('path')}` — 分数 {s['score']:.3f}")
        else:
            L.append(f"- **{label}**：未设置")
    L += ["", "## 本次排名", ""]
    for kind, label in (("movie", "电影聚合"), ("live", "网络直播")):
        L += [f"### {label}", "", "| # | 仓库 | 详情 | 分数 |", "|---|---|---|---|"]
        for i, r in enumerate(report["ranking"][kind], 1):
            L.append(f"| {i} | `{r['repo']}` | {r['detail']} | {r['score']:.3f} |")
        d = report["decisions"].get(kind)
        if d:
            L += ["", f"> {'✅ 已切换' if d['switch'] else '⏸ 保持不动'}：{d['reason']}"]
        L.append("")
    if report["failed"]:
        L += ["### 本轮不可用", ""]
        for f_ in report["failed"]:
            L.append(f"- `{f_['repo']}` — {f_['reason']}")
    with open(os.path.join(HERE, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {"movie": [], "live": [], "failed": []}

    for cand in CFG["candidates"]:
        if ONLY and ONLY not in cand["repo"].lower():
            continue
        try:
            r, err = scan_repo(cand)
        except Exception as e:
            r, err = None, f"异常: {e}"
        if r:
            results[r["kind"]].append(r)
        else:
            results["failed"].append({"repo": cand["repo"], "kind": cand["kind"], "reason": err})
        time.sleep(0.6 if TOKEN else 6.5)   # 无 token 时 search 限速 10/min，放慢节奏

    winners = {}
    for kind in ("movie", "live"):
        lst = sorted(results[kind], key=lambda x: -x["score"])
        if lst:
            winners[kind] = lst[0]

    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "winners": {}, "ranking": {}, "failed": results["failed"], "decisions": {}}
    for kind in ("movie", "live"):
        report["ranking"][kind] = [
            {k: v for k, v in r.items() if k != "_raw"}
            for r in sorted(results[kind], key=lambda x: -x["score"])
        ]

    # ---- 是否切换 ----
    state = load_state()
    for kind, w in winners.items():
        do_switch, why = decide_switch(kind, w, state)
        report["decisions"][kind] = {"switch": do_switch, "reason": why}
        report["winners"][kind] = {k: v for k, v in w.items() if k != "_raw"}
        log(f"    [{kind}] 决策: {'切换' if do_switch else '保持'} — {why}")
        if do_switch:
            if kind == "movie":
                notes = build_movie(w, OUT_DIR)
            else:
                notes = build_live(w, OUT_DIR)
            report["winners"][kind]["build"] = notes
            state[kind] = {k: v for k, v in w.items() if k != "_raw"}
            log(f"    [{kind}] 已输出: {'; '.join(notes) if notes else 'ok'}")

    save_state(state)
    with open(os.path.join(OUT_DIR, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    write_markdown(report, state)

    if QUIET:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print("\n" + "=" * 78)
    for kind in ("movie", "live"):
        title = "电影聚合" if kind == "movie" else "网络直播"
        print(f"\n【{title}】排名")
        for i, r in enumerate(report["ranking"][kind], 1):
            mark = " ★本次最优" if i == 1 else ""
            print(f"  {i}. {r['score']:.3f}  {r['repo']:42s} {r['detail']}{mark}")
        d = report["decisions"].get(kind)
        if d:
            print(f"  → {'已切换' if d['switch'] else '保持不动'}: {d['reason']}")
    if report["failed"]:
        print(f"\n【不可用】{len(report['failed'])} 个")
        for f_ in report["failed"]:
            print(f"  - {f_['repo']:42s} {f_['reason']}")
    print(f"\n输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
