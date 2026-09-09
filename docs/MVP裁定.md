# MVP 裁定（P0 出口）

P0 的出口判据是"由覆盖度矩阵裁定 MVP 建哪些表"。矩阵本身自动生成，在
`docs/数据源覆盖度.md`（`cd etl && python -m onco_etl matrix`）；这份文档只写裁定：
每一维进不进、以什么口径进、缺的那部分在页面上怎么显示。

输入是 `source_probe_log` 里 16 份内容级探针裁定 + 21 行候选源登记表。
未做内容级实测的源不在此裁定的证据范围内，它们的状态一律 `paused` 而不是 `rejected`。

## 一、逐维裁定

| 站点维 | 裁定 | 供数源与实测 | 落库/展示必须带的口径 |
|---|---|---|---|
| 身份与 ID 主干 | **进** | `mondo` `ok` 18/18（63,278 term，主条目 18/18 解析） | 枢纽声明是 NCIt，但**不抓 NCIt 全量**（237 MiB）：跨源 ID 一律取 MONDO 主条目的 xref 列，实测 NCIT xref 18/18、ICD-9 另带出 145 个亚部位 term 供下钻。MONDO 自带的 MESH 只 7/18、EFO 5/18、UMLS 17/18，不能当主干 |
| 器官 / 关联器官 | **进** | `icdo3_seer` `ok` 18/18 | 82 site recode / 332 拓扑码 / 806 形态学码一份文件同时供 anatomy 与 histology；亚部位用 MONDO 的 ICD-9 亚部位 term，不用 NCIt 定位轴 |
| 组织学 | **进**（自建） | 同上（806 个形态学码是全局码表，无逐病格） | 18 病的组织学分类按 ICD-O-3 形态学段自己展开，矩阵里这一维没有逐病证据列，别把它当成"已实测覆盖" |
| 症状清单 | **进** | `nci_pdq_html` `ok` 18/18（209 条，5 病目测 72/72，precision 100%） | L2 规则解析，非 NER；`source_id` 必须随行落库。探针的 209 条落库时按 `(disease_id, name_lang, name)` 归一成 185 行（24 条是同一症状在多个组织学档各写一遍），另有 4 条从散文句抠出的不进——句子不是症状项 |
| 症状中文名 | **部分进** | `wikidata` `partial` 7/18（PDQ 英文 18/18 + 中文 7/18：中文维基 5 病 ∪ WHO 中文版 3 病） | 按源分行 `(disease_id, source_id, name_lang, name)`，**不做翻译列**。11 病没有中文清单，页面按病显示"暂无可靠中文来源"而不是留白 |
| 叙述 / 介绍段 | **不进** | `who_factsheet` `partial` 4/18，判据线 ≥12/18 | 全站 fact sheet 只有 73 个主题、癌种专页 18 病里 4 病有 ≥3 条要点清单。 disease 页不给叙述段落，或只给一句由症状/统计维拼出来的中性导语 |
| 危险因素清单 | **进**（两层） | GWAS Catalog `partial` 18/18（按 `targets.GWAS_URI` 声明档；只认主条目是 14/18）→ C2e 落 6,208 行；GBD CRA 探针判 `blocked`（那条判据要的是带效应量的 ≥3 个因素），但同一趟实测出的匿名 A2 清单骨架可用：33 个暴露 × 71 条 cause×Risk 对应 → C2e 落 71 行 | 一行是一个**关联**不是一个位点（键含 STUDY ACCESSION 与 P-VALUE，实测按 PUBMEDID 构造会折掉 496 条真关联）。`role='genetic'` 是遗传易感性不是可干预暴露，页面文案不许写成"危险因素排行"；`role='exposure'` 只有清单没有强度，源里 Deaths/YLLs/YLDs/DALYs 四列的 `X` 标的是"这个组合有数"，不是效应量，别拿它当强度排序 |
| 危险因素归因强度（PAF） | **不进** | `gbd_results` `blocked` 0/18；`gbd_cra` 的效应量同一道门（vizhub 数据面 `/api/metadata`、`/api/data`、`/api/hierarchy`、`/api/data/version` 四路由全 401） | 缺的只剩强度这一半，清单两半已在 C2e 落库。`paf` / `paf_basis` 建而不填，UI 不画数值榜，标"需 IHME 授权" |
| 发病量（国家单点） | **进** | `globocan` `ok` 18/18 | 2024 年估算、国家级单点，34 个癌种码；现患(type 2)每个 (cancer,sex,type) 键实测单行且不带期间标签——1 年 / 3 年 / 5 年现患混在同一个数里，口径只在 `description.prevalence` 那一句，落库时随行带上且不许标成「5 年现患」 |
| 发病年龄组 / 趋势 | **进**（双列） | `gco_overtime` `partial` 18/18 有值 | 中国是 5 个登记处覆盖 60% 人口的外推、最新一年 2017；与 Cancer Today 的国家级估算不同源，**两列分开存、不可相减成趋势** |
| 死亡年龄组（中国） | **不进** | `who_gho` `empty` 0/18、GBD `blocked` | 判据两半（≥10 年龄组 × 中国行）在 GHO 里从不同时出现在同一指标上；GBD 那半等注册账号后重测 |
| 五年存活率 | **进** | `seer_statfacts` `ok` 18/18 | 分期档 17/18（leukemia 整页无分期表，源本身不给）；观测窗止于 2018、2019–2023 是拟合值；**美国 SEER 口径，页面必须写明不是中国数据** |
| 在招试验 | **进** | `ctgov_v2` `ok` 18/18（探针在招 23,660 项；同口径全状态命中 110,954）→ C2f 落 23,705 行 | 疾病键只能按 `targets.search_terms` 声明词查。落的是在招三档，一行是"一个试验命中一个病"（23,705 行＝19,254 个试验，其中 2,469 个跨病出现），页面不许把行数报成试验数；历史累计做过多少试验得另问一次全状态查询。匿名侧无地理过滤器（`filter.geo` 与 `aggFilters=geo` 都被拒），"中国参与"只能把 `locations.country` 数组落库再前端筛；抽样实测有数可筛——900 条里 141 条带中国大陆研究地点（15.7%），页面别把它写成"几乎无中国试验" |
| 前沿文献 | **进** | `europepmc` `ok` 18/18（近 5 年 765,891 篇，每病都有 OA 命中）→ C2f 每病取相关度前 500、落 9,000 行 | 同上按声明词查；`MH:` 主题词路只覆盖全库 2.2%，不用它做主键。这张表是**上限样本不是全量**，页面写"库内共 N 篇，这里取相关度前 500"，N 取 `stat_fact` 的 `publication_count` 不取行数。期刊名是 `journal` 列（18 病 1,793 个刊名，预印本没有期刊留空），别拿 EPMC 的库别代码 MED/PPR/PMC/AGR 当期刊 |
| 靶点 / 药 | **进** | `opentargets` `ok` 18/18（合计 228,551 条关联、6,323 个在研药）→ C2f 落 `disease_target` 28,919 行（靶点节点 7,098）、`drug` 6,309 行 | 查询节点按 `targets.OT_NODE` 覆盖（目前只有乳腺癌换宽档），换档前后的数字都在探针 message 里可对账。关联按 `score ≥ 0.1` 收，阈下那近 20 万条是共现级、页面按"可讨论靶点数"讲；药物一行是一个 (病, 药, 阶段)（源给的 6,323 行三元组收拢成 6,309 行），`phase` 是源原文不是有序档，不能排成"临床期数越早越前沿" |
| 症状分析（症状→病反查） | **进** | 复用 `symptom` 表 | 英文路 18/18 可反查；中文路只有 7/18，反查结果按 `name_lang` 分组显示，不混排 |
| 器官 / 症状 / 危险因素 / 靶点 / 药物反查 | **进**（危险因素两层各自反查） | 上面各行 | 反查命中率随正向维的缺口一起不一致，UI 要在结果数少的维度上显示覆盖边界，不能默认"没有结果＝没有关联"。危险因素这一维反查出来的是 `risk_factor` 节点（`genetic_locus` 是位点标签、`exposure` 是 GBD 暴露名），两层不同形状，结果页按 `role` 分栏不混排 |

