# MVP 裁定（P0 出口）

P0 的出口判据是"由覆盖度矩阵裁定 MVP 建哪些表"。矩阵本身自动生成，在
`docs/数据源覆盖度.md`（`cd etl && python -m onco_etl matrix`）；这份文档只写裁定：
每一维进不进、以什么口径进、缺的那部分在页面上怎么显示。

输入是 `source_probe_log` 里 17 份内容级探针裁定 + 21 行候选源登记表。
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
| 危险因素清单 | **进**（两层） | GWAS Catalog `partial` 18/18（按 `targets.GWAS_URI` 声明档；只认主条目是 14/18）→ C2e 落 6,208 行；GBD CRA 探针判 `blocked`（那条判据要的是带效应量的 ≥3 个因素），但同一趟实测出的匿名 A2 清单骨架可用：33 个暴露 × 71 条 cause×Risk 对应 → C2e 落 71 行 | 一行是一个**关联**不是一个位点（键含 STUDY ACCESSION 与 P-VALUE，实测按 PUBMEDID 构造会折掉 496 条真关联）。`role='genetic'` 是遗传易感性不是可干预暴露，页面文案不许写成"危险因素排行"；`role='exposure'` 的强度已由下一行的 PAF 补上；CRA 源里 Deaths/YLLs/YLDs/DALYs 四列的 `X` 标的是"这个组合有数"，不是效应量，别拿它当强度排序。暴露那层薄：17/18 病有行（brain 在源里一行都没有），只有 10/18 病达到"≥3 个独立暴露"的判据线，其余病只有 1–2 个——按实有条数显示，不补 0、不与遗传关联排成同一张榜 |
| 危险因素归因强度（PAF） | **进**（2026-09-10 补） | `gbd_results` 授权取数 `ok` 18/18（探针 rows_seen 1,164；71/71 条暴露关系全带值）→ 71 行 `paf` 落库 | `paf` 存年龄标化 PAF ×100 的百分数（两位小数，值域 −7.33–100.00，3 个负值是保护方向），`paf_basis` 固定"GBD 2023 Deaths 年龄标化 2021"。PAF 是人群归因分数，与遗传层的 OR/p 值不是同一种数，两榜不混排；brain 无暴露行所以连 PAF 一起缺席（见上一行） |
| 发病量（国家单点） | **进** | `globocan` `ok` 18/18 | 2024 年估算、国家级单点，34 个癌种码；现患(type 2)每个 (cancer,sex,type) 键实测单行且不带期间标签——1 年 / 3 年 / 5 年现患混在同一个数里，口径只在 `description.prevalence` 那一句，落库时随行带上且不许标成「5 年现患」 |
| 发病年龄组 / 趋势 | **进**（双列） | `gco_overtime` `partial` 18/18 有值 | 中国是 5 个登记处覆盖 60% 人口的外推、最新一年 2017；与 Cancer Today 的国家级估算不同源，**两列分开存、不可相减成趋势** |
| 死亡年龄组（中国） | **进**（2026-09-10 补） | `who_gho` `empty` 0/18 → `gbd_results` 授权取数 `ok` 18/18（951 行 ZIP，19 病因 × 3 性别 × 20 档里零死亡的档不在文件里）→ 319 行落库（both 236 / female 67 / male 16，每病 16–20 档） | GBD 2023 Deaths、2021 年、20 档年龄、`year=2021` 不走 0 哨兵；无全年龄行，缺档全在低龄段且是零死亡档；构成比＝该档死亡数/在场档合计（锚：410 Neoplasms / Both 20 档求和 2,401,092.52＝全年龄单行）；`estimate_basis='national_estimate'` |
| 五年存活率 | **进** | `seer_statfacts` `ok` 18/18 | 分期档 17/18（leukemia 整页无分期表，源本身不给）；观测窗止于 2018、2019–2023 是拟合值；**美国 SEER 口径，页面必须写明不是中国数据** |
| 在招试验 | **进** | `ctgov_v2` `ok` 18/18（探针在招 23,660 项；同口径全状态命中 110,954）→ C2f 落 23,705 行 | 疾病键只能按 `targets.search_terms` 声明词查。落的是在招三档，一行是"一个试验命中一个病"（23,705 行＝19,254 个试验，其中 2,469 个跨病出现），页面不许把行数报成试验数；历史累计做过多少试验得另问一次全状态查询。匿名侧无地理过滤器（`filter.geo` 与 `aggFilters=geo` 都被拒），"中国参与"只能把 `locations.country` 数组落库再前端筛；抽样实测有数可筛——900 条里 141 条带中国大陆研究地点（15.7%），页面别把它写成"几乎无中国试验" |
| 前沿文献 | **进** | `europepmc` `ok` 18/18（近 5 年 765,891 篇，每病都有 OA 命中）→ C2f 每病取相关度前 500、落 9,000 行 | 同上按声明词查；`MH:` 主题词路只覆盖全库 2.2%，不用它做主键。这张表是**上限样本不是全量**，页面写"库内共 N 篇，这里取相关度前 500"，N 取 `stat_fact` 的 `publication_count` 不取行数。期刊名是 `journal` 列（18 病 1,793 个刊名，预印本没有期刊留空），别拿 EPMC 的库别代码 MED/PPR/PMC/AGR 当期刊 |
| 靶点 / 药 | **进** | `opentargets` `ok` 18/18（合计 228,551 条关联、6,323 个在研药）→ C2f 落 `disease_target` 28,919 行（靶点节点 7,098）、`drug` 6,309 行 | 查询节点按 `targets.OT_NODE` 覆盖（目前只有乳腺癌换宽档），换档前后的数字都在探针 message 里可对账。关联按 `score ≥ 0.1` 收，阈下那近 20 万条是共现级、页面按"可讨论靶点数"讲；药物一行是一个 (病, 药, 阶段)（源给的 6,323 行三元组收拢成 6,309 行），`phase` 是源原文不是有序档，不能排成"临床期数越早越前沿" |
| 症状分析（症状→病反查） | **进** | 复用 `symptom` 表 | 英文路 18/18 可反查；中文路只有 7/18，反查结果按 `name_lang` 分组显示，不混排 |
| 器官 / 症状 / 危险因素 / 靶点 / 药物反查 | **进**（危险因素两层各自反查） | 上面各行 | 反查命中率随正向维的缺口一起不一致，UI 要在结果数少的维度上显示覆盖边界，不能默认"没有结果＝没有关联"。危险因素这一维反查出来的是 `risk_factor` 节点（`genetic_locus` 是位点标签、`exposure` 是 GBD 暴露名），两层不同形状，结果页按 `role` 分栏不混排 |

