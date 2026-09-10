# onco-trace

癌症与高致死疾病的结构化数据站：常规介绍、症状、关联器官（人体器官树 + 疾病↔器官多对多）、
最前沿研究、症状分析、危险度分析、患病年龄段与死亡年龄段分析、五年存活率，以及从器官/症状/
危险因素/靶点/药物**反查疾病**。

数据全部来自公开源抓取，仓库里没有人工录入模块。每个数字都带来源、口径与查阅时间；
本站不做诊断，症状反查输出的是参考排序。

## 当前进度：装载六批按维收口，后端五批二十条读接口 + 前端四视图（含五路反查）

P0 的出口判据是"由覆盖度矩阵裁定 MVP 建哪些表"，两个产出都已在仓库里：
[docs/数据源覆盖度.md](docs/数据源覆盖度.md)（13 列 × 16 份裁定，由
`cd etl && python -m onco_etl matrix` 从 `source_probe_log` 自动生成）与
[docs/MVP裁定.md](docs/MVP裁定.md)（逐维裁进/不进、以什么口径进、缺的那半在页面上怎么显示）。
21 个候选源的 `status` 已从占位的 `candidate` 翻成 13 `active` / 7 `paused` / 1 `rejected`
（C2e 把 GBD 匿名可取的危险因素清单落库后，`gbd_cra` 从 `paused` 转成 `active`）。
15 张业务表已按这份裁定建好并落库（`db/migrations/0002_business_tables.sql`，与 `db/schema.sql`
同内容）。每张表的每一列都对着 `source_probe_log` 里的 `fields_seen` 与 `sample` 来——
探针没量到的字段不建列，源给出口径差别的地方拆成列存而不是混成一列。
装载器已开工：`etl/onco_etl/load/` 是底座（拿版本号、幂等写行、出处五列），
疾病主档、器官树与组织学、统计层与生存率、症状、危险因素、研究层六批已落库——`disease` 18 行（`ncit_id` 18/18 非空）、
`anatomy_node` 133 个节点（82 个 SEER site recode + 51 个 MONDO 亚部位 term）、
`histology_code` 657 个恶性形态学码，逐病挂载 85 条器官关系与 3,111 条组织学关系；
`stat_fact` 16,470 行（GLOBOCAN 中国 2024 国家单点 162 / GCO Over Time 逐年 × 18 档 5 岁组
11,232 / SEER 新发率与死亡率年度序列加年龄组构成 5,004 / 研究层四个计数 72），`survival` 1,762 行
（分期档 17 病 + 全期头条 18 病 + 五年存活率逐年序列，其中 882 行是 Joinpoint 拟合值）。
五年存活率整维只在 `survival` 一张表里，长表不重复写同一个数；中国死亡年龄组这一维
源侧就是 0 行，页面按空态显示。
`symptom` 270 行、18/18 病都有英文清单：PDQ 185 条（源侧 209 条清单条目，按页面声明顺序归一后
吃掉 24 条"同一症状在多个组织学档里各写一遍"的复述；该源另有 4 条从散文句里抠出来的，句子不是
症状项，不落）、中文维基条目 67 条、WHO 中文版 18 条（只有 3 病有症状节）。中文侧合起来只有
7/18 病有现成清单，另 11 病的中文名按实测留空、不做翻译列；维基那 67 条里 32 条逐条目测判为
分期定义、亚型描述、并发症或释义段，置 `rejected` 留痕而不是删掉——删了就没人知道为什么少了。
`risk_factor` 3,082 个节点（3,049 个遗传位点标签 + 33 个可干预暴露）配 `disease_risk_factor`
6,279 行：GWAS 6,208 行覆盖 18/18 病（主条目档 3,651 + `targets.GWAS_URI` 声明档 2,557；
一行是一条关联不是一个位点，键含研究号与 p 值，所以行数比去异位点数 4,493 大）；GBD CRA 71 行
覆盖 17/18 病（brain 在源里一行都没有），这一层只有清单没有强度——源里 Deaths/YLLs/YLDs/DALYs
四列的 `X` 标的是"这个组合有数"，不是效应量。`paf`、`paf_basis`、`risk_factor.label_zh` 建而不填。
研究层一批四源合一台（`load/research.py`）：`trial` 23,705 行＝19,254 个在招试验（一行是一个试验
命中一个病，2,469 个跨病出现，最多一个试验挂在 17 个病上），只落在招三档所以 `why_stopped` 整列空；
`publication` 9,000 行是每病按相关度取的前 500——上限样本不是全量，库内真实命中数与靶点/药计数
一起进 `stat_fact` 那 72 行 `query_count`；OT 的 228,551 条关联按 `score ≥ 0.1` 收成 `disease_target`
28,919 行与 `target` 7,098 个节点（节点只从落库的关系行长出来，所以两个数对得上），`drug` 6,309 行
按 (病, 药, 阶段) 存、机制并成数组（4,128 行有机制）。这一台与其他五台唯一的差别是探针从没取过行
（B5 那三支判的是"这一维能不能拿到"），所以行级取数由装载器自己做：原始响应 gzip 进
`data/raw/<源>/rows-<版本>/`，并按四个新 dataset_code 补登记 `dataset_release`——
`--offline` 因此能逐字段重放，本轮离线重放与联网那趟的装载报告除耗时外逐字相同。
装载侧到此按维度收口（`MVP裁定.md` §一 里判"进"的维都已落库），往后每一批都在读的那一侧。
P0 只剩一件收尾的事：IHME 的免费非商用账号已注册、凭据已填进 `.env`，但数值入口的登录是
Azure AD B2C 换 token（scope `…/data-api/data.read`，界面前还有一层 Cloudflare），这条取数路还没实现。
接上之前 `gbd_results` 一支探针记 `paused`（`gbd_cra` 匿名可取的清单已在 C2e 落库），
中国死亡年龄组与危险因素归因强度（PAF）这两半留空。注册入口也已实测定位（GBD Results 页一打开就弹注册对话框，顶栏 Account → Register
是同一条流的第二条路；没有独立注册页）。