## 二、页面上要显示"暂无可靠来源"的地方

1. 中国死亡年龄组（全 18 病）。
2. 危险因素的归因强度 PAF（全 18 病）。清单两层都已落库，但暴露那层薄：17/18 病有行（brain 在源里一行都没有），其中只有 10/18 病达到探针那条"≥3 个独立暴露"的线，其余病只有 1–2 个——按实有条数显示，不补 0、不与遗传关联排成同一张榜。
3. 症状频率带 `freq_band`（全 18 病——PDQ 症状小节百分号出现数实测 0，全站只有 Orphanet 能给而它常见上皮癌 0 命中）。
4. 11 病的中文症状名：liver, stomach, esophagus, prostate, cervix, ovary, thyroid, bladder, kidney, brain, nhl。
5. 疾病页的叙述/介绍段（全 18 病）。
6. 白血病的分期别 5 年生存率（源本身不提供分期表）。

这六处一律走同一个前端约定：空态写"暂无可靠来源"+ 该维的判据缺在哪，不画 0、不画占位线。

## 三、五项悬置的收口

### 1. IHME 账号与取数路（账号已就位，仍卡着人——换 token 只能浏览器里点）

裁定是"开，两维一起补"（死亡年龄组 + 危险因素归因强度）。**账号已注册、凭据已进 `.env`**
（2026-09-08 实测两个键都是非空真值）。前置因此从"等人注册"换成"等取数路实现"：
`gbd_results` 的状态仍是 `paused`（2026-09-08 C2e 后 `gbd_cra` 已转 `active`——它匿名可取的
A2 清单够装暴露那一层，授权门后剩的只是强度，见 §一 与 §二.2），要接的是"人在浏览器里登录
换到 token、脚本带着 token 取数"这一段——已实测 `authorize` 只认 v2 端点加 PKCE 授权码流，
隐式流被拒，所以换 token 这步代不了浏览器，探针只能吃换回来的 token。