## 二、页面上要显示"暂无可靠来源"的地方

1. 症状频率带 `freq_band`（全 18 病——PDQ 症状小节百分号出现数实测 0，全站只有 Orphanet 能给而它常见上皮癌 0 命中）。
2. 11 病的中文症状名：liver, stomach, esophagus, prostate, cervix, ovary, thyroid, bladder, kidney, brain, nhl。
3. 疾病页的叙述/介绍段（全 18 病）。
4. 白血病的分期别 5 年生存率（源本身不提供分期表）。

（原先还有两条——中国死亡年龄组与 PAF——2026-09-10 走通 IHME 授权取数后补上了，
两条空态随之删掉；暴露那层薄、按实有条数显示的那句提醒仍在 §一 危险因素清单行。）

这四处一律走同一个前端约定：空态写"暂无可靠来源"+ 该维的判据缺在哪，不画 0、不画占位线。

从 D3a 起这一节是接口算出来的，不是前端抄下来的：`/api/diseases` 每行与详情的 `gaps` 数组每条带
`dim` / `scope` / `label` / `text` / `basis` 五件，第 3 项那种"整个维缺席"是 `scope=dimension`、
挂在 `/api/meta` 上。同一结构里另有两处不在这一节而写在 §五：`anatomy_node.label_zh` 与
`risk_factor.label_zh`（整维级），以及"这一病没有亚部位下钻"（逐病，实测 6 病命中——三台是血液
系统肿瘤，另三台是卵巢、前列腺、甲状腺，所以这两类各一句，不并成"血病都不给亚部位"）。
上面这份名单就是这一层的对账依据：`python api/tests/run.py` 里"11 病无中文症状名 / 1 病无分期档 /
全 18 病缺频率带"那几条断言照的是这一节，不是照代码。

D4a 起了界面，这一节在页面上仍只有一处实现：空态组件渲染 `gaps[]` 的 `label` / `text` / `basis`
三件，前端不另判第二次"这一维是不是空"；数值缺席显示「—」而不是 0，`year=0` 那一类单点不折成线。
所以这一节的文案改了，页面跟着改而不用动代码。