后端第一批（D3a）已落：`api/onco_api/` 与采集层互不 import，只读同一份 `.env` 与同一套表，
只注册 GET，且连接在 MySQL 会话级就是 READ ONLY——"服务层会不会改数"这一问在库里就有答案（跑测器里那条
UPDATE 会被服务端直接拒）。三个接口各答一件事：`/api/meta` 一次答完"什么时候的数"（`etl_job_log` 里
`load` / `probe` / `probe-reach` 三台各自最近一次运行 + 20 行版本登记收成 19 份数据集的最近一版）、
"有多少"（19 张表行数与十个维度的逐度量合计）、"缺什么"（三条整维级空态）；`/api/diseases` 每行是
18 病之一的身份 11 列加十维度量——一台 `GROUP BY disease_id` 的聚合，一次请求 10 条查询而不是 18 × 10 条计数；
`/api/diseases/{code}` 给 `disease` 全行加出处加与列表同一份的逐维计数。三处口径由这一层承担：
§二 那六处空态写成度量上的谓词而不是硬编码文案（`paf` 或 `freq_band` 哪天填上，页面就不再说它空着；
反过来哪个病掉出一批零行，页面也立刻如实说缺），出处的五列在每行收进一个 `provenance` 对象并补上源名、
许可与上游版本号，响应字段名一律用 DDL 的列名而不另造一套。`python api/tests/run.py` 用 pymysql 把三个
接口返回的每个数字另问一次对账（外加"DDL 里每个 json 列都在解码表里"这类静态条），这一批 530 条断言，
单次请求 70–80 ms。

后端第二批（D3b）加三条读接口，统计层与生存率这两维的读侧到此收口。`/api/diseases/{code}/stats`
把 `stat_fact` 那 16,470 行折成序列：一条线由八列共同定义（`metric` / `unit` / `dataset_code` /
`region` / `sex` / `age_band` / `estimate_basis` / `cohort_note`），全库 210 条线 16,398 个点；
`estimate_basis` 是其中最不能省的一列——GCO 的中国国家级估算与登记处外推同病、同度量、同名，只有
这一列分开两批人。72 行 `query_count` 不折线，单独回在 `counts` 里（每度量只有一行，折出来是 72 条
单点线，看着像有序列）。`year=0` 不是公元 0 年而是"源没给年份"的哨兵，这句解释随响应回在
`conventions.year_zero`。`/api/diseases/{code}/survival` 按三层回而不是摊平成一张表：全分期头条 1 个
数、分期档 4–5 档、逐年序列 93 个点（SEER 8 那套队列，观测线 44 年 + 拟合线 49 年，两条年份重叠但不能相减），
实测每病 94–99 行；白血病分期档 0 行，是源没有实体瘤分期那一档而不是解析失败。这三层不能用
`year=0` 切——`survival` 1,762 行没有一行是 0（装载器给全期头条打的是源标的年份窗末年 2022，与逐年
序列里的 2022 撞在同一个值上）——用的判据是"同一个 (档, 年份窗) 下有几个年份"，这一份判据维度量与
路由共用。`/api/stats/compare` 是跨病榜：18 个度量、口径切片由四个轴（地区/估算依据/性别/年龄组）的组合数决定，
没钉死的轴不替调用方猜——只有一个取值的轴自动钉并记进 `auto_pinned`，还剩几个取值的回 `needs` 并把每个取值
各自的覆盖病数、行数与年份跨度回在 `choices` 里，所以前端照 `needs` 一路点下去就到榜，不必把组合抄进代码；
`sex=both` 的中国国家估算只有 13/18 病有行（另五病的源只按性别发），把 `female`/`male` 凑进来是拿两批
不同的人凑一个率，所以缺的五病列在 `absent` 里而不是零填。空字符串是 `region` 与 `age_band` 的合法取值
（表示这一路的源根本没有这一列），与"未选择"在 URL 级别严格区分：未选择的轴不出现，空值用 `key=` 传回。
`python api/tests/run.py` 现在 1,196 条断言：三个新接口仍是两路对照（TestClient 与 pymysql 各问一次），
榜那一台先在全库断言"五轴加年份钉死后一行一病、只有一个数据集版本"，再让每个度量照自己回的
`needs`/`choices` 一路选到底、与直查逐病比序比值；生存率的三层这台用 `COUNT(DISTINCT year)` 判、路由用
`EXISTS (… year <> …)` 判，两种写法今天同解，抄错条件的那一边会红。另加一台看 SQL 文本本身的：碰
`symptom` / `stat_fact` / `survival` 的每句 SELECT 都必须真的写了 `review_status` 过滤——后两张表今天
零行 rejected，"过滤了"与"忘了过滤"回一样的数（变异检查实测如此），所以这一条盯语句而不是盯数字，
唯一的豁免是 `/api/meta` 问"这张表物理上多少行"的那句 `table_rows`（症状维 270 行含 32 条判非留痕，
两个数各说一件事）。单次请求 37–160 ms。