注册入口已实测定位（2026-09-08）。**没有独立的注册页**，这就是找不到入口的原因：

- 官方口径：GBD Results 工具页写着 "We require all users to create an account in order to
  search and download GBD data."，配套说明在 <https://www.healthdata.org/account-support>
  ——注册才能检索下载；**商用不在非商用条款内**，需联系 IHME Client Services 谈许可。
- 实际入口：`https://vizhub.healthdata.org/gbd-results/`（或 gbd-compare）打开即弹一个
  "要下载估算并访问其他功能，请注册并登录"的对话框，卡上有 **注册** / **登录** 两个按钮和条款链接——
  这是最短的一条路，不必去找顶栏菜单。窄屏下顶栏收进汉堡按钮，
  Account 菜单里的 Login / Register 是同一件事的第二条路。
- 登录与注册是同一条流：Account 菜单的 Login 与 Register 都调 MSAL 的 `loginPopup`，
  Register 只是多带一个 `state={"mode":"signup"}`。授权服务器
  `https://login.healthdata.org/a07655f6-e482-42f3-8b30-6b7d009f813d/B2C_1A_SIGNUP_SIGNIN`
  （策略名就叫 SIGNUP_SIGNIN），clientId `9e66b6a3-5d2e-400f-b812-f60f441d5041`，
  scope `https://ihmecsu.onmicrosoft.com/data-api/data.read`。
- 该授权页实测可达且带自助注册链接：`GET .../oauth2/v2.0/authorize`（PKCE 授权码流）回 200，
  页面配置里 `showSignupLink=True`、文案 "Don't have an account? **Sign up now**"，
  表单模板由 IHME 自己的 `https://azure-aad-b2c-templates.ihme.services/SignIn.html` 提供。
- 三条走不通的路，别再试：不带 `code_challenge` → `AADB2C99059`；用 v1 端点
  `.../oauth2/authorize` → `AADB2C90012`（这个 scope 只认 v2）；用 implicit
  （`response_type=token`）→ `AADB2C90057`（应用未开隐式流）。
  第四条：在 authorize 链接后面硬加 `&link_type=signup` 去 GET，回的是 "We can't sign you in"
  错误页——自助注册卡只由那张登录卡片以 POST 触发。所以注册这一步只能人从站内点进去，
  探针与脚本都代不了。
  手工拼的链接能打开注册卡但换不到 token（code_verifier 不在 MSAL 缓存里），
  所以**要走站内那条**，别收藏手工链接。
