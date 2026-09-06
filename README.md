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
16 个候选源登记     etl/onco_etl/sources.py   →  MySQL db_ot.source
可达性探针          ops\etl.ps1 probe-reach   →  MySQL db_ot.source_probe_log
```

16 个源全部可达（13 个直连、3 个需代理）。已确认的硬结论：症状维在常见上皮癌上
**没有任何公开机器可读源可用**，HPO/Orphanet/NCIt 实测覆盖见
[docs/数据源探针计划.md](docs/数据源探针计划.md)。这也是 P0 必须先跑探针、不先建表的原因。

## 架构

```
┌─ 采集层 etl/onco_etl/ ───────────┐   ┌─ 服务层（待建）─────────────────┐
│ sources.py   16 个候选源登记表    │   │ FastAPI :8000                   │
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
```

依赖本机已装（SQLAlchemy、PyMySQL、requests、certifi、lxml、bs4、pandas），不需要 pip install。

## 常用命令

```powershell
python db/tests/run.py status            # 表行数 + legal_note 门禁
ops\etl.ps1 probe-reach --code mondo     # 只探指定源
ops\etl.ps1 probe-status                 # 每源最近一次裁定
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