后端第三批（D3c）再加四条读接口，词表四维（器官、组织学、症状、危险因素）的读侧到此收口。
这四维一行是一个词不是一个数，所以四台都不折线、不求和：页面要的是清单，加上"这一条凭什么算给
这一病"。也因此每台都带回两份出处——关系行一份、节点行一份；JOIN 出来的宽行只挂一份，等于把
"挂载依据"或"节点本身"其中一个说成没有来源。`/api/diseases/{code}/anatomy` 两档平铺：primary
1–5 条是器官级分组（页面「这一病长在哪里」显示它），subsite 1–12 条是 MONDO 的亚部位 term
（只做下钻，12/18 病有），两档不相加也不混排；库里没有父子边（`anatomy_node` 无 parent 列，
亚部位的挂载依据是本病声明的 ICD-9 档而不是某个 site recode 的下级），所以读侧不补一条不存在
的层级。`/api/diseases/{code}/histology` 默认只回三位组档与条数——一病 129–212 个形态学码收在
32–63 档（全站 657 码 / 3,111 条挂载），组档是聚合，不冒充一行事实，所以这一层不带出处，
要某一档的码行用 `?group=` 取；聚合只按组码不按组名，因为码表里 172 个三位组码对 173 个组名
（804 同时是 SMALL CELL CARCINOMA, NOS 与 NON-SMALL CELL CARCINOMA, NOS，854、897 同），
按 (组码, 组名) 聚会把一档拆成两档、每档那几个 DISTINCT 数各算一遍就是重计，所以每档回
`label_variants` 说明这一档有几个组名写法。`/api/diseases/{code}/symptoms` 按源分块，三个源两种
语言不合表也不跨源去重——中文那两路只覆盖 7/18 病，并起来会让另外 11 病看起来也有中文名；
32 条判非的留在库里做留痕、不进响应（270 行 → 238 条），`name` 是源里的说法原样，`heading` 与
`source_url` + `anchor` 能点回页面那一段。`/api/diseases/{code}/risk-factors` 两层分列不相加：
genetic 一行是一个关联（研究 × 位点 × p 值）不是一个位点，所以同时回行数、去重度点数与研究号数
三个数（最多一病 2,151 行只对应 1,061 个位点、99 次研究录入），榜按源自己算好的 `-log10(p)`
降序、`limit` 默认 100（上限 500，18 病里 9 病会被截）并自述 `truncated`；`uri_tier` 两档各回
自己的数（主条目 16/18、含同级组织学声明档 18/18，两档都有 2 病——两个口径合成一个就是虚报）；
exposure 那 71 行只有清单、一个强度都没有，`paf` 与 `effect_kind` 两列建而不填（前者在 IHME
授权门后，后者是源把 OR 与 β 装进同一列、方向只写在 CI 文本的注记里）。
`python api/tests/run.py` 到这一批是 4,202 条断言、整跑 27 s：四台仍是两路对照，聚法那台两侧算法刻意
不同（路由用 `COUNT(DISTINCT …)`，对账在 Python 里用 set 重数，档集合本身也在内）。这一批新钉
两类判据：一是响应行的键集合——节点的列加出处再加 JOIN 带出的那半张表，多出一个 `parent` 或
`children` 就是把库里没有的东西说成有，聚合层冒出 `provenance` 同理；二是榜的截断边界，因为排序
跨引擎不可复现（MySQL 的 `utf8mb4_0900_ai_ci` 对大小写与重音不敏感，Python 按码点序，11/18 病
在第 100 行处分岔而前缀一致），所以不钉 id 清单，钉在榜的降序不增、落榜第一名不高于在榜最后一名。
404 也分两种：码表里有而这一病没有的组码（这一条才抓得住"忘了按病筛"）与形态上不可能存在的写法，
两者都要求报本病的档数。单次请求 138–165 ms，这个数大头在每请求那 10 条维度聚合上——
词表这四台自己的行查询都很轻。