## 三、五项悬置的收口

### 1. IHME 账号与取数路（2026-09-10 走通：换 token 是浏览器一次性人工动作，取数全程可重放）

裁定是"开，两维一起补"（死亡年龄组 + 危险因素归因强度），**已收口**：`gbd_results` 转
`active`，两维落库（§一 那两行）。走通的分界线就是原来卡住的那句"人在浏览器里登录换到
token、脚本带着 token 取数"，拆开后是这样两半：

- **换 token（唯一要人做的一步）**：站内 Account 菜单的 Login 走 MSAL `loginPopup`
  （PKCE 授权码流）人工点一次，`data.read` scope 的 access_token 与 refresh_token 落
  `data/raw/gbd_results/auth.json`（不进仓库）。B2C 的 v2.0 token 端点当时实测 ReadTimeout，
  refresh_token 的脚本化刷新暂缓——token 过期后重做的是"浏览器里再点一次"，不是整条取数路。
- **取数（可编程重放）**：`POST php/download.php` 带该 token 提交任务（PHP 数组语法
  urlencoded；实测这一步只认浏览器环境，Python 与 curl_cffi 直接 POST 一律 401）→ 回 202
  与 taskID；任务参数哈希在 IHME 那头有缓存，所以 taskID 固化进脚本即可重放。轮询与下载
  反而不需要 token：`php/get_download_result.php?taskID=` 与 `dl.healthdata.org` 的 ZIP
  都是匿名 200。两份归档的 taskID 写死在 `etl/onco_etl/probes/gbd_results.py`。

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
  （2026-09-08 实测两键非空且不是占位串）。换 token 那一步人工输的就是这两条。

### 2. WHO / GBD 的非商用边界

维持"先按非商用"。真要商用时的出局清单比想象的小：叙述维本来就不进 MVP，
WHO 那一半还会带走 3 病（breast_female / colorectum / lung）的中文症状清单；
GBD 两维虽已走授权取数落库，但**商用不在 IHME 非商用条款内**（§三.1），得另谈许可——
在许可谈下来之前，商用化会带走死亡年龄组与 PAF 两维。所以净损失按谈成与否分两档：
WHO 那 3 病中文清单是确定会走的，GBD 两维是"许可谈成就不走"；
症状维主体（PDQ 英文 18/18 + 中文维基 5 病 CC BY-SA）两种情形下都不受影响。

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
`active`（13 / 7 / 1），2026-09-10 `gbd_results` 授权取数走通后再转 `active`，`source` 表
现值是 14 / 6 / 1。判据写在 `sources.py` 的模块 docstring 里：

- `active`（14）：mondo, icdo3_seer, nci_pdq_html, seer_statfacts, globocan, gco_overtime,
  gwas_catalog, gbd_cra, gbd_results, ctgov_v2, europepmc, opentargets, who_factsheet, wikidata。
  其中 who_factsheet 与 wikidata 都是 `partial`——进 `active` 是因为它们各自还有
  达标的贡献（WHO 的 3 病中文清单、维基的 5 病中文清单与 CC0 英文名），
  不是因为整维过线；`gbd_cra` 同理：探针判 `blocked` 的是效应量那一面，匿名 A2 清单
  这一面已经够装 `role='exposure'` 的表，剩下的缺口写在它的 evidence 里而不是靠状态位表达。
- `paused`（6）：icdo32_naaccr, mesh, ncit, orphanet_product4, hpoa, monarch_common_disease
  （只做过可达性，没做过内容级实测——MVP 不依赖它们，要启用得先补探针）。
  `gbd_results` 已于 2026-09-10 转出此列：授权取数走通、两维落库（§三.1）。

## 四、P1 建表时要一并处理的事

- ✅ GWAS 的 4 病（breast_female, uterus, pancreas, esophagus）按主条目不达线——这条已在
  P1 的 C1a 收口：裁成 `targets.GWAS_URI` 逐病声明（形状同 `OT_NODE`，不收分子亚型、癌前
  与良性档），矩阵那一列改读"主条目 + 声明档"口径 = 18/18，只认主条目的 14/18 原样留在
  探针 sample 的 `pass_main`。三口径的分工与逐病代价写在 `docs/数据源探针计划.md` 的 B4 那节。
