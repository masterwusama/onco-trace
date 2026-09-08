-- 业务表（P1 的 C1c）。逐维进不进、以什么口径进的裁定在 docs/MVP裁定.md §一，这里只写结构与口径列。
-- 列的取舍全部对着 source_probe_log 里最近一次内容级探针的 fields_seen / sample 来的：
-- 探针没量到的字段不建列（CT 的白名单里没有起止日期，所以 trial 就没有日期列），
-- 量到但源不给的列照建并留空（freq_band / paf / label_zh），页面据此出空态。
--
-- 三条一起生效的约定：
--   1. 每张事实表都带 source_id / dataset_release_id / extract_method / review_status / loaded_at，
--      任何一个数都要能回答"哪个源的哪个版本、怎么解析出来的、人看过没有"。
--      `python db/tests/run.py status` 把这五列当成门禁报出来，新表漏了当场看得见。
--   2. 进唯一键的列一律 NOT NULL DEFAULT ''（年份用 0），不用 NULL：唯一键里的 NULL 允许重复，
--      装载器按业务键 UPSERT 就会静默长出双份。这一条与 dataset_release.upstream_version 同源。
--   3. 无外键，与"源与探针"那四张表一致：库是 etl 与后端的唯一契约，两侧都不生成 schema。
--
-- 本文件末尾的 DDL 与 db/schema.sql 里的业务表段是同一份内容，改结构要两边一起改。

