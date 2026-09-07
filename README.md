# onco-trace

癌症与高致死疾病的结构化数据站：常规介绍、症状、关联器官（人体器官树 + 疾病↔器官多对多）、
最前沿研究、症状分析、危险度分析、患病年龄段与死亡年龄段分析、五年存活率，以及从器官/症状/
危险因素/靶点/药物**反查疾病**。

数据全部来自公开源抓取，仓库里没有人工录入模块。每个数字都带来源、口径与查阅时间；
本站不做诊断，症状反查输出的是参考排序。

## 当前进度：P0 源探针

后端与前端尚未开工。现在仓库里只有"源与探针"这一层——先实测每个维度到底有没有能自动落库的
公开源、覆盖率多少，再决定建哪些业务表。

```
18 个恶性肿瘤基准   etl/onco_etl/targets.py
21 个候选源登记     etl/onco_etl/sources.py   →  MySQL db_ot.source
可达性探针          ops\etl.ps1 probe-reach   →  MySQL db_ot.source_probe_log
专项覆盖度探针      ops\etl.ps1 probe         →  MySQL db_ot.source_probe_log
```

21 个源全部可达（19 个直连、2 个需代理）。已过出口判据的三维：

- **器官树**——SEER 的 ICD-O-3 Site Recode 码表一份文件同时供 anatomy 与 histology：
  82 个 site recode / 332 个拓扑码 / 806 个形态学码，18 病 18/18 挂载到器官级分组。
- **ID 主干**——MONDO 18 个疾病主条目全部解析通过，跨源枢纽定为 NCIt
  （主条目的 NCIT xref 18/18；MONDO 自带的 MESH 只有 7/18、EFO 5/18，文献维带不动。
  GWAS 不受此限——它里面的癌种档以 MONDO ID 为键）。
- **生存率**——SEER Cancer Stat Facts 18 病各一页，17 病带分期别五年生存率（≥3 档）。
  但观测值止于 2018，2019–2023 只有模型趋势线，页面必须标口径；白血病无实体瘤分期，只给全分期一个数。

统计层达标（有条件）。年度序列来自 SEER：发病率与死亡率 1975–2024 逐年齐备（死亡 50 年、发病 49 年），
另有 6 组种族/民族与双性别分层；但它的年龄分组只有 8 档宽区间（`<20` 到 `>84`），做不了 5 岁组标化率。
唯一按 5 岁组乃至 1 岁组发布的 GBD，**数值全部在注册登录门后**——词表实测 18 病 18/18 有对应病因档、
中国在内、1990–2021 逐年、155 个年龄组、四个度量齐备，可一个数都取不回来。
5 岁组这一档由 IARC 的 GCO 补上，两个入口都匿名直连：Cancer Today 给中国 18 病的国家级例数、
世界标化率与累积风险（无年龄维、一版一个年份），Cancer Over Time 给 2002–2017 × 18 个 5 岁档 ×
三性别的年龄别率。两条限定随数值一起落库——中国这一路只有发病没有死亡，且是 5 个登记处覆盖约 60%
人口的外推，不与 Today 的全国估算相减。死亡年龄段的中国那一半仍空：备选的 WHO GHO 已实测排除
（AGEGROUP 与 GHECAUSES 两维都齐，可带这两维的 8 个指标只有区域与收入组聚合、中国零行，
而有中国行的 14 个指标一个都没有年龄维），这一维只剩 IHME 注册账号一条路。

危险度这一维匿名侧能给出危险因素清单和位点级效应量，但给不出归因强度。GBD 的比较风险评估数值与
统计层撞在同一道 IHME 注册门后——vizhub GBD Compare 的四个数据接口一律 401，匿名开放的只有
回版本号的那一个。能匿名取回的是随 GBD 发布的 A2 交叉表：2390 对病因 × 危险因素关联
（217 个病因 × 88 个危险因素），按 REI 层级剔掉聚合档后 18 病里 **10 病有 ≥3 个独立危险因素**
（肺癌 16、结直肠 11、乳腺 7，脑肿瘤一档都没有）。但表里 Deaths/YLLs/YLDs/DALYs 四列只是
"这个组合有数"的标记，没有一个 PAF 或 RR。