- ✅ `gbd_cra` 在 P0 是按"取不到 PAF"整源挂起的，但判据取不到的那一面（vizhub 数据面四路由
  全 401）与它匿名可取的 A2 清单是两件事——清单里没有效应量，不等于清单本身不能装表。
  这条已在 P1 的 C2e 收口：33 个暴露 × 71 条 cause×Risk 对应按 `role='exposure'` 落库，
  源状态转 `active`。当时授权门后缺的 `paf` / `paf_basis` 已由 `gbd_results` 于 2026-09-10
  授权取数补上（71/71 行全带值，§一 危险因素归因强度行）。以后再有"某源一面 blocked"，
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
| 危险因素（两层） | `risk_factor` + `disease_risk_factor` | `role` 分 genetic/exposure，`uri_tier` 分主条目与声明档，`p_value_text` 与 `pvalue_mlog` 两列分开存。C2e 实测：节点 3,049 位点 + 33 暴露（基因整串当一个节点，多基因分号串不拆；`MAPPED_GENE` 空的 613 行退到 SNPS），关系 6,208 行遗传（18/18 病＝主条目 3,651 行 16 病 + 声明档 2,557 行 4 病）+ 71 行暴露（17/18 病）。2026-09-10 补：暴露 71/71 行全带 `paf`（GBD 2023 年龄标化 ×100 的百分数、两位小数、值域 −7.33–100.00，3 个负值是保护方向）与 `paf_basis`（固定"GBD 2023 Deaths 年龄标化 2021"）；brain 无暴露行所以连 PAF 一起缺席 |
| 发病量 / 年龄组 / 趋势 | `stat_fact`（长表） | `estimate_basis` 就是"两列分开存、不可相减成趋势"那一句的落点。装的是 GLOBOCAN 国家单点、GCO 逐年 × 18 档 5 岁组、SEER 的新发率与死亡率年度序列（观测与拟合分 `registry_cohort` / `model_trend`）与 SEER 的 8 档宽年龄组构成；2026-09-10 补 GBD 2023 中国死亡年龄构成 319 行（`metric='age_death_pct'`、`region='China'`、2021 年不走 0 哨兵、`estimate_basis='national_estimate'`，18/18 病每病 16–20 档——零死亡的档不在源文件里） |
| 五年存活率 | `survival` | `stage_scheme` 分 SEER 汇总档与 Ann Arbor，`is_observed` 分开观测值与拟合值。整维三层都在这一张表：分期档、At a Glance 的全期头条、5-Year Relative Survival 的逐年序列——`stat_fact` 不重复写同一个数（实测一次装载里逐格相同的有 1674 行） |
| 在招试验 | `trial` | 只建 CT 白名单实测到的列，没有日期列。C2f 实测：23,705 行＝19,254 个 NCT（2,469 个跨病出现，最多一个试验挂在 17 个病上）；只落在招，三档全在 `status_bucket='active'` 一档，`why_stopped` 整列空（那句只有终止试验才有）；`enrollment` 23,704/23,705 行是估算值，估算还是实际整块留在 `design_info` |
| 前沿文献 | `publication` | `ext_key` 是 UPSERT 键（pmid→doi→标题哈希；实测 697 行没 pmid，其中 540 行退 doi、157 行只能哈希），计数走 `stat_fact` 的 `query_count`。落的是每病相关度前 500、共 9,000 行；`journal` 存刊名（8,448 行有值、1,793 个刊名、最长 221 字符），预印本留 NULL |
| 靶点 / 药 | `target` + `disease_target` + `drug` | `node_used` 记下这个病用的是宽档还是主条目。C2f 实测：228,551 条关联里 `score ≥ 0.1` 的 28,919 行进 `disease_target`，节点只从这些关系行长出来（7,098 个，所以两个数对得上）；`drug` 6,309 行是源 6,323 行三元组按 (病, 药, 阶段) 收拢来的，机制并成数组（4,128 行有机制），`phase` 存源原文 11 个取值 |
| 五路反查 | 复用上面各表，无独立表 | `symptom` 上另有 `idx_symptom_lookup`，反查按 `name_lang` 分组 |