后端第四批（D3d）再加四条读接口——`/api/diseases/{code}/trials`、`/publications`、
`/targets`、`/drugs`——研究层四维（试验、文献、靶点、在研药）的读侧到此收口，
十五条接口把 15 张业务表全读过一遍（登记表四张里只碰 `source` / `dataset_release` /
`etl_job_log`，`source_probe_log` 归采集层生成的矩阵文档）。这一层的量级与前三批不同：一病
试验 292–3,368 行、靶点关联 226–3,321 行、在研药 20–1,023 行、文献每病固定 500 行，而一行
试验折算 2,419 字节、带满两段重文本是 5,863（试验整维带满一次回完是 132.6 MB）。所以四台都分页，
`limit` 默认 50、上限 200，`page` 固定回
`limit` / `offset` / `total_rows` / `returned` / `has_more` 五键。三个数分开说：`total_rows`
是过滤后这一维还剩几行，`source_hit.value` 是源那头命中几行（读 `stat_fact` 里
`estimate_basis='query_count'` 那一行，带那一行自己的出处），`stored_rows` 是库里存了几行——
`captured` 逐病现算而不写死，实测 trial / target / drug 三维 18/18 病两数相等、publication
0/18 相等（每病 500 行是相关度上限样本，取满要七百多页约 5 GB），所以文献页说的是
「库内 500 篇、源命中 139,780 篇」而不是「这一病有 139,780 篇文献」。筛选项由数据自己给：
`facets` 一律按未过滤的整维算（跟着过滤缩水的分面选一次就自删一个选项，第二筛没有回退路），
过滤值不在这一病当次的取值里回 404 并列出可取值——"这一病没有 PHASE4 的在招试验"与
"PHASE4 不是这一病能取的值"是两句话。四条榜都不发明排序（库里没有相关度分值列）：试验按
`nct_id`（注册号序，一病内实测唯一）、文献按 `id`（装载序即 EPMC 查询当时给的相关度序，
拿归档与库里逐位对过三个病各 500 行）、靶点按 `score` 降序加 `ot_id` 兜并列（同一病内 4,391 组同分、
最狠的一组 194 行，不兜底翻页会重行或漏行）、在研药按 `drug_name`。最后那一条是 MySQL
`utf8mb4_0900_ai_ci` 的排序规则序而不是码位序（实测 `.ALPHA.-TOCOPHERYLOXYACETIC ACID` 排在
`(R)-PFI-2` 前面，JS 默认 `sort` 的结果正相反），所以这一维的序由接口自述、前端按拿到的序显示。
`eligibility`（入排标准整段自由文本，行行都有、均长 2,883 字符）与 `publications`（试验引出的
文献，只有 4,709/23,705 行有、那些行均长 2,777 字符）默认不带——lung 的一页 50 行实测 112 KB，
带上两段是 230 KB，要的人用 `?include=eligibility,publications` 点名。
试验行的命中理由 `matched_terms` 有 4,872/23,705 行是 NULL，这一档不是解析失败：逐行查过这些行的
conditions 与标题，0 行出现过本病声明的任何一个词（每病 26–925 行）——它们靠主题词自带的 MeSH
展开进来，页面上"凭什么是这一病"说到"经 MeSH 展开命中"为止。
`python api/tests/run.py` 现在 15,789 条断言、整跑 58 s：这一层的断言重心跟着换三样——
分页切出来那一页必须是同一个全序里的那一段（逐页走完等于整表，越界 offset 回空页而不是回整表）、
分面既不许跟着过滤缩水也不许跟着重算（`(value, rows)` 有序列表逐项对）、`total_rows` 与
`source_hit.value` 各说一件事（文献那一台要求 18/18 病不相等）。四台仍是两路对照，且行的
**类型**也在对照内：JSON 列解码、`Decimal` 折整、出处五列收拢，两侧各算一遍而不是共用转换函数。
单次请求 155–179 ms，且从一页 50 行到一页 200 行只涨一点点——大头仍是每请求那 10 条维度聚合，
四台的行查询本身很轻。

前端第一批（D4a）起 `frontend/`：两个视图、十一屏（概览 + 十条维度各一屏），读的就是上面那十五条
接口，界面不自己算一个数。生产不另占端口——`ops\web.ps1 build` 出 `dist/` 之后 `ops\api.ps1 serve`
在同一台端口上托管站点与接口，本来就是同源；路由用 hash 而不是 history，为的是不给深链接补
fallback、从而不动"只注册 GET"那道护栏。这一层做的事其实只有一件：接口已经说清的口径原样显示出来。
空值显示「—」不显示 0，空态用响应 `gaps` 里那一句而不是前端再判一遍（判据在度量上，只该有一处）；
`year=0` 那三类单点（国家级估算、年龄组构成、查询计数）只显数值不折线；生存率的拟合段与观测段在图上
分开并标出「观测止于 2018」；榜一律按接口给的序显示，前端不 `.sort()`——库的序是 MySQL
`utf8mb4_0900_ai_ci` 的序，JS 默认按码点，两者在这批数据上会分岔，排一次就把接口自述序那句话说假了；
症状按源分块显示，不合表也不翻译。四条研究维共用一台表格组件、逐维给配置，`eligibility` 与
`publications` 两段重文本默认不发、要点名 `?include=`。这一批新钉两样在跑测器里：一道路径契约
（见文末第 5 条），和一条"每个分面轴都得是那个接口可请求的参数名"。断言因此从 15,789 条到
15,849 条、整跑 56 s；实点 lung / breast_female / thyroid 三个病逐屏（稠密、稀疏、整维空态各占
一类），单次请求 140–164 ms，控制台无报错、无 404 接口。