- 凭据落地：`.env.example` 有 `IHME_USER` / `IHME_PASS` 占位（不进仓库），本机 `.env` 已填真值
  （2026-09-08 实测两键非空且不是占位串）。凭据在库里不等于取数路通——上面那段换 token 还没实现。

### 2. WHO / GBD 的非商用边界

维持"先按非商用"。真要商用时的出局清单比想象的小：叙述维本来就不进 MVP，
WHO 那一半还会带走 3 病（breast_female / colorectum / lung）的中文症状清单；
GBD 两维本来就因授权门没进。所以商用化的净损失只有这 3 病中文清单，
症状维主体（PDQ 英文 18/18 + 中文维基 5 病 CC BY-SA）不受影响。

### 3. Open Targets 窄档重声明

只换乳腺癌：`breast_female` 的查询节点从 `MONDO:0004379`（female breast carcinoma，
assoc 643 / lit 88 / drugs 0）改到 `MONDO:0007254`（breast cancer，
assoc 17,064 / lit 724,160 / drugs 1,036）。代价如实记在 `targets.py`：
这一档不分性别，`sex=female` 的约束仍由 icd10 / SEER / GCO 那几列保证。

`pancreas` 不换：它自己 10,528 靶点 / 463 个在研药，而父节点 pancreatic neoplasm 是
10,964 / 30——父节点更空，换过去研究层反而退化；它文献偏少（382 篇）由 EPMC 那一列补。
这两例说明关联与文献量在 MONDO 层级上**不单调**，所以不做"整维统一按父节点"这种一刀切。

落地形态是 `targets.OT_NODE` 一份声明 + `ot_node()` 一个解析函数，探针只读它、
不自己按名字猜档；重跑时探针会把被覆盖掉的那一档一并取回并在 message 里报换前换后。

### 4. croissant 的 CC0 与登记表的"数据随上游"

两个声明并存是真的，不是抄错：Open Targets 的 `croissant.json` 顶层 `license`
是一个 URL 字符串 `https://creativecommons.org/publicdomain/zero/1.0/`（CC0），
而 59 个 distribution 条目**没有一个自带许可字段**——整份清单只在顶层说了一次话。

裁定：登记表保留更严的那句"平台 Apache 2.0，数据随上游"作为署名口径，
把 croissant 的 CC0 记为第二个冲突声明（写在 `opentargets` 的 `legal_note` 里），
站点署名两句一起带。哪一层哪天改了，以探针 message 里的 license 字段对账。

### 5. `source.status` 全部离开 candidate

21 行原裁定翻成 12 `active` / 8 `paused` / 1 `rejected`；C2e 落库暴露清单后 `gbd_cra` 转
`active`，`source` 表现值是 13 / 7 / 1。判据写在 `sources.py` 的模块 docstring 里：

- `active`（13）：mondo, icdo3_seer, nci_pdq_html, seer_statfacts, globocan, gco_overtime,
  gwas_catalog, gbd_cra, ctgov_v2, europepmc, opentargets, who_factsheet, wikidata。
  其中 who_factsheet 与 wikidata 都是 `partial`——进 `active` 是因为它们各自还有
  达标的贡献（WHO 的 3 病中文清单、维基的 5 病中文清单与 CC0 英文名），
  不是因为整维过线；`gbd_cra` 同理：探针判 `blocked` 的是效应量那一面，匿名 A2 清单
  这一面已经够装 `role='exposure'` 的表，剩下的缺口写在它的 evidence 里而不是靠状态位表达。
- `paused`（7）：gbd_results（等浏览器换到 token 才能补数值面）；
  icdo32_naaccr, mesh, ncit, orphanet_product4, hpoa, monarch_common_disease
  （只做过可达性，没做过内容级实测——MVP 不依赖它们，要启用得先补探针）。
- `rejected`（1）：who_gho（内容级探针判空 0/18，且这一维换源补不上）。

