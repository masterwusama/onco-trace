-- 业务表（P1 的 C1c）。逐维进不进、以什么口径进的裁定在 docs/MVP裁定.md §一，这里只写结构与口径列。
-- 列的取舍全部对着 source_probe_log 里最近一次内容级探针的 fields_seen / sample 来的：
-- 探针没量到的字段不建列（CT 的白名单里没有起止日期，所以 trial 就没有日期列），
-- 量到但源不给的列照建并留空（freq_band / label_zh），页面据此出空态（paf 原在此列，
-- GBD Results 授权取数走通后已填，见 0008）。
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
  `gbd_cause` varchar(16) NOT NULL DEFAULT '' COMMENT 'GBD 病因层级 L3 档，GBD Results 授权取数的对齐键：死亡年龄组与 PAF 两份 ZIP 都按它取回（实测 18/18 有数），不再是钉住对齐不出数的空列',
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
  `source_id` int NOT NULL COMMENT 'PDQ（英文 185 行 18/18 病）、中文维基条目（67 行 5 病）、WHO 中文版（18 行 3 病）是三个源，逐行带 source_id 才谈得上分开报口径与商用限制。维基那一路挂在 wikidata 那条登记下：三条中文路同一支探针检测、同一份许可（CC BY-SA），只有条目章节这一条给得出症状行',
  `dataset_release_id` bigint DEFAULT NULL,
  `name` varchar(255) NOT NULL COMMENT '源里的说法原样存，不规范成同义词表：反查按这一串做，混排会造出源里没有的症状',
  `name_lang` enum('en','zh') NOT NULL,
  `heading` varchar(191) NOT NULL DEFAULT '' COMMENT '所在小节标题，中文维基那一列会写出"症狀及診斷"这类章节名',
  `source_url` varchar(255) NOT NULL DEFAULT '' COMMENT '条目出自哪一页。女性乳腺的清单取自男性乳腺癌页，这一列就是那条口径的落点',
  `anchor` varchar(96) NOT NULL DEFAULT '' COMMENT '页内锚点，前端要跳到那一段时用',
  `extract_kind` enum('list_item','sentence') NOT NULL DEFAULT 'list_item' COMMENT 'sentence＝页面没有清单、从散文按句切出来的，粒度与清单条目不同。装载的这一批恒为 list_item：PDQ 那 4 条句子实测全在乳腺栏目页（该页没有 <ul>），讲的是"症状因类型而异""早期往往没有症状"——关于症状的句子不是症状项，故不落',
  `page_lastmod` date DEFAULT NULL COMMENT 'cancer.gov sitemap 的 lastmod，页面改版检测用',
  `freq_band` varchar(16) DEFAULT NULL COMMENT '建而不填：PDQ 症状小节百分号出现数实测 0，全站只有 Orphanet 有六档而它常见上皮癌 0 命中。抽取变不出源里没有的东西，页面按"暂无可靠来源"出空态',
  `provenance` varchar(128) NOT NULL DEFAULT '' COMMENT 'L3 兜底若启用必须写 derived-from-<源>-<rev>（规范定在 docs/数据源探针计划.md），本轮没有一行用到',
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL COMMENT '症状维实测落在 l2_rule：HTML 的 <li> 清单规则解析就过判据，不需要 NER',
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed' COMMENT '逐条目测过的（源, 病）组合记 spot_checked：PDQ 5 病、维基 5 病、WHO 中文 3 病，其余如实记 unreviewed；维基那 67 条里 32 条按 wikidata.WIKI_DROP 的解析序号判非症状（分期定义、白血病亚型描述、并发症、名目下的释义段），置 rejected 而不是删掉',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_symptom` (`disease_id`,`source_id`,`name_lang`,`name`),
  KEY `idx_symptom_lookup` (`name_lang`,`name`) COMMENT '症状反查的入口：按名字取疾病集合，中英文分开走不混排',
  KEY `idx_symptom_disease` (`disease_id`,`name_lang`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 危险因素。两层都实测过，缺的东西不一样：遗传关联（GWAS）带效应量与 p 值、给不出暴露语义，
-- 可干预暴露（GBD CRA 的 A2 交叉表）给得出清单，四列度量只是"这个组合有数"的标记，
-- 强度由 GBD Results 的年龄标化 PAF 补上——但 PAF 是人群归因分数，与 OR 不可比。
CREATE TABLE IF NOT EXISTS `risk_factor` (
  `id` int NOT NULL AUTO_INCREMENT,
  `kind` enum('genetic_locus','exposure') NOT NULL COMMENT 'genetic_locus＝GWAS 的位点/基因；exposure＝可干预暴露，取 CRA A2 表 Risk 列的 REI 名（实测 33 个、71 条关联、17/18 病）。两类的缺口分开看：前者不是可干预暴露，后者的强度是 PAF（人群归因分数），与 OR 不可比',
  `label` varchar(191) NOT NULL COMMENT 'genetic_locus 存 MAPPED_GENE 整串（实测最长 48 字符；一行多个基因是分号相连的，不拆成多个节点——拆开要把同一条关联复制成几行，行数虚增），整列为空的（613/6,210 行）退到 SNPS；exposure 存 CRA 的危险因素名（实测最长 57 字符）',
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

-- 关系行。GWAS 一行是一个"关联"（位点 × 表型 × 研究），CRA 一行是一个"对应关系"（病因 × REI），
-- 同一张表分开的是 role 与那批 GWAS 专属列的空缺，而不是拆两张表——拆表会让人以为两边的
-- 强度可比，而 CRA 一侧的强度是 PAF（人群归因分数），与 OR 本就不可比。
CREATE TABLE IF NOT EXISTS `disease_risk_factor` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `risk_factor_id` int NOT NULL,
  `role` enum('genetic','exposure') NOT NULL COMMENT '不给默认值：默认 genetic 会让 CRA 那 71 行看起来像遗传关联。genetic 是遗传易感性不是可干预暴露，页面文案不许写成"危险因素排行"；exposure 有清单、强度是 PAF（人群归因分数）不是效应量',
  `assoc_key` char(40) NOT NULL COMMENT 'GWAS：sha1(病码|STUDY ACCESSION|SNPS|STRONGEST SNP-RISK ALLELE|P-VALUE)——一行是一个关联不是一个位点，键必须含研究：实测按 PUBMEDID 构造 6,210 命中行只剩 5,669 组，折掉的 541 行里有 496 组研究号与 p 值都不同（一篇论文登记多个研究），那是漏写不是去重。只到研究号又太粗：同一次录入会把一个位点按两个 p 值报两次（17 组），加上 P-VALUE 才是 6,208 行。剩下 2 组只差一个连接号写法（`–` 与 `-`），本就是同一条关联，该折。CRA：sha1(cra|cause_id|rei_id)——两支形状本就不同，分开构造才不会互相撞键',
  `uri_tier` enum('main','declared') DEFAULT NULL COMMENT 'GWAS 专用：这一行挂在主条目还是 targets.GWAS_URI 的声明档上，两个口径的覆盖数是 14/18 与 18/18，混成一个数就是虚报。CRA 的关联没有档位概念，留 NULL 而不是造一个 n/a 值',
  `trait_label` varchar(191) NOT NULL DEFAULT '' COMMENT '源里这一行管这个病叫什么：GWAS 存 MAPPED_TRAIT 原文（声明档命中时它不等于本病的 name_en），CRA 存 GBD 的 Cause 名',
  `trait_uri` varchar(32) NOT NULL DEFAULT '' COMMENT 'GWAS 存命中的 MONDO 档尾段（MONDO_0008903，13 字符），CRA 存 GBD:426 这种病因档',
  `snps` varchar(96) NOT NULL DEFAULT '' COMMENT '实测最长 62（一行六个 rs 号分号相连）。CRA 行留空',
  `risk_allele` varchar(128) NOT NULL DEFAULT '' COMMENT 'STRONGEST SNP-RISK ALLELE，形如 rs1051730[A]，实测最长 74。CRA 行留空',
  `chr_id` varchar(24) NOT NULL DEFAULT '' COMMENT '多 SNP 行是分号串而不是单值（实测最长 17 字符，形如 14;14;14;14;14;14），原先按 varchar(4) 拍会截断',
  `chr_pos` varchar(64) NOT NULL DEFAULT '' COMMENT '与 chr_id 同形状的分号串（实测最长 53 字符）。原先拍成 int：一行六个位点要么装不下要么报错，宁可用字符串也不截断',
  `risk_allele_freq` decimal(6,4) DEFAULT NULL COMMENT '实测命中行 2,818/6,210 有，值域 0.0002–0.9998',
  `p_value_text` varchar(24) NOT NULL DEFAULT '' COMMENT '原样存 "1E-245" 这类写法（实测最长 6）：小数存不下这个量级，画图上界用 mlog',
  `pvalue_mlog` decimal(8,2) DEFAULT NULL COMMENT 'GWAS 自己算好的 -log10(p)，排序与画轴用它（实测 5.0–321.7，命中行全有）',
  `or_beta` decimal(16,6) DEFAULT NULL COMMENT '实测命中行 0.0168–550.2、6,083/6,210 行有值',
  `effect_kind` enum('or','beta','unknown') NOT NULL DEFAULT 'unknown' COMMENT '源把 OR 与 β 装进同一列 `OR or BETA`，方向只写在 CI 文本的 unit increase/decrease 注记里，实测无法自动判定（命中行负值 0 个，全表只有 6 行）——整列默认未判定，不许按"多数是 OR"猜',
  `ci95_text` varchar(64) NOT NULL DEFAULT '' COMMENT '95% CI (TEXT) 原文，区间与方向注记都在这一串里（实测最长 35）。注意非空不等于有可解析区间：`[1.09-1.22] unit increase` 那种前缀形态能解析，6,210 命中行里解析不出的 458 行分三种——254 行写成 `(1.164-1.456)` 没有方括号、182 行只有 `[NR]`、22 行的连接号是 `–` 不是 `-`。三者都只影响"能不能自动算区间"，原文照存不丢，别按能解析的比例报覆盖',
  `pubmedid` int NOT NULL DEFAULT 0 COMMENT 'CRA 行没有文献号，留 0',
  `study_accession` varchar(16) NOT NULL DEFAULT '' COMMENT '实测最长 12（GCST90090980）。CRA 行留空',
  `initial_sample` varchar(380) NOT NULL DEFAULT '' COMMENT '实测最长 357：源写的是 "1,352 African American cases, 9,610 African American controls, …" 这种逐层串，原样存不解析',
  `replication_sample` varchar(380) NOT NULL DEFAULT '' COMMENT '实测最长 298，命中行 1,971/6,210 有（很多研究只在 INITIAL 里写了后续队列）',
  `paf` decimal(6,2) DEFAULT NULL COMMENT 'exposure 行填 GBD 2023 的年龄标化 PAF（Deaths、2021、中国；71/71 条全中，值域 -7.33~100.00，负值＝保护方向照落）；genetic 行留 NULL——GWAS 没有 PAF。它是人群归因分数不是效应量，与 or_beta 不可比，不许把两类混成一个榜',
  `paf_basis` varchar(64) DEFAULT NULL COMMENT 'paf 的口径串（如 "GBD 2023 Deaths 年龄标化 2021"）：哪个 measure、哪一年算出来的记在这里，换口径重取时这一列跟着变',
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
  `metric` varchar(32) NOT NULL COMMENT '装载器写进去的值见 docs/MVP裁定.md：incidence/mortality/prevalence 各带 _asr/_crude_rate/_total，SEER 的 new_case_rate/death_rate 年度序列（五年存活率不在长表里，整维在 survival），age_case_pct/age_death_pct，研究层的 trial_count/publication_count/target_count/drug_count。不用 enum 是因为每接一个新接口都会添值，改 enum 要一次迁移',
  `unit` enum('count','per_100k','percent','ratio') NOT NULL,
  `value` decimal(16,4) NOT NULL COMMENT 'SEER 用 "-" 表示"无观测"而不是 0，那种格子直接不落行',
  `year` int NOT NULL DEFAULT 0 COMMENT '0＝不是年度序列：国家级单点估算、年龄组占比、按声明词命中的条数都是 0',
  `age_band` varchar(32) NOT NULL DEFAULT '' COMMENT '按源原样存。SEER 只有 8 档宽分组（<20 到 >84），GCO 是 18 档，GBD 是 20 档（<5 到 95+），三套混画会得出假的年龄梯度',
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

-- 五年存活率整维：一页分期表、At a Glance 的全期头条、5-Year Relative Survival 的逐年序列。
-- 三层都在这张表，stat_fact 不重复写同一个数——同一个数进两张表，迟早有一边先漂。
CREATE TABLE IF NOT EXISTS `survival` (
  `id` int NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '',
  `dataset_release_id` bigint DEFAULT NULL,
  `stage` varchar(64) NOT NULL COMMENT '实体瘤与骨髓瘤是 Localized/Regional/Distant/Unknown 四档（骨髓瘤同为血液肿瘤，源用的却是 SEER 汇总档），NHL 是 Ann Arbor 五档，白血病整页没有分期表（源不提供，不是解析失败），它只有 All stages 的头条与逐年序列',
  `stage_scheme` enum('seer_summary','ann_arbor','none') NOT NULL COMMENT '两套分期不是一套，画在同一个轴上会让人以为可以横着比',
  `window_label` varchar(64) NOT NULL DEFAULT '' COMMENT '分期档与头条是 SEER 标的年份窗原文；逐年序列那一层的标签是拼的（队列 + Observed/Modeled Trend + 该列非空格子的跨度），同年两个值靠它分开——源写在这一列下的年份窗句子只说 1975–2018，观测行与拟合行共用它就会撞唯一键',
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
  `status_bucket` enum('active','idle','completed','other') NOT NULL COMMENT '在招三档（RECRUITING / NOT_YET_RECRUITING / ENROLLING_BY_INVITATION）都落 active：装载器只取在招，实测 18 病 23,705 行全落这一档，其余三档要等接全状态时才有值，别拿它当"整库试验的状态分布"。原文另存 overall_status，分档表在装载器的 STATUS_BUCKET',
  `study_type` varchar(24) NOT NULL DEFAULT '',
  `phases` json DEFAULT NULL,
  `design_info` json DEFAULT NULL,
  `enrollment` int DEFAULT NULL COMMENT '招募数，取 enrollmentInfo.count（实测 23,704/23,705 行有这个对象，形状就是 {count, type}，没有 value 这个键）。估算还是实际标在 enrollmentInfo.type 里，混不得，所以整块留在 design_info：实测在招行 23,704 行是 ESTIMATED——在招的试验还没招完，源给不出实际数，这一列不能当"实际入组规模"用；没给的 1 行留 NULL',
  `conditions` json DEFAULT NULL,
  `interventions` json DEFAULT NULL,
  `arm_groups` json DEFAULT NULL,
  `primary_outcome` text COMMENT '主要结局并成一句（每条 measure [timeFrame]，用分号连）。varchar(512) 装不下：实测 18 病 23,705 行里 1,546 行（6.5%）超 512，最长 14,429 字符（有的试验把几十项都标成了主要结局），所以整段存进 text；列表页只渲染首句。装载器仍按 16,000 字符兜一道截（TEXT 的 65,535 字节上限按最坏四字节字符算），没标主要结局是空串',
  `eligibility` json DEFAULT NULL COMMENT '入排标准原文：CT 的 eligibilityCriteria 是一整段自由文本（带换行与项目符号），不是条目数组，所以存成 JSON 字符串而不是数组——前端要分条得自己按行拆',
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
  UNIQUE KEY `uk_trial` (`disease_id`,`source_id`,`nct_id`) COMMENT '一个 NCT 命中多个声明词时合并成一行，词记在 matched_terms；同一试验跨病出现是允许的（肺与支气管的试验也常被算进胸膜），实测 18 病 23,705 行是 19,254 个唯一 NCT、2,469 个跨病出现，最多一个试验挂在 17 个病上——所以行数不等于试验数',
  KEY `idx_trial_status` (`disease_id`,`status_bucket`),
  KEY `idx_trial_nct` (`nct_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `publication` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `disease_id` int NOT NULL,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '',
  `dataset_release_id` bigint DEFAULT NULL,
  `ext_key` varchar(64) NOT NULL COMMENT 'UPSERT 的业务键：pmid 优先，退到 doi，再退到标题哈希。实测 18 病 9,000 行里 697 行（7.7%）没 pmid，其中 540 行退到 doi、157 行只能标题哈希；用 NULL 进唯一键会长双份',
  `pmid` int DEFAULT NULL,
  `doi` varchar(96) NOT NULL DEFAULT '',
  `title` varchar(1024) NOT NULL DEFAULT '' COMMENT '标题原文。512 装不下：实测最长 542 字符（一条 "Re: …" 通信），整列放到 1,024',
  `journal` varchar(255) DEFAULT NULL COMMENT '期刊名，取 journalInfo.journal.title（实测 8,448/9,000 行有值、1,793 个刊名、最长 221 字符）。顶层的 source 字段不是期刊名，是 EPMC 的库别代码（9,000 行只有 MED/PPR/PMC/AGR 四个值），别拿它当期刊；没期刊名的 552 行里 540 行是预印本（PPR）',
  `pub_year` smallint DEFAULT NULL COMMENT '源标的出版年。实测这一批跨 2021→2027——2027 是在印记录提前给的年份，不是脏数据，所以"最近一年"不能按它截',
  `is_oa` tinyint(1) DEFAULT NULL COMMENT '记录级 OA 标记（isOpenAccess=Y）。整维的"全文可得率"有两个口径：查询 facet 50.8% 与本轮落库 9,000 行实测的 38.5%（同批里全文进了 EPMC 的占 41.4%），页面用 facet、判断能不能挖正文看记录级，所以逐行标记要留着',
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
  `novelty` decimal(8,6) DEFAULT NULL COMMENT 'OT 的新颖度是 0–1 的比例，实测数量级到 1e-4（0.0001003876489362007 这种），varchar(16) 装不下这么长的小数；装载器按 6 位取整，NULL 表示源没给',
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
  `drug_id` varchar(32) NOT NULL DEFAULT '' COMMENT 'OT 给的 CHEMBL 码。实测 18 病 6,323 行零行缺、前缀全是 CHEMBL，所以空串只是兜底；业务键不含它——同一个码会在多个病、多个阶段上重复，这列只用来回查源',
  `drug_name` varchar(255) NOT NULL COMMENT '药名原文（只 strip，不改大小写：实测 2,437 个不同药名里没有仅大小写不同的碰撞，按小写收拢与按原文存结果一致）。191 装不下：最长是前列腺癌一行 193 字符的描述性药名（自体细胞疗法那种整句名），而这列在 uk_drug 里，截断会把两个不同的药折成同一个键',
  `phase` varchar(32) NOT NULL DEFAULT '' COMMENT 'OT 的 maxClinicalStage 原文，形如 PHASE_2 / PHASE_1_2 / EARLY_PHASE_1（实测 11 个取值、最长 13 字符），不映射成有序档：源自己就没排过序',
  `moa` json DEFAULT NULL COMMENT '机制数组，装载器把同 (病, 药, 阶段) 多行的 mechanismsOfAction 去重并起来（源给 6,323 行三元组、收拢成落库 6,309 行，其中 4,128 行有机制、单行最多 15 条）；没机制是 NULL，不是空数组',
  `extract_method` enum('l1_structured','l2_rule','llm_extract','declared') NOT NULL,
  `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed',
  `loaded_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_drug` (`disease_id`,`source_id`,`drug_name`,`phase`) COMMENT '药物反查要的是"这个药在哪些病上做到哪一期"，所以按 (病, 药, 阶段) 存而不是按靶点存。行级字段按 18 病全量归档实测（data/raw/opentargets/rows-26.6.3-drug，6,323 行）：maxClinicalStage 11 个取值（最多的 PHASE_2 占 2,668 行、最少的 PREAPPROVAL 只有 1 行）、机制 4,128 行有值、0 行缺 drug.id；源给的是 (药, 靶点, 阶段) 三元组，同 (病, 药, 阶段) 最多撞 14 行，机制并成数组后 6,323 行收成 6,309 行；这个字段没有分页参数，每病 len(rows)==count 全等，装载器对每病比这个等式，不等就中止',
  KEY `idx_drug_reverse` (`drug_name`),
  KEY `idx_drug_disease` (`disease_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