前端第二批（D4b）落跨病榜一屏 `/compare`：三个视图、先给度量，再照接口回的 `needs`/`choices` 一路把口径钉死，
没钉全时接口不出榜，页面也老老实实说还差几刀。只有一个取值的口径轴自动钉（`auto_pinned`），
`region=''` 与 `age_band=''` 作为合法取值在 URL 里用 `key=` 传回，前端绝不用 `null` 占位——因为
vue-router 4 会把 `null` 序列化成裸键 `?region&`，裸键读回来又跟空串分不清。榜按接口给的序显示，
`year=0` 那批行显示为「无年份（单点）」，缺的病显示为缺席标签而不是零。`python api/tests/run.py` 到这一批
15,920 条断言，新增 71 条钉在榜接口本身（含 18 个度量各自的 `needs` 路径与空串轴路径契约），
前端路径契约清掉 `BACKEND_ONLY`，清单与源码字面量 15 条对 15 条。实点 `incidence_asr`（auto_pin 到
地区 China、年龄组空）、`incidence_asr` 下 `national_estimate × sex=both`（13/18 覆盖、缺五个性别相关癌种）、
`trial_count`（四轴全空串，18/18 覆盖）三档，构建 1.94 s，控制台仅一个故意触发的空串轴 404。

后端第五批（D3e）加五条反查接口，读接口从十五条扩到二十条：`/api/anatomy/{node_id}/diseases`、
`/api/symptoms/{name}/diseases`、`/api/risk-factors/{factor_id}/diseases`、
`/api/targets/{ot_id}/diseases`、`/api/drugs/{drug_id}/diseases`。正向页答"这一病有什么"，
反查页答"这个东西挂在哪些病上"——两边读同一张关系表、走同一道 `NOT_REJECTED` 过滤，同一对
(病, 实体) 不会一边可见一边不可见，这条由跑测器的 `check_reverse` 逐条比对守。路径参数用
稳定标识而不是名字：器官与危险因素用表内 id、症状用名 + lang（`symptom` 没有独立标识列）、
靶点用 `ot_id`、药用 CHEMBL 码；疾病清单一律按病码升序，与列表页同序，前端不重排。各路的
口径与正向页同构：症状反查按源分块回（三源各说各的，不并表）；危险因素反查按 genetic /
exposure 两层分列、两层各自的计数不合成总数（`disease_risk_factor` 没有 `review_status`
列，这一路不过滤——装载器写进来就算）；靶点反查每病带 `score` / `novelty` / `node_used`
（同一病多笔挂载取合成分最高那笔）；药物反查每病带 `entries[]`，即这个药在这一病上的全部
(阶段, 机制) 条目——同药同病可以多期并行。前端加第四个视图 `ReverseView` 一屏五用，器官、
症状、危险因素、研究四块面板的行上各带「反查」链接；五条路径在浏览器里各点过一遍（肺的一个
器官节点 → 1 病、fatigue → 3 病、factor 4802 → 1 病、EGFR → 18 病带 score、
ATEZOLIZUMAB → 16 病带条目数）。`python api/tests/run.py` 到这一批 15,946 条断言、
整跑 56 s；`BACKEND_ONLY` 清空——注册的每条路径都有页面在用。

```
18 个恶性肿瘤基准   etl/onco_etl/targets.py
21 个候选源登记     etl/onco_etl/sources.py   →  MySQL db_ot.source
可达性探针          ops\etl.ps1 probe-reach   →  MySQL db_ot.source_probe_log
专项覆盖度探针      ops\etl.ps1 probe         →  MySQL db_ot.source_probe_log
后端只读接口        ops\api.ps1 serve         →  http://127.0.0.1:8001/api/*
前端站点            ops\web.ps1 build         →  同一个 8001 端口的 /
```

21 个源全部可达（直连为主；托管在 GitHub release 上的 OBO 词表与 Wikidata 要经代理，
具体几个随网络状况变，以最近一次 `probe-reach` 为准）。已过判据的维度：

- **器官树**——SEER 的 ICD-O-3 Site Recode 码表一份文件同时供 anatomy 与 histology：
  82 个 site recode / 332 个拓扑码 / 806 个形态学码，18 病 18/18 挂载到器官级分组。
- **ID 主干**——MONDO 18 个疾病主条目全部解析通过，跨源枢纽定为 NCIt
  （主条目的 NCIT xref 18/18；MONDO 自带的 MESH 只有 7/18、EFO 5/18，文献维带不动。
  GWAS 不受此限——它里面的癌种档以 MONDO ID 为键）。
- **生存率**——SEER Cancer Stat Facts 18 病各一页，17 病带分期别五年生存率（≥3 档）。
  但观测值止于 2018，2019–2023 只有模型趋势线，页面必须标口径；白血病无实体瘤分期，只给全分期一个数。
- **症状**——NCI PDQ 病人版症状小节 L2 规则解析 18/18 病各 ≥4 条、合计 209 条，
  5 病目测 precision 100%；缺的是频率带与中文名列（实测无源，见下）。
- **前沿研究**——三源各 18/18，是 P0 唯一三列判据全过的一批，疾病键只有 Open Targets 能用 ID。

每一维进不进 MVP、以什么口径进，裁定在 [docs/MVP裁定.md](docs/MVP裁定.md)；下面几段是实测口径的展开。