没有表的维：只剩叙述/介绍段一个（裁成不进 MVP，所以不是"一列空着"而是整个维缺席）。
中国死亡年龄组与 PAF 原先也在这句里，2026-09-10 分别落进 `stat_fact`
（`metric='age_death_pct'`）与 `disease_risk_factor.paf`。

三条横切约定：

1. 每张事实表都带 `source_id` + `dataset_release_id` + `extract_method` + `review_status` +
   `loaded_at`。少一列就等于给"这个数哪来的"留下一张回答不了的表，`python db/tests/run.py status`
   扫的就是这五列齐不齐。
2. 进唯一键的列一律 `NOT NULL DEFAULT ''`（年份用 0），不用 NULL——MySQL 的唯一索引允许多个
   NULL，装载器重跑同一份发布就会插出重复行。
3. 建而不填的列等于页面上的空态：`symptom.freq_band`、`anatomy_node.label_zh`、
   `risk_factor.label_zh`。装载器不写它们，前端按 §二 的约定显示。
   `disease_risk_factor.paf` / `paf_basis` 原先也在这一列，2026-09-10 走通 IHME 授权取数后
   已填（71/71 暴露行），空态谓词随之删掉（§二 末段）。

这几条在只读的那一侧（D3a 起的 `api/onco_api/`）各有各的落点，页面不必自己再去对账：

1. 出处五列不摊在业务字段旁边：`serialize.py` 把它们收进每行一个 `provenance` 对象，顺带补上
   `source` 的名称、许可、主页与 `dataset_release` 的上游版本号——"这个数哪来的"在响应里一次说全，
   不需要前端去 JOIN 登记表。同一份边界在连接上：服务层的会话建起来就是 `READ ONLY`，写语句由
   MySQL 服务端拒，改数的那一侧仍然只有采集层。
2. 哨兵值的读法分两张表，不是一句 `year = 0` 打通：`stat_fact` 的"中国国家级单点""年龄组构成"
   "按声明词命中条数"三档读的是 `year = 0` 而不是 `year IS NULL`，这一条按 §一 的口径写进度量
   （`dimensions.py`）。`survival` 不能用它——实测 1,762 行没有一行 `year` 是 0，装载器给全期头条
   打的是源标的年份窗末年 2022（`SEER 21 (Excluding IL) 2016–2022`），与逐年序列里的 2022 撞在同一个
   值上。所以那张表的三层按"同一个 (档, 年份窗) 下有几个年份"分：一个的是当期点（头条与分期档），
   多个的是逐年序列（观测 1975–2018 共 44 年、拟合 1975–2023 共 49 年，两条重叠但不能相减）。
3. 榜不替调用方猜口径：`/api/stats/compare` 要 (度量, region, estimate_basis, sex, age_band) 五样
   钉死才排行。四轴按已钉前缀逐个问还剩几个取值：只剩一个的自动钉并记进 `auto_pinned`，还剩几个的
   回进 `needs` 并把每个可取值各自的覆盖病数、行数与年份跨度回在 `choices` 里（全库 267 个切片，
   GBD 死亡年龄构成补进来前是 214；接口现算这个数，此处的数字只是对账用）。
   前端照 `needs` 一路点下去就到榜，不必把清单抄进代码。`sex=both` 的中国国家估算 13/18 病有行，
   另五病的源只按性别发，把 `female`/`male` 凑进来是拿两批不同的人凑一个率，所以缺的五病列在
   `absent` 而不是零填。
4. 建而不填的那些列在页面上是谓词而不是抄来的文案：`gaps.py` 读的就是逐维度量，`freq_band == 0`
   才报"频率带空缺"。装载器哪天填上，接口就不再说它空着——这一条 2026-09-10 实际发生过一次：
   `paf` 填满 71 行后，"没有归因强度"那条空态连同谓词一起删掉了，页面与文档都不用再改口径；
   反过来哪个病新掉出一批零行，页面也立刻如实说缺。`anatomy_node.label_zh` 与 `risk_factor.label_zh`
   是全站同一件事，走 `/api/meta` 的 `gaps` 而不是逐病回十八遍。
5. 词表四维（D3c）一行是一个词不是一个数，所以四台都不折线、不求和，而各带回两份出处：
   `disease_anatomy` / `disease_histology` / `disease_risk_factor` 这些关系行与它们指向的节点行
   各有一套出处五列（连 `id`、`code`、`review_status` 都同名），只挂一份就等于把"挂载依据"与
   "节点本身"其中一个说成没有来源。聚合层反过来不带出处——`/api/diseases/{code}/histology`
   默认的组档行是从 3,111 条挂载数出来的，不是一行事实，冒充一份出处就是冒充一行事实。
