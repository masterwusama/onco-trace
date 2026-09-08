"""候选源登记表。这份数据就是 docs/数据源探针计划.md 里那张探针清单的代码形态。

两处纪律：

1. `legal_note` 不许为空。授权边界没查清就上采集，等于把整个仓库的可用性押在
   一次事后 DMCA 上。cli.seed-sources 会拒绝写入空值。
2. `download_url` 只填"已经确认可直接取到"的入口。凭印象拼出来的 URL 一律留空，
   由探针从 home_url 解析真实入口后回填——本仓库已经吃过一次亏：
   HPO/Orphanet/NCIt 的文档都声称有疾病↔症状注释，实测常见上皮癌拿不到可用行。

`evidence` 字段记的是设计阶段已经手工验过的事实，探针跑完要能对上或推翻它。

`status` 从 B7c 起不再留 candidate，三个态各自的判据：
  · `active`——有内容级探针（不是只探可达性的 reach 行）实测过，且 MVP 建表用得上。
  · `paused`——两种情形合用一个态：等人工注册账号才能补测的（IHME 那两维）、
    以及只做过 reach 没做过内容级实测的备选码表。两者都不是"源坏了"，所以不写 rejected。
  · `rejected`——内容级探针判空，且这一维换源也补不上（WHO GHO 的死因×年龄组）。
逐源理由与"哪几维进 MVP"记在 docs/MVP裁定.md，这里不重复一遍，免得两处说法各自漂移。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    code: str
    name: str
    org: str
    source_type: str
    dimensions: tuple[str, ...]
    home_url: str
    legal_note: str
    download_url: str | None = None
    auth: str = "none"
    license: str | None = None
    commercial_use: bool = True
    attribution_required: bool = True
    robots_url: str | None = None
    rate_note: str | None = None
    fetch_mode: str = "once"
    reliability: str = "medium"
    status: str = "candidate"
    evidence: str = ""


SOURCES: tuple[Source, ...] = (
    # ---- ID 主干与器官树 ----
    Source(
        code="mondo",
        name="MONDO Disease Ontology",
        org="Monarch Initiative",
        source_type="ontology",
        dimensions=("identity",),
        home_url="https://mondo.monarchinitiative.org/",
        download_url="https://purl.obolibrary.org/obo/mondo.obo",
        license="CC BY 4.0",
        legal_note="OBO Foundry 公开发布，CC BY 4.0 需署名；purl 会 302 到 GitHub release asset，"
        "下载要走代理时重试逻辑在 fetch.py 里",
        fetch_mode="quarterly",
        reliability="high",
        status="active",
        evidence="探针实测（releases/2026-09-01，.obo 53134854 字节 / 63278 个 term）："
        "mondo.json 107586061 字节也可达，mondo-base.json 已 404；取 .obo——体量减半且能逐行"
        "流式解析。原判据「用 ICD-10 xref 找 18 病的主条目」被推翻：MONDO 没有裸 ICD10: 前缀"
        "（只有 ICD10CM 2142 行 / ICD10WHO 209 行 / ICD9 5658 行），且挂 ICD10CM:C34 的是 "
        "grouping 父类 respiratory system cancer 而不是 lung cancer，按 ICD-10 命中会稳定落到"
        "上一层分类词。改按 ICD-9 命中能 18/18，但捞到的是 bronchus cancer / anal canal cancer "
        "这类亚部位 term（合计 145 个，正好当器官与组织学下钻的子节点）——疾病主条目一个 ICD 码"
        "都不带，colorectal cancer 与 non-Hodgkin lymphoma 皆如此，所以主条目只能在 targets.py "
        "里声明，探针改为校验声明。18 个主条目 xref 实测：NCIT 18/18、UMLS 17/18（缺 uterus）、"
        "MESH 7/18、EFO 5/18。disease_has_location→UBERON 全库仅 775 行、disease_has_feature→HP "
        "仅 819 行（约 1%），当不了器官树或症状维的主源",
    ),
    Source(
        code="icdo3_seer",
        name="ICD-O-3 Site Recode 与形态学码表（SEER）",
        org="NCI / SEER",
        source_type="code_table",
        dimensions=("anatomy", "histology"),
        home_url="https://seer.cancer.gov/icd-o-3/",
        download_url="https://seer.cancer.gov/icd-o-3/sitetype.icdo3.d20220429.xlsx",
        license="US Government work",
        legal_note="美国政府作品可自由使用，要求引用 SEER；ICD-O 本体 WHO 版权，统计用途允许",
        fetch_mode="annual",
        reliability="high",
        status="active",
        evidence="探针实测（d20220429 版，xlsx 451875 字节 / 12496 行）：82 个 site recode"
        "（器官级，如 C160-C166,C168-C169 = STOMACH、C340-C343,C348-C349 = LUNG & BRONCHUS）"
        "展开出 332 个拓扑码，去重后 806 个 histology/behavior 码，一个文件同时覆盖 anatomy 与 "
        "histology 两维；18 病 100% 落到 site recode 分组，判据通过。"
        "SEER 不托管完整 ICD-O-3 Topography 表，亚部位名（贲门/胃窦/胃体）不在其中——"
        "那要 NCIt 的 obo（248 MB 且只能走代理），对 18 病的站不划算，"
        "作为已知缺口接受，器官树做到器官级两级；亚部位下钻改用 MONDO 的 145 个 ICD-9 亚部位 term",
    ),
    Source(
        code="icdo32_naaccr",
        name="ICD-O-3.2 Morphology 主文件（NAACCR 镜像）",
        org="NAACCR / WHO-IARC",
        source_type="code_table",
        dimensions=("histology",),
        home_url="https://www.naaccr.org/icdo3/",
        download_url="https://www.naaccr.org/wp-content/uploads/2020/10/"
        "Copy-of-ICD-O-3.2_MFin_17042019_web.xls",
        license="ICD-O-3.2 WHO/IARC，NAACCR 免费镜像",
        legal_note="WHO 拥有 ICD-O 版权，NAACCR 镜像供肿瘤登记用途；再发布前要确认 WHO 条款。"
        ".xls 老格式需 xlrd；同页另有 Histology3_vNN 年度更新表，属独立 dataset_code",
        fetch_mode="annual",
        reliability="high",
        status="paused",
        evidence="探针实测：464896 字节，单 sheet 'ICD-O-3.2 Morphology'，列为 "
        "ICDO3.2/Level/Term/Code reference/obs/See also/See note/Includes/Excludes/Other text，"
        "术语分 Preferred 与 Synonym 两级，比 SEER 的扁平描述细；不含 Topography 表",
    ),
    Source(
        code="mesh",
        name="MeSH 描述符全量（NLM）",
        org="NLM / NIH",
        source_type="code_table",
        dimensions=("identity", "literature"),
        home_url="https://www.nlm.nih.gov/mesh/meshhome.html",
        download_url="https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.xml",
        license="US Government work，NLM 免费分发",
        legal_note="美国政府作品，NLM 要求引用；年度发布，desc<year>.xml 随年份改名，"
        "URL 里的 2026 需要跟着换，不能写死在调度里",
        fetch_mode="annual",
        reliability="high",
        status="paused",
        evidence="B2 实测加进来的源，起因是 MONDO 只能给 7/18 个主条目提供 MESH xref，"
        "而 B5 的 Europe PMC 检索要靠 MeSH 主题词才有查准率。探针实测该 URL 直连 200、"
        "text/xml、312952703 字节（约 298 MiB）；同源另两个路径 "
        "projects/mesh/ftp/xmlmesh/desc2026.xml 返回 200 但是 text/html 外壳（200≠数据），"
        "www.nlm.nih.gov/mesh/ftp/... 直接 404。体量大但一年只下一次，"
        "而且直连可达，不像 NCIt 要依赖代理",
    ),
    Source(
        code="ncit",
        name="NCI Thesaurus（NCIt）",
        org="NCI / EVS",
        source_type="ontology",
        dimensions=("identity", "anatomy"),
        home_url="https://ncithesaurus.nci.nih.gov/",
        download_url="https://purl.obolibrary.org/obo/ncit.obo",
        license="CC BY 4.0（NCIt 本体）",
        legal_note="NCIt 以 CC BY 4.0 发布需署名；部分术语源自 WHO/ICD-O 等第三方，"
        "再发布前要按 NCIt 的版权页逐项确认，不能整库当成单一许可",
        fetch_mode="quarterly",
        reliability="high",
        status="paused",
        evidence="B2 实测加进来的源：MONDO 的 18 个主条目 xref 里 NCIT 是唯一 18/18 齐备的"
        "（UMLS 17/18、MESH 7/18、EFO 5/18），所以跨源枢纽应该落在 NCIt 而不是 MONDO 上，"
        "NCIt 自带 MeSH/ICD-O-3/ICD-10/EFO 交叉引用，一次能补齐三个缺口。"
        "取数链路实测：purl 302 → github.com/ncit-obo-org/ncit-obo-edition/releases/"
        "latest/download/ncit.obo → release-assets.githubusercontent.com，直连三连均 200、"
        "application/octet-stream、248162694 字节（约 237 MiB）、618–1376ms，"
        "走代理同样 200 但要 3005ms——直连可用且更快，不需要代理兜底。"
        "两个要留意的地方：URL 里的 latest 不是固定版本，upstream_version 只能从文件头"
        " data-version 取（实测 releases/2026-03-19，owl:versionInfo 26.02d）；"
        "而这个 OBO Edition 比 NCIt 主版本慢约半年，追新术语要回到 NCI 自己的分发渠道。"
        "NCI 自家 evs.nci.nih.gov/ftp1/NCI_Thesaurus/ 及其 archive 下的 OWL.zip "
        "均返回 200 但 content-type 是 text/html、只有 2873 字节，是外壳不是数据，已排除",
    ),
    # ---- 症状维（已知最弱的一环，四个源并列试） ----
    Source(
        code="orphanet_product4",
        name="Orphanet 罕见病↔临床征象（en_product4）",
        org="Orphanet / Inserm",
        source_type="code_table",
        dimensions=("symptom",),
        home_url="https://www.orphadata.com/",
        download_url="https://www.orphadata.com/data/xml/en_product4.xml",
        license="CC BY 4.0",
        legal_note="orphadata 明示 CC BY 4.0，需署名 Orphanet；文件约 45 MiB",
        fetch_mode="quarterly",
        reliability="high",
        status="paused",
        evidence="2026-07 版实测：4357 病 / 116664 行，每行都带六档频率带（Very frequent 99-80% / Frequent 79-30% / "
        "Occasional 29-5% / Very rare <4-1% / Obligate / Excluded）可直接映射 freq_band；"
        "但肺/结直肠/前列腺/胃/宫颈/成人肝 0 命中，血液肿瘤可用（CML 11 条、滤泡淋巴瘤 17 条）",
    ),
    Source(
        code="hpoa",
        name="HPO 疾病-表型注释 phenotype.hpoa",
        org="HPO / JAX",
        source_type="code_table",
        dimensions=("symptom",),
        home_url="https://hpo.jax.org/",
        download_url="https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa",
        license="见文件头声明",
        legal_note="OBO 发布，许可条款以文件头 #license 行为准，入库前抄录到 source.license；purl 会 302 到 GitHub release asset",
        fetch_mode="monthly",
        reliability="medium",
        status="paused",
        evidence="v2026-09-02 实测：35.8 MB / 252467 行 / 11598 病，第一列零个 MONDO ID；"
        "常见癌 3~9 行且是肿瘤同义复述（HP:0003002 Breast carcinoma），Frequency 全空；肺癌/胰腺癌/AML 作为条目 0 行",
    ),
    Source(
        code="monarch_common_disease",
        name="HPO 遗留常见病文本挖掘注释",
        org="Monarch Initiative",
        source_type="code_table",
        dimensions=("symptom",),
        home_url="https://data.monarchinitiative.org/hpo/",
        download_url="https://data.monarchinitiative.org/hpo/common_disease_annot.tar.gz",
        license="见包内声明",
        legal_note="2015 BioLark 文本挖掘产物，已被 HPO 主线弃用；只可当症状词表候选，不可当频率来源",
        fetch_mode="once",
        reliability="low",
        status="paused",
        evidence="实测 6.7 MB / 3647 文件：原发性肝癌 111 行、结直肠 111、食管 81、胰腺 55，"
        "但 16495 条癌症行 Frequency 全空，58 个癌文件只有 1 行 HP:0002664 Neoplasm",
    ),
    Source(
        code="nci_pdq_html",
        name="NCI PDQ 癌症信息摘要（HTML）",
        org="NCI",
        source_type="html_doc",
        dimensions=("symptom", "narrative"),
        home_url="https://www.cancer.gov/types",
        download_url="https://www.cancer.gov/sitemaps/pageinstructions.xml",
        license="US Government work",
        legal_note="美国政府公开内容，可转载需署名 NCI；站点无内容级 JSON/XML 端点，症状正文只有 HTML"
        "（sitemap 是唯一的 XML，只给 loc 与 lastmod，不给正文）",
        fetch_mode="monthly",
        reliability="high",
        status="active",
        evidence="探针实测（2026-09-08；sitemap 6,480 个 loc / 831 个含 pdq / 791 个在 /types/ 下，"
        "18 病按 targets.py 的 pdq_pages 认领 30 页）：症状维过判据——18/18 病从症状小节规则化取回"
        "≥4 条症状项，合计 209 条清单条目，5 病目测 precision 100%（判据线 ≥80%）。"
        "原设计两处假设被推翻：① 「这一维要靠 L3 模型抽取兜底」不成立，病人版页面的症状就是现成的"
        " <ul><li> 清单，L2 规则解析直接出条目，模型只在需要中文症状名时才用得上；"
        "② 「正文给不给频率」的答案是一条都不给——症状小节里百分号出现数为 0（hp 版整页 1,857 个"
        "百分号全是生存率与缓解率，没有一个落在症状段里），所以 freq_band 这一列这一源供不了。"
        "入口形状不统一，只能逐病声明：摘要页是 /types/<段>/<hp|patient>/<slug>-pdq，"
        "18 段里只有 12 段开了 patient 子树，另 6 段的病人内容在 /types/<段>/symptoms 栏目页"
        "（这种页全站只有 12 段有，lung/ovarian/thyroid/prostate/pancreatic 都没有）；"
        "旧结构 /types/lung/pdq/lung-adult-pdq 已 404，不存在的路径会 301 到栏目页并回 HTTP 200，"
        "所以每页必须比对 final_url。乳腺段最特殊：栏目页只有三句散文，全段唯一一份症状清单"
        "挂在男性乳腺癌页。版本戳页内取不到（响应头 Last-Modified 每页都是同一个站点重建时间，"
        "摘要页也没有 Updated），只能取 sitemap 的 per-page lastmod（本次最大 2026-07-09）。"
        "清单还不总挂在症状标题下（myeloma 两串挂在疾病名节的冒号引言后），取的是引言句之后"
        "紧跟的那一串，解析规则见 probes/nci_pdq_html.py。",
    ),
    # ---- 统计层 ----
    Source(
        code="seer_statfacts",
        name="SEER Cancer Stat Facts",
        org="NCI / SEER",
        source_type="html_doc",
        dimensions=("stat", "survival"),
        home_url="https://seer.cancer.gov/statfacts/",
        license="US Government work",
        legal_note="美国政府公开数据，需署名 SEER；HTML 表结构改版频繁，解析器必须留 fixture 回归",
        fetch_mode="annual",
        reliability="high",
        status="active",
        evidence="B3 实测（18 页全部 200，Last-Modified 2026-04-22，合计约 1.5 MB）："
        "SEER 自己把可抓的表标成 class=scrapeTable、id=scrapeTable_NN，但 NN 与页面出现顺序"
        "不一致（lungb 实测 _01 _02 _04 _05 _03 _07 _08 _06），只能按文档顺序用最近的 <strong> "
        "标题认领，不能按编号取表。八张表只有三种形状：年度序列是两行表头（4 个 metric 各 "
        "colspan=2，第二行 Observed / Modeled Trend），分期与年龄分布是一行表头，"
        "种族费率表压根没有 thead、6 行全是数据。量到的：年度序列跨 1975–2024 共 50 行 × "
        "4 个 metric，但各 metric 的观测窗不一样——死亡率 Observed 50 年到 2024、发病率 SEER 8 "
        "49 年到 2023、发病率 SEER 12 32 年（1992 起），而五年生存率 Observed 只有 44 年、"
        "止于 2018，2019–2023 仅有 Modeled Trend 拟合值；18 页的 min 与 max 完全相等，"
        "说明这是全站口径不是个别病缺失，落库时必须把 Observed 与 Modeled Trend 分开存。"
        "生存率分期 17/18 页有表：实体瘤是 Localized/Regional/Distant/Unknown 4 档，"
        "NHL 是 Ann Arbor 5 档、骨髓瘤同为血液肿瘤用的却是 SEER 汇总档 4 档，都过 ≥3 档判据；"
        "唯独白血病整页没有分期表"
        "（leuks.html 只有 7 张 scrapeTable，生存率是一条时间序列），只能给全分期一个数。"
        "费率表的性别不是一律双性别：非性别特异癌才有 Males/Females 两张表与 <h5> 标签，"
        "乳腺/宫颈/卵巢/子宫体/前列腺这 5 个性别特异癌只出一张表、页面上没有性别标签，"
        "性别仅写在口径行里（All Races, Females），所以按 targets.py 的 sex 列逐病校验。"
        "另有 6 个种族/族裔分组。同页三套 cohort 并存："
        "年度序列脚注是 SEER 12 + U.S. Mortality、All Races、Both Sexes，年龄发病表上方是 "
        "SEER 21 2019–2023，年龄死亡表是 U.S. 2020–2024，落库时按 metric 各记各的口径。"
        "年龄分布只有 8 档宽分组（<20、20–34、35–44、45–54、55–64、65–74、75–84、>84），"
        "未达 ≥10 档判据——画年龄分布够用，做不了 5 岁组标化率。"
        "SEER*Explorer 底层是 2000 美国标准人口 20 个 5 岁组，另有 "
        "source/content_writers/load_json_asset.php?asset=<name> 这个免鉴权 JSON 接口，"
        "但它只出 UI 配置（footnote-defines、default-checkboxes 一类），真正的数据要走 "
        "render_region_*.php 且表单参数没有公开文档。download_url 留空：入口是 18 个分页，"
        "不是一个文件",
    ),
    Source(
        code="gbd_results",
        name="GBD Results（发病/患病/死亡，按年龄×性别×地点）",
        org="IHME",
        source_type="statistics",
        dimensions=("stat",),
        home_url="https://vizhub.healthdata.org/gbd-results/",
        # 整轮 GBD 的数值一律要登录，唯一匿名可取的是这份词表 ZIP——
        # download_url 按"已确认可直接取到"的规矩只填它，不代表取到词表就等于取到数据
        download_url=(
            "https://ghdx.healthdata.org/sites/default/files/ihme_query_tool/"
            "IHME_GBD_2021_CODEBOOK.zip"
        ),
        auth="register",
        license="CC BY-NC 4.0",
        commercial_use=False,
        legal_note="IHME 数据非商用且必须署名；实测数值入口一律要注册登录，"
                   "注册的是免费非商用账号，走通之前这一维不能算通",
        fetch_mode="annual",
        reliability="high",
        status="paused",
        evidence="B3 实测（2026-09-07）：设计口径全中，数值入口全关。"
                 "词表层 18/18 病都有对应病因档（另带 13 个 L4 亚档，肝癌按肝炎/酒精/NASH 分因）、"
                 "中国=location_id 6、年度 1990–2021、性别 Male/Female/Both、"
                 "年龄组词表 155 档（含 1 岁一档 94 个）、Deaths/Incidence/Prevalence/YLDs 四个度量都在。"
                 "但 GBD 2023 的 22 个 GHDX record 里 118 个文件的下载链接一律换成 /download-access/login"
                 "（合计 6.3 GiB，最大单项 206 MB），另有 2 个 record 整页是 HTTP 200 的 Protected Page；"
                 "Results Tool 的查询接口要 Azure AD B2C 换来的 token"
                 "（scope https://ihmecsu.onmicrosoft.com/data-api/data.read），界面前还有一层 Cloudflare。"
                 "“死亡年龄段分析”因此暂无源可用，改由 GLOBOCAN 与 WHO GHO 顶上。"
                 "B4 复核（同日）：这份 codebook ZIP 仍直连 200 / 127869 字节，"
                 "但它依旧是 2021 版词表，而平台已发布 GBD 2023（vizhub /api/config "
                 "releaseText=GBD 2023、copyYear=2025）——引用这一档 ID 时标的年份按估计值那一版走，"
                 "不要按词表的 Y2024M05D16 走",
    ),
    Source(
        code="gbd_cra",
        name="GBD 归因分析（Comparative Risk Assessment）",
        org="IHME",
        source_type="statistics",
        dimensions=("risk",),
        home_url="https://vizhub.healthdata.org/gbd-compare/",
        # 附件 URL 带发布日戳（…_Y2025M10D23.XLSX），填进登记表等于把一个季度就会换的
        # 字符串当入口——下一版发出来这条 reach 记录就变 404，看着像源没了。
        # 所以登记挂这份附件的那一页，版本由探针每次从页面现取
        download_url="https://www.healthdata.org/research-analysis/about-gbd/gbd-data-and-tools-guide",
        auth="register",
        license="CC BY-NC 4.0",
        commercial_use=False,
        legal_note="IHME 内容 CC BY-NC 4.0：非商用 + 署名，注册的是免费非商用账号。"
                   "归因口径必须与登记处观测分开标——CRA 是模型估的归因分数，"
                   "SEER/GCO 是观测或估算的例数，不同表可以并排显示但不能相减",
        fetch_mode="annual",
        reliability="high",
        status="paused",
        evidence="B4 实测（2026-09-07）：效应量 0/18，关联骨架 10/18。"
                 "vizhub GBD Compare 的匿名面只有 GET /api/config（200，只有 "
                 "releaseText=GBD 2023 / gbdYear=2023 / copyYear=2025 这类版本字段），"
                 "/api/metadata、/api/data、/api/hierarchy、/api/data/version 一律 401，"
                 "对照 /api/language 404 说明 401 是真有路由且要授权；"
                 "授权面同 gbd_results（Azure AD B2C，scope data-api/data.read）。"
                 "匿名可取回的是 guide 页挂的 IHME_GBD_2023_A2_RESULTS_BY_MEASURE_Y2025M10D23.XLSX"
                 "（294 KB，5 张表）：Risk 表 2390 行 cause×REI 对（217 病因 × 88 REI），"
                 "Deaths/YLLs/YLDs/DALYs 四列只有 X 或空——有数标记，没有一个效应量数值，"
                 "所以 disease_risk_factor 的 paf 列无源可填；Cause 表 381 档，"
                 "targets.py 声明的 gbd_cause 18/18 在列（可作 gbd_results 词表的同源替代）。"
                 "剔掉聚合档（规则：同病集合内仍有后代的不计；不能按 level≥3 切，"
                 "High body-mass index 是二档却自带暴露与 PAF）后 10/18 病有 ≥3 个独立危险因素"
                 "（lung 16 / colorectum 11 / breast_female 7 / liver 5 / pancreas 4 / esophagus 4 / "
                 "prostate 4 / leukemia 4 / stomach 3 / kidney 3），"
                 "cervix / ovary / bladder 各 2、thyroid/uterus/nhl/myeloma 各 1、brain 一行都没有。"
                 "层级表只能继续用 2021 codebook：GBD 2023 的 Cause/REI/Location Hierarchies record "
                 "页面上没有任何未登录文件链接，且这一版 2023 的 88 个 REI 全部能在 2021 层级表里找到，"
                 "两版之间没有出现过重编号",
    ),
    Source(
        code="globocan",
        name="GCO Cancer Today (GLOBOCAN estimates)",
        org="IARC / WHO",
        source_type="statistics",
        dimensions=("stat",),
        home_url="https://gco.iarc.who.int/today/",
        download_url="https://gco-api.iarc.fr/api/globocan/v3/2024/"
        "factsheet/population/156/?group_CRC=1&include_nmsc=1&include_nmsc_other=1",
        license="IARC terms",
        legal_note="IARC 数据需署名 GLOBOCAN / IARC；一次性估算数据集，可商用未见限制，"
        "但引用必须带版本与估算方法码（本版中国 incidence=2b）",
        fetch_mode="annual",
        reliability="high",
        status="active",
        evidence="B3 实测：真实入口是 gco-api.iarc.fr 的 JSON，匿名直连、无 token 无登录门。"
        "路径里的版本号 2024 不是常量，写在前端 bundle 的 data_version 里，探针从那里读。"
        "中国 country=156（按 iso3=CHN 认，别硬写数字）；一次 factsheet 请求回 279 行 = "
        "sex 0/1/2 × type 0 新发/1 死亡/2 现患 × 34 个癌种码，每行带 total / asr（世界标化）"
        "/ crude_rate / cum_risk_74，18 病全部配得上。码表另给 meta/cancers(41 档，带 ICD 段) "
        "与 meta/populations(238 地点，带 method_incidence 等估算方法码) "
        "与 meta/update(14 个历史版本)。"
        "两条限制：这一路完全没有年龄维（路径末段实测是癌种过滤器，ages_specific=1 无效）；"
        "一版只有一个年份，meta/update 那 14 个版本每步都换估算口径，拼不出趋势线。"
        "另注意现患 type=2 在一个 (sex,cancer) 上有 3 行而不带期间标签（1/3/5 年混在一起），"
        "取数前必须回响应的 description 读口径。年龄别与逐年由 gco_overtime 顶上",
    ),
    Source(
        code="gco_overtime",
        name="GCO Cancer Over Time (registry time series)",
        org="IARC / WHO",
        source_type="statistics",
        dimensions=("stat",),
        home_url="https://gco.iarc.who.int/overtime/",
        download_url="https://gco-api.iarc.fr/api/overtime/v2/22/"
        "data/rate/0/0_1_2/156/all/",
        license="IARC terms",
        legal_note="同上需署名；这是登记处汇编的观测序列而非模型估算，引用必须带"
        "inc_cov / inc_period / inc_source 三个口径字段，中国是抽样登记格外推",
        fetch_mode="annual",
        reliability="high",
        status="active",
        evidence="B3 实测：与 Cancer Today 同门户但是另一个数据库、另一套癌种码"
        "（肺 11、结直肠 106、NHL 26=「C82-86,C96」、骨髓瘤 27=「C88+C90」——"
        "同一个 C88 在 Today 归 NHL、在这里归骨髓瘤，所以两列码分开声明在 targets.py）。"
        "API base 与版本 22 都写死在前端 chunk-vendors 里，探针从 bundle 读；"
        "5 岁档标签（0-4…85+ 共 18 档）在 index bundle 里。"
        "中国一次请求回 1248 行 = 16 个年度 2002-2017 × sex 0/1/2 × 28 个癌种码，"
        "每行带 ages（18 档计数 + unk）/ populations（同构分母）/ age_specific_rate（同构率）"
        "再加 asr / asr_e / asr_e2013 / asr_n / cum_risk_74 / cum_risk_79，"
        "18/18 病全过——这正是 SEER（只有 8 档宽组）与 GBD（数值全在登录门后）都没给的"
        "≥5 年 × ≥10 年龄组。两条硬限定：中国 mortality=false，type=1 实测 0 行，"
        "死亡年龄段这一路没有；且 bool_national=False、inc_cov=0.6，"
        "inc_source 明写上海/嘉善/中山/哈尔滨南岗等 5 个登记处，是子 national 口径，"
        "不能与 Cancer Today 的国家级估算并成一条曲线",
    ),
    Source(
        code="who_gho",
        name="GHO OData API（WHO 全球卫生估计 GHE）",
        org="WHO",
        source_type="statistics",
        dimensions=("stat",),
        home_url="https://www.who.int/data/gho/info/gho-odata-api",
        download_url="https://ghoapi.azureedge.net/api/SDG_SH_DTH_RNCOM?"
        "$filter=SpatialDim%20eq%20'CHN'&$top=5&$count=true&$format=json",
        license="CC BY-NC-SA 3.0 IGO",
        commercial_use=False,
        legal_note="WHO 内容 CC BY-NC-SA 3.0 IGO：需署名、禁商用、同条款共享；terms-of-use 页"
        "明写教学/非商用以外的用途须事先书面授权。引用必须带指标码与年份，"
        "且 GHE 是模型估计而非登记处观测，不与 SEER/GCO 的观测数并表",
        fetch_mode="annual",
        reliability="medium",
        status="rejected",
        evidence="B3 实测（这一维原本指望它补中国死亡年龄组）：计划里写的 Athena API"
        "（apps.who.int/gho/athena/api/…）已整条 302 到 www.who.int/data/gho/legacy，"
        "后继是 ghoapi.azureedge.net/api 的 OData，匿名无 key、每个指标一个实体集，"
        "$metadata 12 MB 所以取数只能点名；网关把 $top 硬限在 1000（超了回 400，"
        "原因只在响应体里）。可达性分两档：$top=5 的单指标小查询直连 200，"
        "而逐指标翻页的整趟探针有一步落代理（504 与连接重置都出现过），"
        "所以 probe-reach 记 direct、专项探针记 proxy。"
        "形状完全合判据：Dim1/Dim2/Dim3 带 SEX、AGEGROUP、GHECAUSES（列序逐指标不同，"
        "必须按 DimNType 认领），AGEGROUP 维有 YEARS00-04…YEARS80-84 + 85PLUS 共 17 档"
        "成体系的 5 岁阶梯。缺的是国家级行：带死因维的 22 个指标里只有 14 个有 CHN 行，"
        "其中带 ≥10 个年龄组的 0 个——有中国行的这些指标只有 SEX 与死因两维；"
        "反过来形状最合的 8 个（GHE_DALY*/YLL*/YLD*、MORT_600/700）SpatialDimType 只有 "
        "MGHEREG，SpatialDim eq 'CHN' 零行，只有区域与收入组聚合。"
        "能取到的中国癌种数最细只到 GHE061「Malignant neoplasms」一档合计"
        "（SDG_SH_DTH_RNCOM 240 行 = 2000-2019 × 3 性别 × 4 个大组，无年龄）。"
        "另一条独立上限：GHECAUSES 159 档里 GHE061–GHE079 只 19 个癌种档，18 病仅 13 病"
        "能一对一——肾/脑/甲状腺合并在 GHE078、NHL 与骨髓瘤共用 GHE076，"
        "所以即使将来放出国家级×年龄组，这一路也只到 13/18。"
        "18 病 × 年龄组 × 中国死亡数在这一路上实测 0/18，缺口没被填上",
    ),
    Source(
        code="gwas_catalog",
        name="GWAS Catalog 全量关联",
        org="EBI",
        source_type="association_db",
        dimensions=("risk",),
        home_url="https://www.ebi.ac.uk/gwas/downloads",
        download_url="https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest/"
        "gwas-catalog-associations_ontology-annotated-full.zip",
        license="CC BY 4.0",
        legal_note="EBI CC BY 4.0 需署名；整包 73.5 MB 的 zip，流式解压只读里面那张 TSV，"
        "不展开成 740 MB 落盘文件；探针取整包并解析全量而不是前若干行——关联表按录入年份排序，"
        "取前缀会把填充率系统性量偏",
        fetch_mode="monthly",
        reliability="high",
        status="active",
        evidence="探针实测：旧入口 /gwas/api/search/downloads/full 已 404 下线。真实入口是 EBI FTP 的 "
        "releases/<年>/<月>/ 日历目录（实测 2026-09，这份包 2026-09-04 发布），latest/ 只是它的镜像，"
        "所以 upstream_version 从日历目录读、不拼死。ontology-annotated 版整包 73,489,749 B，"
        "里面 gwas-catalog-download-associations-alt-full.tsv 是 1,192,032 行 / 38 列。"
        "疾病侧对齐靠 MAPPED_TRAIT_URI，而它用的是 MONDO URI（24619 个 trait 档里 2129 个是 MONDO_），"
        "所以 B2 那个「MONDO 主条目 EFO xref 只有 5/18」的缺口在这里不构成障碍，方向反过来即可。"
        "效应量与 CI 匿名就在文件里：`OR or BETA` 非空 84.3%、`95% CI (TEXT)` 非空 83.2%，"
        "而 CI 只能按「含可解析区间」算，那是 75.0%"
        "（非空 991,389 行减去含区间的 894,029 行，差的 97,360 行只有 `unit increase` 这类文字没数字）；"
        "全表没有任何 PAF/归因分数列。`OR or BETA` 是 OR 与 β 一列混装：值 <0 的只有 6 行，"
        "方向写在 CI 文本的 unit increase/decrease 注记里（841,694 行），"
        "所以 effect_kind 无法自动判定，只能整列留未判定。"
        "按 targets 声明的主条目 URI 精确命中：有行的 16/18，按判据（≥3 个带效应量与区间的独立位点）"
        "14/18——breast_female 与 uterus 零行、pancreas 主条目 12 行全无区间、esophagus 只剩 1 个位点。"
        "这几病的关联大量挂在同级组织学档上（breast carcinoma 1832 行、exocrine pancreatic "
        "carcinoma 171、endometrial carcinoma 102），并进来可达 18/18。P1 的 C1a 已把它裁成"
        "`targets.GWAS_URI` 逐病声明：只收与 icd10 段语义等同的器官/组织级档，不收分子亚型、"
        "癌前与良性档；探针按「主条目 + 声明档」判覆盖，严格口径单独上报，不许探针按名字自动并档。"
        "MAPPED_TRAIT_URI 是逗号分隔的多值列（96,520 行带 2~7 个档，MR 研究把暴露档与疾病档并在一行），"
        "整格取尾档会让这 1,339 行伪装成本病自身的位点证据，所以只有单档行算命中；"
        "label 列与 URI 列共用逗号而段数对不上的有 9,191 行，拆档只认 URI、label 当整串用。"
        "取数姿势：单条长连接会在 53,767,694 B 处被断流且以 HTTP 200 正常收尾，"
        "必须按 8 MB 分段 Range 续传",
    ),
    # ---- 前沿研究 ----
    Source(
        code="ctgov_v2",
        name="ClinicalTrials.gov API v2",
        org="NIH / NLM",
        source_type="trial_registry",
        dimensions=("trial",),
        home_url="https://clinicaltrials.gov/data-api/api",
        download_url="https://clinicaltrials.gov/api/v2/studies",
        license="US Government work",
        legal_note="美国政府公开数据；API 有速率限制，rate_note 以官方文档为准",
        rate_note="分页 pageToken，避免并发",
        fetch_mode="weekly",
        reliability="high",
        status="active",
        evidence="B5 实测（2026-09-08，单趟 149 s / 249 个请求）：18/18 病都有 ≥1 项在招试验。"
        "按主查询（第一项 MeSH 主题词裸写 OR 同义词加引号）命中 110,954 项，其中在招 23,660、"
        "未招募但仍在随访 7,449、已完成 46,704。同口径逐病比有两个方向的增益：主题词裸写比整串"
        "加引号多 9,787 项（MeSH 概念展开），声明的同义词又在主题词之上多并进来 2,823 项"
        "（brain +1,021、liver +606、colorectum +347、nhl +198）——两个都不能省。"
        "逐词裸/引对比另有 8 个词裸写超过加引号的 2 倍（colorectum「colon cancer」8,256 对 2,273、"
        "cervix「cervical cancer」11,331 对 2,538、breast_female「female breast carcinoma」4,182 对 78）"
        "——非主题词裸写退化成词级匹配，会把别的癌种灌进来，所以只有声明的第一项允许裸写。"
        "设计期以为 condition 能直接当键用，实测不成立：`fields=` 白名单里 "
        "conditionsModule.meshTerm 与 derivedSection.* 一律 400，取回的记录里没有可对齐的 MeSH ID，"
        "查询词只能在 targets.py 的 `search_terms` 里逐病声明。计数有机制：`countTotal=true` 让响应"
        "顶层多出一个 `totalCount`，实测与逐页数出来的行数完全相等（`countOnly`/`meta`/`totalHits` "
        "这些猜的名字一律 400，`pageSize=0` 回 200 但不给计数）——这一支第一版不知道它，"
        "按 pageSize=1000 翻页数了两趟约 160 个请求、467 s，那个耗时结论作废。"
        "23 个落库候选列实测过半有值的 18 个，稀疏的是 collaborators / location_countries / "
        "other_outcome / publications / why_stopped，属试验本身的性质，落库允许为空。"
        "匿名侧没有地理过滤器（`filter.geo` 三种写法与 `aggFilters=geo:CHN` 都被拒），"
        "抽样 900 条里 0 条有中国大陆研究地点；在招口径可以问（`filter.overallStatus` 与 "
        "`query.cond` 同时生效）。唯一的版本戳在 `/api/v2/version`"
        "（实测 apiVersion 2.0.5 / dataTimestamp 2026-09-04T09:00:06），"
        "`/api/v2/studies` 的 etag 是站点静态资源戳不是数据版本",
    ),
    Source(
        code="europepmc",
        name="Europe PMC REST",
        org="EBI",
        source_type="literature",
        dimensions=("literature",),
        home_url="https://europepmc.org/RestfulWebService",
        download_url="https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        license="混合（OA 子集多为 CC BY）",
        legal_note="检索元数据可自由使用；全文按各出版商许可，只有 OA 子集可再分发，入库要逐篇记 license",
        rate_note="官方建议低并发，批量走 OA 子集的 FTP 而不是逐篇取",
        fetch_mode="monthly",
        reliability="high",
        status="active",
        evidence="B5 实测（2026-09-08，单趟 181 s）：18/18。近 5 年命中 765,891 篇、全库 2,357,107 篇；"
        "全文可得率是两个数，不是一句「可得率多少」——facet 口径 OPEN_ACCESS 50.8%、IN_EPMC 56.5%，"
        "而同一批查询的记录级抽样 900 条只有 31.7% isOpenAccess。差额是抽样构成造成的：抽到的源里 "
        "MED 845 / PPR 47 / PMC 8，MED 内部 OA 率本来就低（先前实测 24.0%，PMC 内部 81.8%）。"
        "facet 说的是库里有多少，记录级说的是随手翻到的是多少，L3 能不能拿到正文要看后者。"
        "core 抽样 900 条的字段填充：abstractText 86.4%（症状抽取的第二输入）、title 100.0%、doi 98.6%、pmid 93.9%——落库按 pmid 建键，DOI 只当可选链接。"
        "MeSH 不能当疾病键：同一批病按 `MH:` 字段只召回全库的 2.2%（52,282 / 2,357,107），"
        "主题词检索另不稳定（`MH:\"Lung Neoplasms\"` 3,474 对自由文本 260,001）。三个必踩的坑："
        "查询串必须 urlencode 生成，自己 percent-encode 与 `+` 编码对同一内容量出 20,275 与 3,474 两个数；"
        "`PUB_YEAR:2021-2025` 连字符写法被静默忽略当成没过滤，必须 `lucene=true` 配 "
        "`PUB_YEAR:[2021 TO *]`；错误查询回的是 HTTP 200 加 body 里的 errCode，"
        "不判 errCode 就会把写错的查询记成「这一病没文献」。"
        "响应只给搜索版本号（实测 version=6.9），没有数据发布日",
    ),
    Source(
        code="opentargets",
        name="Open Targets Platform 数据下载",
        org="Open Targets",
        source_type="association_db",
        dimensions=("trial", "literature"),
        home_url="https://platform.opentargets.org/",
        # 已确认可 GET 直连取到的入口只有这份清单；真实取数走 POST 的 GraphQL（见 evidence）。
        # latest 是移动别名，版本号必须从文件里的 version 字段读，不能拼死
        download_url="https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/latest/croissant.json",
        license="平台 Apache 2.0，数据随上游",
        legal_note="平台代码 Apache 2.0，但关联数据继承各上游源条款（ChEMBL/ClinVar/GWAS 等），再发布前需逐上游确认。"
                   "B5 待裁：这份 croissant 清单自己的 license 字段写的是 CC0（publicdomain/zero/1.0），"
                   "与本行\"继承上游\"的说法不一致——署名口径以哪个为准要人定，探针不替它改",
        fetch_mode="quarterly",
        reliability="high",
        status="active",
        evidence="B5 实测（2026-09-08，单趟 85 s）：18/18 达标（判据＝关联靶点 ≥10）。"
        "点查层是 `POST api.platform.opentargets.org/api/v4/graphql`，匿名、收 MONDO 号"
        "（`efoId:\"MONDO_0008903\"`，冒号换下划线），一次别名批量问完 18 病的关联数/文献量/"
        "表型数/在研药数；关联靶点最少 breast_female 643、最多 colorectum 16,299，合计 212,130。"
        "带分数的靶点清单可取（lung 首三条 EGFR 0.901、KRAS 0.858、ERBB2 0.842，"
        "分数按 datasourceScores 拆到数据源，最高那项是 europepmc 1.0——即这条关联主要由文献共现撑起）。"
        "父节点不能并进同一趟：18 病连 parents 一次问完回 408 Request Timeout，按 6 病一批才全通。"
        "窄档问题按实测暴露：breast_female 节点 88 篇文献 vs 父节点 breast carcinoma 710,750，"
        "pancreas 382 vs 166,685——换宽档要重新声明，探针不自动并档。"
        "顺带的负向证据：9 病 phenotypes.count 为 0，其余 2~8 条，表型注释当不了症状维的源。"
        "批量层是 EBI FTP `platform/<版本>/output/`，实测 56 个数据集 / 1,102 个 parquet 分片 / "
        "58.5 GiB（最大 colocalisation 20 GB），清单声明的 56 个目录名与 FTP 实到完全一致；"
        "croissant 不给单文件大小（根条目的 sha256 字段是字面量 'sha256' 占位），体量只能逐目录列。"
        "版本 26.06 / datePublished 2026-06-23，GraphQL 的 meta.apiVersion 是 x/y/z 对象（26.6.3）"
        "不是字符串——站点按点查用就够，落库不需要整包",
    ),
    # ---- 叙述与中文 ----
    Source(
        code="who_factsheet",
        name="WHO 癌症 Fact Sheets（含中文版）",
        org="WHO",
        source_type="html_doc",
        dimensions=("narrative", "symptom"),
        home_url="https://www.who.int/news-room/fact-sheets/detail/cancer",
        download_url="https://www.who.int/news-room/fact-sheets",
        license="CC BY-NC-SA 3.0 IGO",
        commercial_use=False,
        legal_note="WHO 内容 CC BY-NC-SA 3.0 IGO：需署名、禁商用、同条款共享；商用站点不可直接转载"
        "——这一条与站点是否商用直接冲突，症状维已有 PDQ（美国政府公开内容）可替，"
        "WHO 只剩\"中文症状名与概述底稿\"这一项用途，是否值得为它放弃商用空间交 B7 裁",
        fetch_mode="annual",
        reliability="high",
        status="active",
        evidence="探针实测（2026-09-08；A-Z 列表页一次请求回 242 份 sheet 的 slug 与标题，"
        "按 targets 词认领到 6 份并逐份取中英两版 + 试六语种，77 s）："
        "**不达判据**——判据线是 ≥12/18 病有癌种专页，实测 4/18"
        "（lung、colorectum、breast_female、cervix），肝/胃/胰腺/食管/前列腺/卵巢/甲状腺/膀胱/"
        "肾/脑/子宫体/白血病/NHL/骨髓瘤 14 病 WHO 压根没有专页，这不是抓取失败。"
        "但它的独特价值坐实了：中文症状清单是真翻译且带现成 <li>，"
        "breast 5 条、colorectal 6 条、lung 7 条，目测 precision 100%，"
        "所以\"中文症状名\"这一列有公开非模型的路可走——代价是只覆盖这 3 病，"
        "cervix 有专页但整页没有症状节（中英都没有）。另有两份不分病种的通页"
        "（Cancer、Childhood cancer）能给 18 病共用的概述。"
        "口径五处：① 正文容器是 <section id='content'>，按 <article> 取会连整站导航的 24 串菜单进来；"
        "② 中文 URL 是换根不是加后缀，/zh/news-room/fact-sheets/detail/<slug> 可取、"
        "/detail/<slug>/zh/ 回 404，实测 6 份 sheet 的六语种全回 200；"
        "③ 中英两版的小节集合不是同一套——乳腺癌那 5 条症状中文版有\"症状\"标题、英文版整页没有 "
        "Symptoms 节（挂在 \"Who is at risk?\" 之下），而 Cancer 通页英文版连 Key facts 一节都没有"
        "（结构是 The problem / Causes / Risk factors / …），中文版有 5 条要点，"
        "所以按标题定位必须逐语种各切一遍；"
        "④ 版本戳在页内 <div class='date'>，中文页「2026年7月3日」、英文页「3 July 2026」两种格式，"
        "与 JSON-LD dateModified 互校后实测 12 页全等；各 sheet 自己更新，"
        "认领到的 6 份跨度 2026-02-13…2026-07-03，所以不能取\"今天\"当版本；"
        "⑤ schema.org 的 hasHealthAspect 只有部分中文页有（breast 3 类、colorectal 与 lung 4 类，"
        "cervical 与两份通页无，英文页全无），只能当佐证不能当段别索引。",
    ),
    Source(
        code="wikidata",
        name="Wikidata / 中文维基百科",
        org="Wikimedia",
        source_type="association_db",
        dimensions=("identity", "symptom", "narrative"),
        home_url="https://www.wikidata.org/",
        license="Wikidata CC0 / Wikipedia CC BY-SA 4.0",
        legal_note="Wikidata CC0 可自由使用；维基百科正文 CC BY-SA 需署名且同条款共享，转载要保留出处链接",
        fetch_mode="monthly",
        reliability="low",
        status="active",
        evidence="探针实测（zh-label-symptom，2026-09-08，partial 7/18）："
        "① 直连 Wikimedia 全线读超时，每一发都要落代理（代理侧约 1 s/请求，一趟 70 发上下约 15 分钟），"
        "且一整趟里会撞上一到两次 CONNECT 抖动——_json 已按 3 次退避重试，"
        "探针的 reachability 只按真正用上的那批响应算；"
        "② 症状子树可用但不等于能用：Q169872 往下 wdt:P279* 有 10045 个实体、2158 个带 zh label，"
        "拿它当英中词典去贴 PDQ 的 213 条症状能命中 168 条（78.9%），"
        "但逐条目测只有 对 63 / 泛 43 / 错 62，precision 37.5%（判据线 80%）——"
        "错的是另一个概念的译名（weight loss→减肥、loss of appetite→耳咽管開放症、"
        "anemia→密穗蕨科），泛的是所有 *pain→疼痛、所有 swelling→水肿；"
        "③ 按 PDQ 原文逐条查 wbsearchentities 的天花板只有 21.3%：169 个去重写法里 ≤2 词仅 36 个、"
        "≥5 词占 91 个，而 Wikidata 查词条不查短语，36 个短词样本里 4 个根本没命中、"
        "4 个命中却无中文标签，languages=zh 还是繁简混排；"
        "④ 中文维基自己的症状章节是唯一中文能全对的一路，但只有 5/18 病有 ≥4 条真症状清单"
        "（leukemia 10、uterus 9、myeloma 8、pancreas 4、colorectum 4；解析条数虚高，"
        "colorectum 的 17 条里 13 条是分期定义、myeloma 那节标题本身就叫「症狀及併發症」），"
        "11 病写成散文规则取不到、只有 liver 与 nhl 连章节都没有；"
        "与 WHO 中文版并集才 7/18，离判据 12/18 还差 5 病——"
        "所以中文症状名不做成翻译列，symptom 按 (disease_id, source_id, name_lang, name) 落；"
        "⑤ 两处口径： titles 解析必须把 normalized / converted / redirects 三种边都走一遍再喂 "
        "action=parse（它不认 converttitles）——只按字面标题匹配时 宫颈癌/甲状腺癌/肾癌/"
        "多发性骨髓瘤 稳定 missingtitle，会被误报成「这病没有症状章节」，实测趟里就是这四个；"
        "条目名还可能整页重定向（结直肠癌 → 大腸癌），action=parse 带 redirects=1 能跟上，"
        "但繁简与别名的差异仍在，段标题要按简繁两套一起认（症状/症狀/病徵/臨床表現…）。",
    ),
)

BY_CODE = {s.code: s for s in SOURCES}