-- 疾病主档。它是 etl/onco_etl/targets.py 那 18 行的库内镜像加 MONDO 实测解析结果，
-- 不是第二份人工录入入口：code 一一对应，装载器按声明覆盖，人不直接改这张表。
CREATE TABLE IF NOT EXISTS `disease` (
  `id` int NOT NULL AUTO_INCREMENT,
  `code` varchar(32) NOT NULL COMMENT '仓库内短码，同时是前端 slug',
  `name_zh` varchar(96) NOT NULL,
  `name_en` varchar(96) NOT NULL,
  `category` varchar(24) NOT NULL DEFAULT '' COMMENT 'carcinoma / heme，生存率与组织学口径的粗分类',
  `sex` enum('both','female','male') NOT NULL COMMENT '不给默认值：性别决定 SEER 该出几张分性别的表，猜错的代价是把女性专属癌当成两性通用',
  `icd10` varchar(64) NOT NULL DEFAULT '' COMMENT 'ICD-10 章节段（C18-C21），跨源对齐的第一把钥匙',
  `icdo3` varchar(64) NOT NULL DEFAULT '' COMMENT 'ICD-O-3 拓扑码段，器官树挂载依据',
  `icd9` varchar(128) NOT NULL DEFAULT '' COMMENT '逗号分隔的 ICD-9 前缀，只为把 MONDO 的亚部位 term 捞到正确粒度，不做美国老数据',
  `mondo_id` varchar(24) NOT NULL DEFAULT '',
  `mondo_name` varchar(128) NOT NULL DEFAULT '' COMMENT 'MONDO 主条目的英文首选名，与上面 name_en 是两个东西：源叫 lung cancer，我们叫 Lung and Bronchus',
  `ncit_id` varchar(24) NOT NULL DEFAULT '' COMMENT '跨源枢纽。实测主条目 NCIT xref 18/18，而 MONDO 自带的 MESH 只 7/18、EFO 5/18，带不动文献维',
  `xrefs` json DEFAULT NULL COMMENT '其余 xref 原样存（DOID/MEDGEN/UMLS/SCTID/OMIM/Orphanet/ICD10CM/ICD9…），稀疏的码不设列',
  `ot_node` varchar(24) NOT NULL DEFAULT '' COMMENT '研究层实际查询的节点。只有乳腺癌与 mondo_id 不同（targets.OT_NODE），换档前后的数都在探针 message 里可对账',
  `gwas_uris` json DEFAULT NULL COMMENT '效应量维额外认领的同级档（targets.GWAS_URI），空数组＝只认主条目',
  `gbd_cause` varchar(16) NOT NULL DEFAULT '' COMMENT 'GBD 病因层级 L3 档。IHME 账号到位前不出数，这一列只是把对齐关系钉住',
  `gco_today` varchar(8) NOT NULL DEFAULT '',
  `gco_time` varchar(8) NOT NULL DEFAULT '' COMMENT '与 gco_today 分开声明：两套是各自独立的码空间，同一 ICD-O-3 段在两边归到不同的病',
  `search_terms` json NOT NULL COMMENT '研究层查询词表，第一项是 MeSH 主题词。trial 与 publication 的命中全按它算，两侧共用一份是为了矩阵两列口径可比',
  `pdq_pages` json NOT NULL COMMENT 'cancer.gov 页面相对路径清单。这一列只能由人填：247 个摘要页里只有 12 段开了病人版，同一 ICD 段还会被拆成多档',
  `source_id` int NOT NULL,
  `dataset_release_id` bigint DEFAULT NULL,
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL COMMENT '这一行是 declared：行本身来自仓库内声明，mondo_name/ncit_id/xrefs 三列才是 MONDO 解析结果',
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_disease_code` (`code`),
  KEY `idx_disease_mondo` (`mondo_id`),
  KEY `idx_disease_ncit` (`ncit_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 器官节点。两类真实存在的节点：SEER 的 82 个 site recode 分组、MONDO 按 ICD-9 捞出的亚部位 term。
-- 332 个三位拓扑码刻意不落表——那是 recode 区间展开出来的合成码，SEER 文件里没有一行长这样，
-- 存进来会让人以为源给了层级。区间留在 icdo3_range，要重算随时能算。
CREATE TABLE IF NOT EXISTS `anatomy_node` (
  `id` int NOT NULL AUTO_INCREMENT,
  `kind` enum('site_recode','subsite_term') NOT NULL,
  `code` varchar(128) NOT NULL COMMENT 'site_recode 存 SEER 原文写法（"C180, C182-C189, C199"），subsite_term 存 MONDO ID',
  `label` varchar(191) NOT NULL DEFAULT '' COMMENT '源自己的英文标签：LUNG & BRONCHUS / bronchus cancer',
  `label_zh` varchar(96) DEFAULT NULL COMMENT '建而不填，与中文症状名同一条裁定：中文器官名没有可匿名取回的公开源，页面按英文标签加病名呈现',
  `icdo3_range` varchar(128) NOT NULL DEFAULT '' COMMENT '亚部位 term 对应的 ICD-O-3 段落（由 ICD-9 前缀收窄得到）',
  `icd9` varchar(16) NOT NULL DEFAULT '',
  `source_id` int NOT NULL,
  `dataset_release_id` bigint DEFAULT NULL,
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL COMMENT 'site_recode 是 l1_structured（xlsx 直取），subsite_term 也是——按 xref 精确匹配，没有模糊路径',
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_anatomy_kind_code` (`kind`,`code`),
  KEY `idx_anatomy_label` (`label`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `disease_anatomy` (
  `id` int NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `anatomy_node_id` int NOT NULL,
  `role` enum('primary','subsite') NOT NULL COMMENT 'primary＝器官级分组，页面"关联器官"显示它；subsite 只做下钻，与父级不混排',
  `basis` enum('icdo3_overlap','mondo_icd9') NOT NULL COMMENT '挂载凭什么成立：前者＝targets.icdo3 与该 recode 的拓扑码集合相交（探针 18/18 用的同一判据），后者＝MONDO term 的 ICD-9 xref 命中 targets.icd9',
  `matched_codes` varchar(255) NOT NULL DEFAULT '' COMMENT '相交到的三位码，回答"凭什么把这条算给肺"——挂载错法在报告里表现为命中率很高，必须留可查的依据',
  `source_id` int NOT NULL,
  `dataset_release_id` bigint DEFAULT NULL,
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_disease_anatomy` (`disease_id`,`anatomy_node_id`),
  KEY `idx_disease_anatomy_node` (`anatomy_node_id`,`role`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 组织学。同一份 SEER sitetype.xlsx 是 site recode × 形态学码的交叉表（82 × 176 组 = 803 个码），
-- 所以码表能直取，逐病展开要经过 recode 这一跳——那是我们的推导，不是源说的话。
CREATE TABLE IF NOT EXISTS `histology_code` (
  `id` int NOT NULL AUTO_INCREMENT,
  `code` varchar(8) NOT NULL COMMENT '形态学四位码，如 8010',
  `behavior` char(1) NOT NULL DEFAULT '' COMMENT '0 良性 / 1 动态未定 / 2 原位 / 3 恶性。基准全是恶性肿瘤，装载器只放 /3 进来',
  `code_behavior` varchar(12) NOT NULL COMMENT 'SEER 文件里的原样写法：8010/3',
  `label` varchar(191) NOT NULL DEFAULT '',
  `group_code` varchar(8) NOT NULL DEFAULT '' COMMENT '三位组码（801），同一文件给的上一层，176 个',
  `group_label` varchar(191) NOT NULL DEFAULT '',
  `source_id` int NOT NULL,
  `dataset_release_id` bigint DEFAULT NULL,
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_histology_code` (`code_behavior`),
  KEY `idx_histology_group` (`group_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `disease_histology` (
  `id` int NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `histology_code_id` int NOT NULL,
  `via_recode` varchar(128) NOT NULL DEFAULT '' COMMENT '从哪个 site recode 展开来的，多对一时能追到每一条的出处',
  `basis` enum('via_site_recode','declared') NOT NULL DEFAULT 'via_site_recode' COMMENT '覆盖度矩阵里没有组织学这一列（源没有逐病格）：这一维是自建展开，不许被读成"源说过这个病有这些组织学类型"',
  `source_id` int NOT NULL,
  `dataset_release_id` bigint DEFAULT NULL,
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_disease_histology` (`disease_id`,`histology_code_id`),
  KEY `idx_disease_histology_code` (`histology_code_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 症状。按源分行，不做翻译列：中文路只有 7/18，并到同一行会让另外 11 病看起来也有中文名。
CREATE TABLE IF NOT EXISTS `symptom` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `source_id` int NOT NULL COMMENT 'PDQ（英文 18/18）、中文维基条目（5 病）、WHO 中文版（3 病）是三个源，逐行带 source_id 才谈得上分开报口径与商用限制',
  `dataset_release_id` bigint DEFAULT NULL,
  `name` varchar(255) NOT NULL COMMENT '源里的说法原样存，不规范成同义词表：反查按这一串做，混排会造出源里没有的症状',
  `name_lang` enum('en','zh') NOT NULL,
  `heading` varchar(191) NOT NULL DEFAULT '' COMMENT '所在小节标题，中文维基那一列会写出"症狀及診斷"这类章节名',
  `source_url` varchar(255) NOT NULL DEFAULT '' COMMENT '条目出自哪一页。女性乳腺的清单取自男性乳腺癌页，这一列就是那条口径的落点',
  `anchor` varchar(96) NOT NULL DEFAULT '' COMMENT '页内锚点，前端要跳到那一段时用',
  `extract_kind` enum('list_item','sentence') NOT NULL DEFAULT 'list_item' COMMENT 'sentence＝页面没有清单、从散文按句切出来的。逐病达标数只算 list_item，句子不算症状项',
  `page_lastmod` date DEFAULT NULL COMMENT 'cancer.gov sitemap 的 lastmod，页面改版检测用',
  `freq_band` varchar(16) DEFAULT NULL COMMENT '建而不填：PDQ 症状小节百分号出现数实测 0，全站只有 Orphanet 有六档而它常见上皮癌 0 命中。抽取变不出源里没有的东西，页面按"暂无可靠来源"出空态',
  `provenance` varchar(128) NOT NULL DEFAULT '' COMMENT 'L3 兜底若启用必须写 derived-from-<源>-<rev>（规范定在 docs/数据源探针计划.md），本轮没有一行用到',
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL COMMENT '症状维实测落在 l2_rule：HTML 的 <li> 清单规则解析就过判据，不需要 NER',
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed' COMMENT '中文维基那 17 条里有 13 条是 0–IV 期与 A–D/B1–C2 的分期定义，按 WIKI_EYEBALL 声明的条数只留真症状条目，其余置 rejected 留痕',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_symptom` (`disease_id`,`source_id`,`name_lang`,`name`),
  KEY `idx_symptom_lookup` (`name_lang`,`name`) COMMENT '症状反查的入口：按名字取疾病集合，中英文分开走不混排',
  KEY `idx_symptom_disease` (`disease_id`,`name_lang`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 危险因素。实测到的只有遗传关联那一层；可干预暴露与归因强度都在 IHME 授权门后。
CREATE TABLE IF NOT EXISTS `risk_factor` (
  `id` int NOT NULL AUTO_INCREMENT,
  `kind` enum('genetic_locus','exposure') NOT NULL COMMENT 'genetic_locus＝GWAS 的位点/基因，是本站唯一实测到的一层；exposure＝可干预暴露，等 CRA 账号，建而不填',
  `label` varchar(191) NOT NULL COMMENT 'genetic_locus 存 MAPPED_GENE（无 mapped gene 时退到 SNPS），exposure 存 CRA 的危险因素名',
  `label_zh` varchar(96) DEFAULT NULL COMMENT '建而不填，同中文器官名：没有可匿名取回的中文名源',
  `source_id` int NOT NULL,
  `dataset_release_id` bigint DEFAULT NULL,
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_risk_factor` (`kind`,`label`),
  KEY `idx_risk_factor_label` (`label`) COMMENT '危险因素反查的入口'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `disease_risk_factor` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `risk_factor_id` int NOT NULL,
  `role` enum('genetic','exposure') NOT NULL DEFAULT 'genetic' COMMENT '只有 genetic 一层有数：GWAS 给的是遗传易感性而非可干预暴露，页面文案不许写成"危险因素排行"',
  `assoc_key` char(40) NOT NULL COMMENT 'sha1(疾病档|SNPS|STRONGEST SNP-RISK ALLELE|PUBMEDID)。GWAS 一行是一个关联不是一个位点，同一位点会被多个研究反复报，幂等键必须含研究',
  `uri_tier` enum('main','declared') NOT NULL COMMENT '这一行挂在主条目还是 targets.GWAS_URI 的声明档上。两个口径的覆盖数是 14/18 与 18/18，混成一个数就是虚报',
  `trait_label` varchar(191) NOT NULL DEFAULT '' COMMENT 'MAPPED_TRAIT 原文，声明档命中时它不等于本病的 name_en',
  `trait_uri` varchar(32) NOT NULL DEFAULT '',
  `snps` varchar(96) NOT NULL DEFAULT '',
  `risk_allele` varchar(128) NOT NULL DEFAULT '' COMMENT 'STRONGEST SNP-RISK ALLELE，形如 rs1051730[A]',
  `chr_id` varchar(4) NOT NULL DEFAULT '',
  `chr_pos` int NOT NULL DEFAULT 0,
  `risk_allele_freq` decimal(6,4) DEFAULT NULL,
  `p_value_text` varchar(24) NOT NULL DEFAULT '' COMMENT '原样存 "1E-50" 这类写法：小数存不下这个量级，画图上界用 mlog',
  `pvalue_mlog` decimal(8,2) DEFAULT NULL COMMENT 'GWAS 自己算好的 -log10(p)，排序与画轴用它',
  `or_beta` decimal(16,6) DEFAULT NULL,
  `effect_kind` enum('or','beta','unknown') NOT NULL DEFAULT 'unknown' COMMENT '源把 OR 与 β 装进同一列 `OR or BETA`，方向只写在 CI 文本的 unit increase/decrease 注记里，实测无法自动判定（全表值为负的只有 6 行）——整列默认未判定，不许按"多数是 OR"猜',
  `ci95_text` varchar(64) NOT NULL DEFAULT '' COMMENT '95% CI (TEXT) 原文，区间与方向注记都在这一串里',
  `pubmedid` int NOT NULL DEFAULT 0,
  `study_accession` varchar(16) NOT NULL DEFAULT '',
  `initial_sample` varchar(48) NOT NULL DEFAULT '',
  `replication_sample` varchar(48) NOT NULL DEFAULT '',
  `paf` decimal(6,2) DEFAULT NULL COMMENT '建而不填：两半效应量都在 IHME 授权门后（vizhub 数据面四个路由全 401）。UI 不画数值榜，标"需 IHME 授权"',
  `paf_basis` varchar(64) DEFAULT NULL COMMENT '将来填 paf 时记它是哪个 measure、哪个年份窗算出来的',
  `source_id` int NOT NULL,
  `dataset_release_id` bigint DEFAULT NULL,
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_drf_assoc` (`disease_id`,`source_id`,`assoc_key`),
  KEY `idx_drf_disease` (`disease_id`,`role`,`pvalue_mlog`),
  KEY `idx_drf_factor` (`risk_factor_id`,`disease_id`) COMMENT '危险因素反查：一个位点关联到哪几个病'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 统计层长表。三个源（GLOBOCAN 国家单点、GCO 年度序列、SEER 页面表）的发病率/死亡率/年龄组
-- 都落这里，分开的是 estimate_basis 而不是分表——分表会让人以为可以跨表相减。
CREATE TABLE IF NOT EXISTS `stat_fact` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '' COMMENT '一个源可发多个数据集：GCO 的 gco-today-national 与 gco-overtime-series 是两套码空间',
  `dataset_release_id` bigint DEFAULT NULL,
  `metric` varchar(32) NOT NULL COMMENT '装载器写进去的值见 docs/MVP裁定.md：incidence/mortality/prevalence 各带 _asr/_crude_rate/_total，SEER 的 new_case_rate/death_rate/survival_rate_5y 年度序列，age_case_pct/age_death_pct，研究层的 trial_count/publication_count。不用 enum 是因为每接一个新接口都会添值，改 enum 要一次迁移',
  `unit` enum('count','per_100k','percent','ratio') NOT NULL,
  `value` decimal(16,4) NOT NULL COMMENT 'SEER 用 "-" 表示"无观测"而不是 0，那种格子直接不落行',
  `year` int NOT NULL DEFAULT 0 COMMENT '0＝不是年度序列：国家级单点估算、年龄组占比、按声明词命中的条数都是 0',
  `age_band` varchar(32) NOT NULL DEFAULT '' COMMENT '按源原样存。SEER 只有 8 档宽分组（<20 到 >84），GCO 是 18 档，两套混画会得出假的年龄梯度',
  `sex` enum('both','male','female') NOT NULL DEFAULT 'both',
  `region` varchar(48) NOT NULL DEFAULT '' COMMENT 'World / China / US / SEER 8 / SEER 12 / SEER 21。生存率与发病率一律是美国登记处口径，页面必须写明不是中国数据',
  `estimate_basis` enum('national_estimate','registry_extrapolated','registry_cohort','model_trend','query_count') NOT NULL COMMENT '这一列是统计层最要紧的口径：GLOBOCAN 的国家级估算与 GCO Over Time 的登记处外推（中国是 5 个登记处覆盖 60% 人口、最新一年 2017）不同源，两列分开存、不可相减成趋势；SEER 的 Modeled Trend 是拟合线不是观测值；query_count 是"按声明词命中多少条"，不是流行病学计数',
  `cohort_note` varchar(128) NOT NULL DEFAULT '' COMMENT '源标的口径行原文，如 "SEER 21 2019–2023, Age-Adjusted"。SEER 同一页并存三套 cohort，原样带出来而不是取一个代表值',
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL COMMENT 'SEER 那一半是 l2_rule（页面表格 HTML），GCO/GLOBOCAN 是 l1_structured（JSON 接口）',
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stat_fact` (`disease_id`,`source_id`,`dataset_code`,`metric`,`year`,`age_band`,`sex`,`region`,`estimate_basis`),
  KEY `idx_stat_query` (`metric`,`region`,`year`) COMMENT '跨病比较与排行榜按 metric+region 取，不带病码',
  KEY `idx_stat_disease` (`disease_id`,`metric`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 分期别五年生存率。stat_fact 存的是全分期的年度序列，这张表存一页分期表，两者不重复写。
CREATE TABLE IF NOT EXISTS `survival` (
  `id` int NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '',
  `dataset_release_id` bigint DEFAULT NULL,
  `stage` varchar(64) NOT NULL COMMENT '实体瘤是 Localized/Regional/Distant/Unknown 四档，NHL 与骨髓瘤是 Ann Arbor，白血病整页没有分期表（源不提供，不是解析失败）只有 All stages 一行',
  `stage_scheme` enum('seer_summary','ann_arbor','none') NOT NULL COMMENT '两套分期不是一套，画在同一个轴上会让人以为可以横着比',
  `window_label` varchar(64) NOT NULL DEFAULT '' COMMENT 'SEER 标的年份窗原文',
  `year` int NOT NULL DEFAULT 0,
  `rate_pct` decimal(6,2) NOT NULL,
  `is_observed` tinyint(1) NOT NULL DEFAULT '1' COMMENT '0＝Modeled Trend 拟合值。五年生存率 Observed 止于 2018、拟合到 2023，最后五年全是外推，不标注等于把预测当观测发布',
  `region` varchar(48) NOT NULL DEFAULT 'US',
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_survival` (`disease_id`,`source_id`,`stage`,`window_label`,`year`),
  KEY `idx_survival_disease` (`disease_id`,`stage_scheme`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 前沿研究层。疾病键只能按 targets.search_terms 声明词查——CT 的 condition 是自由文本、
-- EPMC 的 MH: 主题词字段只覆盖全库 2.2%，两侧都没有可对齐的 ID。
CREATE TABLE IF NOT EXISTS `trial` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '',
  `dataset_release_id` bigint DEFAULT NULL,
  `nct_id` varchar(20) NOT NULL,
  `title` text COMMENT 'official title',
  `brief_title` varchar(512) NOT NULL DEFAULT '',
  `overall_status` varchar(48) NOT NULL DEFAULT '',
  `status_bucket` enum('active','idle','completed','other') NOT NULL COMMENT '探针的达标计数按这三档走（在招/未招但在随访/已完成），原文另存 overall_status',
  `study_type` varchar(24) NOT NULL DEFAULT '',
  `phases` json DEFAULT NULL,
  `design_info` json DEFAULT NULL,
  `enrollment` int DEFAULT NULL COMMENT '源自己标"估算"还是"实际"，混不得，原文在 design_info 里没有就留 NULL',
  `conditions` json DEFAULT NULL,
  `interventions` json DEFAULT NULL,
  `arm_groups` json DEFAULT NULL,
  `primary_outcome` varchar(512) NOT NULL DEFAULT '',
  `eligibility` json DEFAULT NULL COMMENT '入排标准条目数组',
  `elig_sex` varchar(8) NOT NULL DEFAULT '',
  `healthy_volunteers` tinyint(1) DEFAULT NULL,
  `lead_sponsor` varchar(191) NOT NULL DEFAULT '',
  `collaborators` json DEFAULT NULL,
  `location_countries` json DEFAULT NULL COMMENT '国家数组。CT 匿名侧没有地理过滤器（filter.geo 与 aggFilters=geo 都被拒），"有没有中国参与的试验"只能取回自己数，所以数组原样存、由前端筛',
  `publications` json DEFAULT NULL,
  `fda_regulated` tinyint(1) DEFAULT NULL,
  `why_stopped` text,
  `matched_terms` json DEFAULT NULL COMMENT '声明词表里哪几项命中了它。裸写主题词自带 MeSH 展开，与加引号是两种检索，命中数不一样',
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_trial` (`disease_id`,`source_id`,`nct_id`) COMMENT '一个 NCT 命中多个声明词时合并成一行，词记在 matched_terms；同一试验跨病出现是允许的（肺与支气管的试验也常被算进胸膜）',
  KEY `idx_trial_status` (`disease_id`,`status_bucket`),
  KEY `idx_trial_nct` (`nct_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `publication` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '',
  `dataset_release_id` bigint DEFAULT NULL,
  `ext_key` varchar(64) NOT NULL COMMENT 'UPSERT 的业务键：pmid 优先，退到 doi，再退到标题哈希。EPMC 有 8~12% 的记录没有 pmid，用 NULL 进唯一键会长双份',
  `pmid` int DEFAULT NULL,
  `doi` varchar(96) NOT NULL DEFAULT '',
  `title` varchar(512) NOT NULL DEFAULT '',
  `journal` varchar(191) NOT NULL DEFAULT '' COMMENT 'EPMC 的 source 字段',
  `pub_year` smallint DEFAULT NULL,
  `is_oa` tinyint(1) DEFAULT NULL COMMENT '记录级 OA 标记。整维的"全文可得率"是两个数——facet 50.8% 与记录级抽样 31.7%，页面用前者、判断能不能挖正文看后者，所以逐行标记也要留着',
  `in_epmc` tinyint(1) DEFAULT NULL,
  `has_pdf` tinyint(1) DEFAULT NULL,
  `has_abstract` tinyint(1) DEFAULT NULL,
  `matched_terms` json DEFAULT NULL,
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_publication` (`disease_id`,`source_id`,`ext_key`),
  KEY `idx_publication_year` (`disease_id`,`pub_year`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 靶点与药。靶点分数按数据源拆开存（datasourceScores），只有一个合成分的话就没法说明
-- "这个分数是遗传学支持的还是文献共现堆出来的"，而反查页要的就是这个区分。
CREATE TABLE IF NOT EXISTS `target` (
  `id` int NOT NULL AUTO_INCREMENT,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '',
  `dataset_release_id` bigint DEFAULT NULL,
  `ot_id` varchar(32) NOT NULL COMMENT 'Open Targets 的 target id（Ensembl 形）',
  `approved_symbol` varchar(64) NOT NULL,
  `approved_name` varchar(191) NOT NULL DEFAULT '',
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_target_ot_id` (`source_id`,`ot_id`),
  KEY `idx_target_symbol` (`approved_symbol`) COMMENT '靶点反查按符号查，符号是全库习惯的叫法'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `disease_target` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `target_id` int NOT NULL,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '',
  `dataset_release_id` bigint DEFAULT NULL,
  `score` decimal(6,4) NOT NULL COMMENT '合成分 0–1，肺癌前三 EGFR 0.901 / KRAS 0.858 / ERBB2 0.842',
  `novelty` varchar(16) NOT NULL DEFAULT '',
  `datasource_scores` json DEFAULT NULL COMMENT '逐数据源分数 {egmn, gwas, clinvar, reactome, …}，"有证据关联"与"只是共现"的区分全靠它',
  `node_used` varchar(24) NOT NULL DEFAULT '' COMMENT '查询用的节点。乳腺癌用宽档 MONDO_0007254 而主条目是 MONDO_0004379，不记这一笔就没法解释为什么这一病条数比别家多',
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_disease_target` (`disease_id`,`target_id`,`source_id`),
  KEY `idx_disease_target_score` (`disease_id`,`score`),
  KEY `idx_disease_target_reverse` (`target_id`,`disease_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `drug` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '',
  `dataset_release_id` bigint DEFAULT NULL,
  `drug_id` varchar(32) NOT NULL DEFAULT '' COMMENT 'OT 的 chimb 码。取不到时留空串，业务键退到 (drug_name, phase)',
  `drug_name` varchar(191) NOT NULL,
  `phase` varchar(32) NOT NULL DEFAULT '' COMMENT '源标的在研阶段原文（Phase 1/2、Early Phase 1…），不映射成有序档：源自己就没排过序',
  `moa` json DEFAULT NULL COMMENT '机制，多机制是数组',
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_drug` (`disease_id`,`source_id`,`drug_name`,`phase`) COMMENT '药物反查要的是"这个药在哪些病上做到哪一期"，所以按 (病, 药, 阶段) 存而不是按靶点存。注意 P0 只量到 drugAndClinicalCandidates.count（合计 6,323 个），行级字段没实测过——装载器首次落库前要先把字段核对一遍并补一次探针',
  KEY `idx_drug_reverse` (`drug_name`),
  KEY `idx_drug_disease` (`disease_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
