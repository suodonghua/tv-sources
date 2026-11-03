# 源自动择优（TVBox 配置 + 直播源）

每天在 GitHub Actions 上自动扫描一批候选源仓库，实测验证后按综合分排序，把最优的**镜像成本仓库里的文件**，电视端只认两个固定地址，永远不用再手动改。

## 电视端只认这两个地址

```
电影聚合   https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/tvbox.json
网络直播   https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/live.m3u
```

## 评分怎么算

不是简单看 star。每个候选都要实际拉下来验证，不合格直接淘汰：

**电影配置**（`sites` 数组 / `urls` 多仓）
- 硬门槛：JSON 能解析、站点数 ≥ 20、含违规站点比例 < 5%、spider 能下载
- 打分：新鲜度 35% + star/fork 25% + 站点丰富度 30% + spider 可用 10%

**直播源**（m3u / TVBox live.txt）
- 硬门槛：格式正确、频道数 ≥ 80、无违规频道
- 打分：新鲜度 25% + star/fork 20% + 频道数 20% + **抽样连通率 35%**（随机抽 8 条流做 TCP 连接测试，全挂的直接出局）

## 换源规则（避免来回跳）

新源分数必须超过当前源 **15%** 才切换（`config.json` 里的 `switch_margin`）。
同一个源有新内容时会原地刷新，不触发切换。

## 仓库结构

```
.github/workflows/scan.yml   定时任务（每天北京时间 03:17）
sourceopt/
  config.json                候选清单、权重、阈值、违规词黑名单
  scan.py                    引擎（纯标准库，无第三方依赖）
  state.json                 当前生效的源（自动维护，别手改）
  REPORT.md                  最近一次排名报告
  out/                       运行时的临时输出（Actions 会清掉）
tvbox.json                   输出的 TVBox 配置（135 个站点）
jar/                         spider，随配置一起镜像，不依赖上游
live.m3u                     输出的直播源（432 个频道）
list.txt / yo21.txt          TVBox 内置直播列表（配置里引用的相对路径）
```

## 为什么要把 spider 一起镜像

TVBox 配置里的 `"spider": "./jar/pg_upgraded.jar"` 是相对路径，会相对于配置文件所在 URL 解析。
只复制 JSON 不复制 jar，站点会全部加载失败。所以脚本会把 spine、内置直播列表、站点级 jar 一并下载到相同相对路径下，**上游删库了也不影响**。

## 想加自己的源

编辑 `sourceopt/config.json` 的 `candidates`：

```json
{
  "kind": "movie",          // movie 或 live
  "repo": "owner/repo",
  "paths": ["api.json"],    // 已知路径可省一次 API 调用；不写会自动发现
  "note": "备注"
}
```

不加 `paths` 也能跑，脚本会用 Trees API 自动找 `.json` / `.m3u` / `.txt`。

## 手动触发

仓库 → Actions → 源自动择优 → Run workflow。
勾上 `force` 会忽略 15% 阈值，直接采用当轮最优。

## 注意

- 仓库必须是 **Public**，raw 地址才能匿名访问，电视那边不带 token。
- 每天跑一次约 1~2 分钟，Actions 免费额度完全够。
- 没有 `GITHUB_TOKEN` 时本地跑会降级：元数据走 Search API（10 次/分钟），文件仍能正常验证。
