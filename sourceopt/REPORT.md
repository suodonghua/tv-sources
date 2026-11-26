# 源择优报告

生成时间：2026-11-26T20:15:13+00:00

## 当前生效

- **电影聚合**：`qist/tvbox` / `0825.json` — 分数 0.979
- **网络直播**：`vbskycn/iptv` / `tv/iptv4.m3u` — 分数 0.942

## 本次排名

### 电影聚合

| # | 仓库 | 详情 | 分数 |
|---|---|---|---|
| 1 | `qist/tvbox` | sites=135 spider_ok=1.00 form=single | 0.979 |
| 2 | `Lightconer/tvbox-ysc-config` | sites=46 spider_ok=0.50 form=multi | 0.843 |
| 3 | `gaotianliuyun/gao` | sites=135 spider_ok=1.00 form=single | 0.617 |
| 4 | `mrgaoshuiquan/tvbox-config` | sites=52 spider_ok=1.00 form=single | 0.608 |

> ✅ 已切换：同一源，刷新内容

### 网络直播

| # | 仓库 | 详情 | 分数 |
|---|---|---|---|
| 1 | `Supprise0901/TVBox_live` | channels=916 alive=1.0(8抽样) | 0.926 |
| 2 | `vbskycn/iptv` | channels=313 alive=0.875(8抽样) | 0.884 |
| 3 | `CCSH/IPTV` | channels=4991 alive=0.75(8抽样) | 0.833 |
| 4 | `fanmingming/live` | channels=94 alive=None(0抽样) | 0.664 |
| 5 | `YanG-1989/m3u` | channels=123 alive=0.375(8抽样) | 0.572 |
| 6 | `ngo5/IPTV` | channels=158 alive=0.0(8抽样) | 0.432 |

> ⏸ 保持不动：保持现有（新 0.926 需超过 1.083 才换）

### 本轮不可用

- `FanchangWang/tvbox_config` — dist/tvbox.json@main: 多仓 9 个子仓全部拉不到内容; dist/my.json@main: 多仓 12 个子仓全部拉不到内容
- `hkuc/tvbox-config` — url.json@main: 不是对象
- `Guovin/iptv-api` — output/result.m3u@master: 拉取失败; output/ipv4/result.m3u@master: 拉取失败; output/ipv6/result.m3u@master: 拉取失败