## 四、P1 建表时要一并处理的事

- ✅ GWAS 的 4 病（breast_female, uterus, pancreas, esophagus）按主条目不达线——这条已在
  P1 的 C1a 收口：裁成 `targets.GWAS_URI` 逐病声明（形状同 `OT_NODE`，不收分子亚型、癌前
  与良性档），矩阵那一列改读"主条目 + 声明档"口径 = 18/18，只认主条目的 14/18 原样留在
  探针 sample 的 `pass_main`。三口径的分工与逐病代价写在 `docs/数据源探针计划.md` 的 B4 那节。
- ✅ `gbd_cra` 在 P0 是按"取不到 PAF"整源挂起的，但判据取不到的那一面（vizhub 数据面四路由
  全 401）与它匿名可取的 A2 清单是两件事——清单里没有效应量，不等于清单本身不能装表。
  这条已在 P1 的 C2e 收口：33 个暴露 × 71 条 cause×Risk 对应按 `role='exposure'` 落库，
  源状态转 `active`，授权门后仍缺的只有 `paf` / `paf_basis`。以后再有"某源一面 blocked"，
  先分开判"这一面能不能装表"，别整源挂起。
- SEER 年龄组只有 8 档宽分组，画 5 岁组标化率要另走 SEER*Explorer 的未公开 JSON 接口。
- 六个 `paused` 的码表源如果 P1 里哪个维要启用，得先补一支内容级探针，
  否则矩阵那一列永远没有逐病格。
- ✅ SEER 的 18 页 HTML 归档只存在于本机（`data/raw/` 整个在 `.gitignore` 里），而
  `legal_note` 要求"表结构改版频繁、解析器必须留 fixture 回归"——这条已在 P1 的 C1b 收口：
  4 页上游原样字节检进 `etl/tests/fixtures/seer_statfacts/`，判据分支各占一页
  （`lungb` 费率表的 Males/Females `<h5>` 证据链、`leuks` 无分期表走豁免、
  `nhl` 同为血液肿瘤却有 Ann Arbor 5 档、`prost` 没有 `<h5>` 性别只有口径行）。
  `manifest.json` 带着 `dataset_release.id` / `source_probe_log.id` 与逐页 sha256，
  `python etl/tests/run.py` 先比字节再断言解析结果，顺当上游漂移检测；整条目录在
  `.gitattributes` 里按二进制处理，否则换机器 checkout 会被 CRLF 归一改成另一份输入。
  归档该不该进仓库仍是按体积逐源判的（MONDO 那 51 MB 不适用这条），且 fixture 只保护解析回归，
  18 页的覆盖裁定还是要实跑重取。
- **CMeSH 中文医学主题词表**（IMICAMS 维护、NLM 有分发）是唯一没测过的中文症状名候选，
  可达性与许可都未测。它是 P1 之后要补中文名列时的第一站，本裁定不含它。

## 五、C1c 落库形状：维度 → 表

§一 那 17 行裁定，落到 `db/migrations/0002_business_tables.sql` 的 15 张表上是这样：