统计层达标（有条件）。年度序列来自 SEER：发病率与死亡率 1975–2024 逐年齐备（死亡 50 年、发病 49 年），
另有 6 组种族/民族与双性别分层；但它的年龄分组只有 8 档宽区间（`<20` 到 `>84`），做不了 5 岁组标化率。
唯一按 5 岁组乃至 1 岁组发布的 GBD，**数值全部在注册登录门后**——词表实测 18 病 18/18 有对应病因档、
中国在内、1990–2021 逐年、155 个年龄组、四个度量齐备，可一个数都取不回来。
5 岁组这一档由 IARC 的 GCO 补上，两个入口都匿名直连：Cancer Today 给中国 18 病的国家级例数、
世界标化率与累积风险（无年龄维、一版一个年份），Cancer Over Time 给 2002–2017 × 18 个 5 岁档 ×
三性别的年龄别率。两条限定随数值一起落库——中国这一路只有发病没有死亡，且是 5 个登记处覆盖约 60%
人口的外推，不与 Today 的全国估算相减。死亡年龄段的中国那一半仍空：备选的 WHO GHO 已实测排除
（AGEGROUP 与 GHECAUSES 两维都齐，可带这两维的 8 个指标只有区域与收入组聚合、中国零行，
而有中国行的 14 个指标一个都没有年龄维），这一维只剩 IHME 注册账号一条路——
P0 的裁定是它不进 MVP，页面按"暂无可靠来源"显示，账号到位后重测。

危险度这一维匿名侧能给出危险因素清单和位点级效应量，但给不出归因强度。GBD 的比较风险评估数值与
统计层撞在同一道 IHME 注册门后——vizhub GBD Compare 的四个数据接口一律 401，匿名开放的只有
回版本号的那一个。能匿名取回的是随 GBD 发布的 A2 交叉表：2390 对病因 × 危险因素关联
（217 个病因 × 88 个危险因素），按 REI 层级剔掉聚合档后 18 病里 **10 病有 ≥3 个独立危险因素**
（肺癌 16、结直肠 11、乳腺 7，脑肿瘤一档都没有）。但表里 Deaths/YLLs/YLDs/DALYs 四列只是
"这个组合有数"的标记，没有一个 PAF 或 RR。

结论相反的 GWAS Catalog 补上强度那一半：匿名整包 1,192,032 行关联里 `OR or BETA` 84.3% 非空、
`95% CI (TEXT)` 75.0% 含可解析区间；按 targets 声明的疾病键判"≥3 个带效应量与区间的独立位点"，
覆盖 **18/18**。其中四病（乳腺、胰腺、食管、子宫体）的关联不挂在主条目上而挂在同级组织学档，
已逐病裁成 `targets.GWAS_URI` 声明（不收分子亚型、癌前与良性档），只认主条目的严格口径 **14/18**
也原样留在探针样本里可对账。代价是它给的是
遗传易感位点而非可干预暴露，`OR` 与 `β` 同列混装、方向只写在文本注记里，效应量类型只能标"未判定"，
而全表没有一个 PAF 列。两半合起来落库的形状是：暴露清单与 role 来自 CRA，位点的 OR/β 与区间来自
GWAS，归因强度两源皆空——危险度页面按"有证据关联、无归因强度"呈现，不做归因分数榜。

前沿研究是 P0 唯一三列判据全过的一批：ClinicalTrials.gov v2、Europe PMC、Open Targets 各自
18/18 达标。18 病按声明查询词合计命中 110,954 项注册试验，其中在招 23,660 项（最少甲状腺 292）、
未招募但在随访 7,449、已完成 46,704；近 5 年文献 765,891 篇（最少子宫体 10,366 篇），
Open Targets 关联靶点 18 病合计 228,551 条、最少的一个病 3,868 个（判据是 10，按 B7c 换档后的口径）。
这一维真正的产出是疾病键的结论：
**只有 Open Targets 能用 ID**——18 病拿 MONDO 号（冒号换下划线）点查全中，带分数的靶点清单
（肺癌前三 EGFR 0.901、KRAS 0.858、ERBB2 0.842）与逐数据源分一次取回；而 CT 的 `fields=` 白名单
里没有可对齐的 MeSH ID，Europe PMC 的 `MH:` 主题词字段只覆盖全库 2.2%，所以那两侧只能按
`targets.py` 里逐病声明的词表查，查询形态本身还是口径：CT 的 `query.cond` 加不加引号是两种检索，
裸写主题词自带 MeSH 展开（18 病比整串加引号多 9,787 项），同义词必须加引号否则退化成词级匹配、
把别的癌种灌进来（实测 8 个词裸写命中超过加引号 2 倍以上）。
两条限定随数值一起落库：文献全文可得率是两个数——按 facet 是 50.8% 开放获取，
按记录级抽样只有 31.7% 能真读到正文（症状维不吃它：B6 实测 PDQ 页面 L2 规则解析就够，
这个数只在将来真要从文献正文里挖症状时才需要重新拿起来）；CT 匿名侧没有地理过滤器
（`filter.geo` 与 `aggFilters=geo` 都被拒），所以"有没有中国参与的试验"只能把地点国家取回自己数，
实测 900 条抽样里 141 条（15.7%）有中国大陆研究地点。Open Targets 整包 56 个数据集 / 1,102 个 parquet / 58.5 GiB，
点查够用，不下载整包；它把乳腺与胰腺落在窄档（自身文献 88 / 382 而父节点 710,750 / 166,685），
B7c 已裁成一份逐病声明 `targets.OT_NODE`：**只换乳腺癌**（`MONDO:0007254`，换后文献 724,160、
在研药 1,036），胰腺不换——它的父节点在研药反而更少（30 对本节点 463），
说明关联量在 MONDO 层级上不单调，不能整维按父节点一刀切。

