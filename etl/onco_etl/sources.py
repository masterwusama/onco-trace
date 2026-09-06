"""候选源登记表。这份数据就是 docs/数据源探针计划.md 里那张探针清单的代码形态。

两处纪律：

1. `legal_note` 不许为空。授权边界没查清就上采集，等于把整个仓库的可用性押在
   一次事后 DMCA 上。cli.seed-sources 会拒绝写入空值。
2. `download_url` 只填"已经确认可直接取到"的入口。凭印象拼出来的 URL 一律留空，
   由探针从 home_url 解析真实入口后回填——本仓库已经吃过一次亏：
   HPO/Orphanet/NCIt 的文档都声称有疾病↔症状注释，实测常见上皮癌拿不到可用行。

`evidence` 字段记的是设计阶段已经手工验过的事实，探针跑完要能对上或推翻它。
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
        license="US Government work",
        legal_note="美国政府公开内容，可转载需署名 NCI；实测无 JSON/XML 端点，只有 HTML，是 L3 抽取的主要输入",
        fetch_mode="monthly",
        reliability="high",
        evidence="L3 症状抽取的正文来源；要实测的是页面结构稳定性与"
        "正文里到底给不给频率（多数只列症状不给百分比，那 freq_band 才是要填的字段）",
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
        evidence="年龄别发病率、5 年相对/观察生存率、按分期分档的主力源；每病一个页面，需从索引页解析真实 URL",
    ),
    Source(
        code="gbd_results",
        name="GBD Results（发病/患病/死亡，按年龄×性别×地点）",
        org="IHME",
        source_type="statistics",
        dimensions=("stat",),
        home_url="https://vizhub.healthdata.org/gbd-results/",
        license="CC BY-NC 4.0",
        commercial_use=False,
        legal_note="IHME 数据非商用且必须署名；批量下载历史上要在工具里选维度，是否存在可编程批量入口是 B3 第一个要实测的问题",
        fetch_mode="annual",
        reliability="high",
        evidence="“死亡年龄段分析”要靠它的 deaths by age；GBD 2023 覆盖 375 病因 × 204 地区",
    ),
    Source(
        code="gbd_cra",
        name="GBD 归因分析（Comparative Risk Assessment）",
        org="IHME",
        source_type="statistics",
        dimensions=("risk",),
        home_url="https://vizhub.healthdata.org/gbd-compare/",
        license="CC BY-NC 4.0",
        commercial_use=False,
        legal_note="同 gbd_results；非商用 + 署名",
        fetch_mode="annual",
        reliability="high",
        evidence="disease_risk_factor 的 paf / 归因死亡数就来自这里：88 个危险因素 × 375 个病因",
    ),
    Source(
        code="globocan",
        name="GLOBOCAN / Global Cancer Observatory",
        org="IARC / WHO",
        source_type="statistics",
        dimensions=("stat",),
        home_url="https://gco.iarc.who.int/",
        license="IARC terms",
        legal_note="IARC 数据需署名，部分表仅提供在线工具导出；中国口径历史上多为 PDF，探针要判定能否机器可读",
        fetch_mode="annual",
        reliability="medium",
        evidence="国家层面发病/死亡，用于与 SEER（美国）并排展示口径差异，不与之混算",
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
        legal_note="EBI CC BY 4.0 需署名；文件是 zip，解析前先解压；探针只取前若干行确认字段",
        fetch_mode="monthly",
        reliability="high",
        evidence="探针实测：旧入口 /gwas/api/search/downloads/full 已 404 下线。真实入口是 EBI FTP 的 "
        "releases/latest/，其中 ontology-annotated 版带 EFO ID（疾病对齐就靠它），"
        "同目录另有 gwas-efo-trait-mappings.tsv 做 trait→EFO 映射。"
        "latest/ 是滚动目录，upstream_version 要从 releases/ 列表读，不能假定为固定值",
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
        evidence="condition 是自由文本，20 病的查询词表命中率是判据；phase 与结果发布标记字段完整性要实测",
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
        evidence="按 MeSH 批量取文献；摘要全文可得率决定 L3 抽取有没有第二输入源",
    ),
    Source(
        code="opentargets",
        name="Open Targets Platform 数据下载",
        org="Open Targets",
        source_type="association_db",
        dimensions=("trial", "literature"),
        home_url="https://platform.opentargets.org/",
        license="平台 Apache 2.0，数据随上游",
        legal_note="平台代码 Apache 2.0，但关联数据继承各上游源条款（ChEMBL/ClinVar/GWAS 等），再发布前需逐上游确认",
        fetch_mode="quarterly",
        reliability="high",
        evidence="疾病↔靶点↔药关联带分数，是“前沿研究”与靶点/药反查的主力；下载入口在 EBI FTP",
    ),
    # ---- 叙述与中文 ----
    Source(
        code="who_factsheet",
        name="WHO 癌症 Fact Sheets（含中文版）",
        org="WHO",
        source_type="html_doc",
        dimensions=("narrative",),
        home_url="https://www.who.int/news-room/fact-sheets/detail/cancer",
        license="CC BY-NC-SA 3.0 IGO",
        commercial_use=False,
        legal_note="WHO 内容 CC BY-NC-SA 3.0 IGO：需署名、禁商用、同条款共享；商用站点不可直接转载",
        fetch_mode="annual",
        reliability="high",
        evidence="中文长文本的少数正规来源之一；页面少但权威，适合做疾病概述的底稿",
    ),
    Source(
        code="wikidata",
        name="Wikidata / 中文维基百科",
        org="Wikimedia",
        source_type="association_db",
        dimensions=("identity", "narrative"),
        home_url="https://www.wikidata.org/",
        license="Wikidata CC0 / Wikipedia CC BY-SA 4.0",
        legal_note="Wikidata CC0 可自由使用；维基百科正文 CC BY-SA 需署名且同条款共享，转载要保留出处链接",
        fetch_mode="monthly",
        reliability="low",
        evidence="本机实测到 Wikimedia 的 DNS 与连接全线超时，reachability 大概率为 blocked；"
        "疾病条目的症状类属性填充度也未能验证，探针必须走代理重试",
    ),
)

BY_CODE = {s.code: s for s in SOURCES}