6. 一个三位组码可以带两个组名：`histology_code` 里 172 个组码对 173 个组名（804 同时是
   SMALL CELL CARCINOMA, NOS 与 NON-SMALL CELL CARCINOMA, NOS，854、897 同），所以组档只按组码聚，
   每档回 `label_variants` 说明这一档有几个写法、`group_label` 只是其中一个；按 (组码, 组名) 聚
   会把一档拆成两档，那几个 DISTINCT 数在两边各算一遍就是重计。同样不补库里没有的结构：
   `anatomy_node` 没有 parent 列，亚部位的挂载依据是本病声明的 ICD-9 档而不是某个 site recode 的
   下级，所以 primary 与 subsite 两档分开平铺、不相加，页面要层级得另找一份真有层级的源。
7. 榜与计数不混：genetic 一行是一条关联（研究 × 位点 × p 值）不是一个位点，实测最多一病 2,151 行
   只对应 1,061 个位点、99 次研究录入，所以 `/api/diseases/{code}/risk-factors` 把行数、去重度点数
   与研究号数三个数一起回，并按 `-log10(p)` 降序在 `limit` 处截断且自述 `truncated`
   （默认 100，18 病里 9 病会被截）——页面拿到的榜是"前 N"，不是"这一病的全部"。
8. 研究层四维（D3d）一行是一条命中记录，量级比词表大两个数量级：一病试验 292–3,368 行、靶点关联
   226–3,321 行、在研药 20–1,023 行、文献每病固定 500 行，而一行试验折算 2,419 字节、带满两段
   重文本是 5,863（试验整维带满一次回完是 132.6 MB）。所以那四台是本批唯一分页的接口（`limit` 默认 50、上限 200），并把三个数分开：
   `page.total_rows` 是过滤后这一维还剩几行，`source_hit.value` 是源那头命中几行（读 `stat_fact` 里
   `estimate_basis='query_count'` 那一行，带那一行自己的出处而不是研究表的），`stored_rows` 是库里
   存了几行。`captured` 逐病现算，不在代码里写死"哪一维取满了"：实测 trial / target / drug 三维
   18/18 病两数相等、publication 0/18 相等。§一 那句"文献取相关度前 500"在页面上的读法就是这里来的
   ——「库内 500 篇、源命中 139,780 篇」说得成立，「这一病有 139,780 篇文献」说不成立。
9. 筛选项由数据自己给，不由接口作者给：四台的 `facets` 一律按**未过滤**的整维算，所以筛一次不会把
   选项集合弄缩水（跟着缩水的分面选一次就自删一个选项，第二筛没有回退路）；过滤值不在这一病当次的
   取值里回 404 并把可取值列出来——"这一病没有 PHASE4 的在招试验"与"PHASE4 不是这一病能取的值"是
   两句话。榜同样不发明排名（库里没有相关度分值列）：试验按 `nct_id`、文献按 `id`（装载序即 EPMC
   查询当时给的相关度序，拿 `data/raw/europepmc` 归档与库里逐位对过三个病各 500 行）、靶点按
   `score` 降序 + `ot_id` 兜并列（同一病内 4,391 组同分、最狠一组 194 行）、在研药按 `drug_name`。
   最后那一键是 MySQL `utf8mb4_0900_ai_ci` 的排序规则序而不是码位序（实测
   `.ALPHA.-TOCOPHERYLOXYACETIC ACID` 排在 `(R)-PFI-2` 前面，JS 默认 `sort` 的结果正相反），
   所以页面按接口给的序显示、不要自己重排，否则翻页会看着跳行。`eligibility` 与 `publications`
   两段大自由文本默认不带，点名走 `include` 白名单。这一层四张表也都不套 `review_status` 过滤基线
   （只有 `symptom` / `stat_fact` / `survival` 有）：本轮一行都没判过非，加过滤等于筛一个不存在的档。

"这一病有没有这一维的数"也不给总分：`/api/diseases` 每行带十维度量明细与各自的 `available`，
所以"症状 12 条 + 靶点 3,000 条"不会被读成同一个东西的两个档。