症状维的一条硬结论：**词表类源给不出常见上皮癌的疾病↔症状注释**——MONDO 的 UBERON 定位与
HP 症状注释各只有约 1% 覆盖，HPO / Orphanet / NCIt 的实测覆盖见
[docs/数据源探针计划.md](docs/数据源探针计划.md)。这一维最后是 NCI PDQ 的病人版页面供上的
（L2 规则解析，18/18），不是某个现成的症状词表。两个缺口按实测留空：
`freq_band`（PDQ 症状小节零百分号，全站只有 Orphanet 能给而它常见上皮癌 0 命中）与
中文症状名（PDQ 只有英文，现成中文清单只有 7/18，四条匿名公开路实测全不达线，
所以 `symptom` 按源分行、不做翻译列）。这也是 P0 必须先跑探针、不先建表的原因。

## 架构

```
┌─ 采集层 etl/onco_etl/ ────────────┐         ┌─ 服务层 api/onco_api/ ─────────────┐
│ sources.py   21 个候选源登记表    │         │ config.py    与采集层读同一份 .env │
│ targets.py   18 病基准清单        │ →MySQL→ │ db.py        会话级只读护栏        │
│ fetch.py     直连→代理三态取数    │  db_ot  │ dimensions.py 十维度量一台聚合     │
│ raw.py       data/raw 归档+sha256 │         │ gaps.py      空态是度量上的谓词    │
│ joblog.py    etl_job_log 运行史   │         │ serialize.py 出处五列 → provenance │
│ probe*       覆盖度探针           │         │ routes/ 七模块二十条 GET         │
│ load/        探针解析 → 业务表行  │         └────────────────────────────────────┘
│ matrix.py    裁定 → 覆盖度文档    │         ┌─ 前端 frontend/ ───────────────────┐
└───────────────────────────────────┘         │ views/   四视图：列表 + 详情 + 榜  │
                                              │          + 反查（五路共用一屏）    │
                                              │ panels/  一屏一面板，研究层共用一台│
                                              │ lib/chart.js  echarts 公共配置     │
                                              │ api/client.js 只发 GET + 路径白名单│
                                              │ build → dist/ 后端同台托管一端口   │
                                              │ dev 5174      /api 代理到 8001     │
                                              └────────────────────────────────────┘
```

五层数据模型：词表层（器官树/症状/危险因素）→ 实体层（疾病主档）→ 关系层（多对多，带 role）→
统计层（长表 + 完整口径维度）→ 证据层（试验、文献、靶点关联；叙述维按实测不进 MVP）。

## 首次准备

```powershell
copy .env.example .env        # 填 DB_* 与 PROXY_URL；API_* 留空即用默认；IHME_USER/IHME_PASS 注册账号后才填
python db/tests/run.py apply db/schema.sql
python db/tests/run.py migrate
ops\etl.ps1 seed-sources
ops\etl.ps1 probe-reach
ops\etl.ps1 probe             # 专项覆盖度探针，不带 --code 就是全跑
```

依赖本机已装（SQLAlchemy、PyMySQL、requests、certifi、lxml、bs4、pandas、openpyxl、FastAPI、Uvicorn，
另有 httpx 供服务层跑测器起 TestClient），不需要 pip install。

## 常用命令

```powershell
python db/tests/run.py status            # 表行数 + 三道门禁（源授权 / 业务表出处列 / 两份 DDL 一致）
python etl/tests/run.py                  # 解析回归：仓库内的上游页面 + 构造的最小页，不联网
python api/tests/run.py                  # 服务层对账：二十条接口返回的每个数字另问一次 SQL，外加一道路径契约（前端用到的 ⊆ 登记清单 ⊆ 真注册的路由，要连着库）
ops\etl.ps1 load --list                  # 有哪些装载器
ops\etl.ps1 load --code disease --offline # 把疾病主档从声明 + MONDO 归档装进 disease
ops\etl.ps1 load --code anatomy --offline # 器官树与组织学：SEER 交叉表 + MONDO 亚部位 → 四张表
ops\etl.ps1 load --code stats --offline  # 统计层：GLOBOCAN + GCO Over Time + SEER → stat_fact 与 survival
ops\etl.ps1 load --code symptoms --offline # 症状：PDQ 英文 + 中文维基条目 + WHO 中文版 → symptom
ops\etl.ps1 load --code risks --offline    # 危险因素：GWAS 关联 + GBD CRA 清单 → risk_factor 与 disease_risk_factor
ops\etl.ps1 load --code research # 研究层：CT 试验 + EPMC 文献 + OT 靶点与药 → 五张表；六台里唯一自己取行的，首跑联网并归档，之后可 --offline
ops\etl.ps1 load --offline               # 全部装载器按注册顺序跑一遍
ops\etl.ps1 load --code anatomy --dry-run # 整批写进去再回滚，只验约束不留下数据
ops\etl.ps1 probe-reach --code mondo     # 只探指定源的可达性
ops\etl.ps1 probe --list                 # 有哪些专项探针
ops\etl.ps1 probe --code mondo --offline # 用 data/raw 归档离线重放，不重新下载
ops\etl.ps1 probe-status                 # 每源每份数据集最近一次裁定
ops\etl.ps1 status                       # 库现状速览
ops\api.ps1 routes                       # 服务层注册了哪几条路径与参数（建 app 但不碰库）
ops\api.ps1 serve                        # 起只读后端，监听 .env 的 API_HOST:API_PORT，文档在 /api/docs
ops\web.ps1 dev                          # 前端开发服务器 http://127.0.0.1:5174，/api 代理到 127.0.0.1:8001（后端要另起）
ops\web.ps1 build                        # 前端出 frontend/dist/；之后 ops\api.ps1 serve 同台托管，站点就在 /
ops\up.ps1 dev                           # 一条命令起两个窗口：8001 只读后端 + 5174 开发服务器
ops\up.ps1 site                          # 一条命令出生产形态：先 build，再同台托管在 8001（站点在 /）
```