结论相反的 GWAS Catalog 补上强度那一半：匿名整包 1,192,032 行关联里 `OR or BETA` 84.3% 非空、
`95% CI (TEXT)` 75.0% 含可解析区间；按 targets 声明的主条目 URI，达到判据（≥3 个带效应量与区间的
独立位点）的是 **14/18**（另有两病主条目一行都没有，关联挂在同级组织学档上，并进来是 18/18 的
上界，要人工裁定成声明 ID 列表才算数）。代价是它给的是
遗传易感位点而非可干预暴露，`OR` 与 `β` 同列混装、方向只写在文本注记里，效应量类型只能标"未判定"，
而全表没有一个 PAF 列。两半合起来落库的形状是：暴露清单与 role 来自 CRA，位点的 OR/β 与区间来自
GWAS，归因强度两源皆空——危险度页面按"有证据关联、无归因强度"呈现，不做归因分数榜。

已确认的硬结论：症状维在常见上皮癌上**没有任何公开机器可读源可用**——MONDO 的
UBERON 定位与 HP 症状注释各只有约 1% 覆盖，HPO/Orphanet/NCIt 的实测覆盖见
[docs/数据源探针计划.md](docs/数据源探针计划.md)。这也是 P0 必须先跑探针、不先建表的原因。

## 架构

```
┌─ 采集层 etl/onco_etl/ ───────────┐   ┌─ 服务层（待建）─────────────────┐
│ sources.py   21 个候选源登记表    │   │ FastAPI :8000                   │
│ targets.py   18 病基准清单        │→MySQL→│  /api/*        查询与反查     │
│ fetch.py     直连→代理三态取数    │ db_ot │  /             托管前端 dist  │
│ raw.py       data/raw 归档+sha256 │   │ MySQL db_ot (localhost:3306)    │
│ joblog.py    etl_job_log 运行史   │   └─────────────────────────────────┘
│ probe*       覆盖度探针           │   ┌─ 前端（待建）Vue 3 + Vite ──────┐
└──────────────────────────────────┘   └─────────────────────────────────┘
```

五层数据模型：词表层（器官树/症状/危险因素）→ 实体层（疾病主档）→ 关系层（多对多，带 role）→
统计层（长表 + 完整口径维度）→ 证据层（叙述、试验、文献、里程碑）。

## 首次准备

```powershell
copy .env.example .env        # 填 DB_* 与 PROXY_URL
python db/tests/run.py apply db/schema.sql
python db/tests/run.py migrate
ops\etl.ps1 seed-sources
ops\etl.ps1 probe-reach
ops\etl.ps1 probe             # 专项覆盖度探针，不带 --code 就是全跑
```

依赖本机已装（SQLAlchemy、PyMySQL、requests、certifi、lxml、bs4、pandas、openpyxl），不需要 pip install。

## 常用命令

```powershell
python db/tests/run.py status            # 表行数 + legal_note 门禁
ops\etl.ps1 probe-reach --code mondo     # 只探指定源的可达性
ops\etl.ps1 probe --list                 # 有哪些专项探针
ops\etl.ps1 probe --code mondo --offline # 用 data/raw 归档离线重放，不重新下载
ops\etl.ps1 probe-status                 # 每源每份数据集最近一次裁定
ops\etl.ps1 status                       # 库现状速览
```

## 结构

| 目录 | 内容 |
|---|---|
| `db/` | `schema.sql` 是全量建表脚本，`migrations/` 是增量变更，`tests/run.py` 是迁移与断言跑测器 |
| `etl/` | `onco_etl` 采集与探针层，`python -m onco_etl` 运行 |
| `docs/` | 数据源探针计划与口径说明 |
| `data/` | `raw/` 原始响应归档、`exports/` 待抽查草稿，都不入库不提交 |
| `ops/` | PowerShell 包装脚本 |

## 四条约定

1. `etl/` 与后端互不引用，只通过 MySQL 表结构对话。`db/schema.sql` 是唯一契约，两侧都不生成 schema。
2. 改表结构一律新增 `db/migrations/NNNN_*.sql`，并在注释里写清改的理由。
3. 写库的时间戳由应用层以本地墙钟格式（`YYYY-MM-DD HH:MM:SS`）写入，一律走 `onco_etl.clock`。
   本机是 UTC+8，带 `Z` 的串会被 MySQL 按字面量存成 UTC 时刻，跨午夜的采样会错位一天。
4. 每个落库的事实都必须能回溯到来源：`source_id` + `extract_method` + `review_status`。
   `source.legal_note` 为空的源不许进采集。
