# 源择优报告

生成时间：2026-11-06T19:58:39+00:00

## 当前生效

- **电影聚合**：`qist/tvbox` / `0825.json` — 分数 0.978
- **网络直播**：`vbskycn/iptv` / `tv/iptv4.m3u` — 分数 0.970

## 本次排名

### 电影聚合

| # | 仓库 | 详情 | 分数 |
|---|---|---|---|
| 1 | `qist/tvbox` | sites=135 spider_ok=1.00 form=single | 0.978 |
| 2 | `Lightconer/tvbox-ysc-config` | sites=46 spider_ok=0.50 form=multi | 0.833 |
| 3 | `mrgaoshuiquan/tvbox-config` | sites=52 spider_ok=1.00 form=single | 0.623 |
| 4 | `gaotianliuyun/gao` | sites=135 spider_ok=1.00 form=single | 0.616 |

> ✅ 已切换：同一源，刷新内容

### 网络直播

| # | 仓库 | 详情 | 分数 |
|---|---|---|---|
| 1 | `Supprise0901/TVBox_live` | channels=773 alive=0.875(8抽样) | 0.882 |
| 2 | `vbskycn/iptv` | channels=377 alive=0.75(8抽样) | 0.871 |
| 3 | `CCSH/IPTV` | channels=3644 alive=0.75(8抽样) | 0.832 |
| 4 | `YanG-1989/m3u` | channels=123 alive=1.0(8抽样) | 0.803 |
| 5 | `fanmingming/live` | channels=94 alive=None(0抽样) | 0.663 |
| 6 | `ngo5/IPTV` | channels=158 alive=0.0(8抽样) | 0.444 |

> ⏸ 保持不动：保持现有（新 0.882 需超过 1.116 才换）

### 本轮不可用

- `FanchangWang/tvbox_config` — dist/tvbox.json@main: 多仓 9 个子仓全部拉不到内容; dist/my.json@main: 多仓 12 个子仓全部拉不到内容
- `hkuc/tvbox-config` — url.json@main: 不是对象
- `Guovin/iptv-api` — output/result.m3u@master: 拉取失败; output/ipv4/result.m3u@master: 拉取失败; output/ipv6/result.m3u@master: 拉取失败