`up.ps1` 只是上面那两行的合成，起前做两道只读检查再清一次场：`.env` 由 `api/onco_api/config.py`
自己解（不复制第二份解析代码）、MySQL 端口得在听，然后把要用的端口腾出来——挂着旧代码的后端照样回
200、照样吐对不上仓库的数，所以留着它比停掉它更贵。清场只认得出是本站这套的进程（8001 上是 `python`、
5174 上是 `node`），并打一行「谁被停了」；进程名对不上的一律报出 PID 与名字后退出，不猜该杀谁。
它不跑迁移（改表结构不该发生在「启动」里，仍归 `db/tests/run.py migrate`）、不跑三门禁。

探针跑完用 `cd etl && python -m onco_etl matrix` 重新生成 `docs/数据源覆盖度.md`——
那份文档是脚本从库里出来的，不要手改。

## 结构

| 目录 | 内容 |
|---|---|
| `db/` | `schema.sql` 是全量建表脚本，`migrations/` 是增量变更（`0002` 是 15 张业务表，DDL 与 `schema.sql` 那段同内容、改结构两边一起改，`status` 会比对），`tests/run.py` 是迁移与断言跑测器 |
| `etl/` | `onco_etl` 采集层，`python -m onco_etl` 运行；`probes/` 是覆盖度探针（只写裁定），`load/` 是装载器（复用探针里那份解析写业务表），`--offline` 重放读本机 `data/raw/` 归档 |
| `etl/tests/` | 解析回归：`fixtures/` 存代表页的上游原样字节，`run.py` 先比 sha256 再断言解析结果 |
| `api/` | `onco_api` 服务层，`python -m onco_api serve` 运行；只读（会话级 READ ONLY + 只注册 GET），`dimensions.py` 一台聚合十个维度量、`gaps.py` 把空态写成度量上的谓词；`tests/run.py` 拿真库把响应里的每个数字与 SQL 直查对账 |
| `frontend/` | `src/` 界面层，Vue 3 + Vite，四个视图（列表 / 详情十一屏 / 跨病榜 / 反查五路一屏）、一个 `client.js` 只发 GET；`ops\web.ps1 build` 出的 `dist/` 由服务层在同一个端口托管。跑法与界面上的那几条约定见 [frontend/README.md](frontend/README.md) |
| `docs/` | `数据源探针计划.md`（判据与逐批实测）、`数据源覆盖度.md`（脚本生成，勿手改）、`MVP裁定.md`（P0 出口：建哪些表） |
| `data/` | `raw/` 原始响应归档、`exports/` 待抽查草稿，都不入库不提交 |
| `ops/` | PowerShell 包装脚本 |

## 六条约定

1. `etl/` 与后端互不引用，只通过 MySQL 表结构对话。`db/schema.sql` 是唯一契约，`migrations/` 只把
   已有库前进到它的同一终态（业务表段两边同内容，`status` 逐字比对），两侧都不生成 schema。
   两侧各留一份 `.env` 解析（约三十行重复）是这条约定的代价，不是漏了抽象：让服务层 import 采集层
   换来的是"改一次 `.env` 语义要同时动两个包"，比重复三十行贵。
2. 改表结构一律新增 `db/migrations/NNNN_*.sql`，并在注释里写清改的理由。
3. 写库的时间戳由应用层以本地墙钟格式（`YYYY-MM-DD HH:MM:SS`）写入，一律走 `onco_etl.clock`。
   本机是 UTC+8，带 `Z` 的串会被 MySQL 按字面量存成 UTC 时刻，跨午夜的采样会错位一天。
4. 每个落库的事实都必须能回溯到来源：`source_id` + `extract_method` + `review_status`。
   `source.legal_note` 为空的源不许进采集。
5. 接口与页面三向钉死，跑在 `api/tests/run.py` 里：前端源码里出现的每个后端路径字面量都要在
   `frontend/src/api/client.js` 的清单上，清单上每条都要是真注册的路由，注册了却还没画屏的接口
   要在 `BACKEND_ONLY` 写明理由。`facets` 的轴名必须就是那个接口可请求的参数名——对不上等于
   一排点不动的选项，筛子看着在、其实什么都没筛。
6. 本机端口：后端 8001、前端开发 5174、生产站点仍是 8001（`ops\web.ps1` 那两行）。8000 与 5173
   是同一台机器上另一个项目占着的，换端口要同时改 `.env` 的 `API_PORT` 与
   `frontend/vite.config.js` 的代理目标。