| 维度 | 表 | 裁定里那句口径落在哪一列 |
|---|---|---|
| 身份与 ID 主干 | `disease` | 一行一病，是 `targets.py` 的库内镜像加 MONDO 实测解析结果；`ncit_id` 是跨源枢纽，`xrefs json` 装稀疏码 |
| 关联器官 | `anatomy_node` + `disease_anatomy` | `anatomy_node.kind` 分 site_recode 与亚部位 term，`disease_anatomy.role` 分 primary 与 subsite，`basis` 记挂载依据（ICD-O-3 相交 / MONDO 的 ICD-9 xref）。亚部位只收 ICD-9 为它单开了部位档的 term（带小数点、末位非 .8/.9、且这一档没被同病别的 term 共用）——145 个候选留 51 个，血病三台整维跳过（ICD-9 200–208 章编的是细胞类型不是部位）；实测 82 节点 + 51 节点，逐病 primary 1–5 条 |
| 组织学 | `histology_code` + `disease_histology` | `basis='via_site_recode'`——这个映射是自己从交叉表推出来的，不是源说过。只收行为码 /3（803 个码里 657 个），逐病展开 129–212 条 |
| 症状清单 + 中文名 | `symptom` | 按源分行，`name_lang` 分 en/zh；`source_id`、`source_url`、`anchor` 随行，可点回原文 |
| 危险因素（两层） | `risk_factor` + `disease_risk_factor` | `role` 分 genetic/exposure，`uri_tier` 分主条目与声明档，`p_value_text` 与 `pvalue_mlog` 两列分开存。C2e 实测：节点 3,049 位点 + 33 暴露（基因整串当一个节点，多基因分号串不拆；`MAPPED_GENE` 空的 613 行退到 SNPS），关系 6,208 行遗传（18/18 病＝主条目 3,651 行 16 病 + 声明档 2,557 行 4 病）+ 71 行暴露（17/18 病） |
| 发病量 / 年龄组 / 趋势 | `stat_fact`（长表） | `estimate_basis` 就是"两列分开存、不可相减成趋势"那一句的落点。装的是 GLOBOCAN 国家单点、GCO 逐年 × 18 档 5 岁组、SEER 的新发率与死亡率年度序列（观测与拟合分 `registry_cohort` / `model_trend`）与 SEER 的 8 档宽年龄组构成 |
| 五年存活率 | `survival` | `stage_scheme` 分 SEER 汇总档与 Ann Arbor，`is_observed` 分开观测值与拟合值。整维三层都在这一张表：分期档、At a Glance 的全期头条、5-Year Relative Survival 的逐年序列——`stat_fact` 不重复写同一个数（实测一次装载里逐格相同的有 1674 行） |
| 在招试验 | `trial` | 只建 CT 白名单实测到的列，没有日期列。C2f 实测：23,705 行＝19,254 个 NCT（2,469 个跨病出现，最多一个试验挂在 17 个病上）；只落在招，三档全在 `status_bucket='active'` 一档，`why_stopped` 整列空（那句只有终止试验才有）；`enrollment` 23,704/23,705 行是估算值，估算还是实际整块留在 `design_info` |
| 前沿文献 | `publication` | `ext_key` 是 UPSERT 键（pmid→doi→标题哈希；实测 697 行没 pmid，其中 540 行退 doi、157 行只能哈希），计数走 `stat_fact` 的 `query_count`。落的是每病相关度前 500、共 9,000 行；`journal` 存刊名（8,448 行有值、1,793 个刊名、最长 221 字符），预印本留 NULL |
| 靶点 / 药 | `target` + `disease_target` + `drug` | `node_used` 记下这个病用的是宽档还是主条目。C2f 实测：228,551 条关联里 `score ≥ 0.1` 的 28,919 行进 `disease_target`，节点只从这些关系行长出来（7,098 个，所以两个数对得上）；`drug` 6,309 行是源 6,323 行三元组按 (病, 药, 阶段) 收拢来的，机制并成数组（4,128 行有机制），`phase` 存源原文 11 个取值 |
| 五路反查 | 复用上面各表，无独立表 | `symptom` 上另有 `idx_symptom_lookup`，反查按 `name_lang` 分组 |

没有表的维：叙述/介绍段（裁成不进 MVP，所以不是"一列空着"而是整个维缺席）、
中国死亡年龄组与 PAF（`stat_fact` 里对应 metric 零行、`disease_risk_factor.paf` 空）。

三条横切约定：

1. 每张事实表都带 `source_id` + `dataset_release_id` + `extract_method` + `review_status` +
   `loaded_at`。少一列就等于给"这个数哪来的"留下一张回答不了的表，`python db/tests/run.py status`
   扫的就是这五列齐不齐。
2. 进唯一键的列一律 `NOT NULL DEFAULT ''`（年份用 0），不用 NULL——MySQL 的唯一索引允许多个
   NULL，装载器重跑同一份发布就会插出重复行。
3. 建而不填的列等于页面上的空态：`symptom.freq_band`、`disease_risk_factor.paf` / `paf_basis`、
   `anatomy_node.label_zh`、`risk_factor.label_zh`。装载器不写它们，前端按 §二 的约定显示。
