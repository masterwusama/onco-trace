-- db_ot : 癌症与高致死疾病的结构化数据站
-- 风格对齐 db_va / db_r2r：无外键、事实表复合主键、时间戳由应用层以本地墙钟写入、
-- utf8mb4_0900_ai_ci、索引显式命名 uk_* / idx_*、非常识列写中文 COMMENT。
--
-- 本文件只有"源与探针"四张表，业务表（disease / anatomy_site / stat_cohort …）刻意不在这里。
-- 理由：P0 的任务是先实测每个维度到底有没有可自动落库的源。症状维已经踩过一次——
-- HPO/Orphanet/NCIt 的文档都声称有疾病↔症状注释，实测常见上皮癌只有 3~9 行且全是肿瘤同义复述。
-- 先按想象建 20 张表，探针回来发现喂不进数据，就得再写一轮迁移把它们改掉。
-- 覆盖度矩阵出来后由 docs/数据源探针计划.md 的出口判据决定建哪些表。
--
-- `db_migration` 由 db/tests/run.py 在首次 migrate 时自建，不写在这里，
-- 保持"schema.sql 只描述业务终态"这一点与 db_r2r 一致。

-- 1) 源登记表。P0 阶段它同时是"授权红线"的落点：legal_note 为空的源不许进入采集。
CREATE TABLE IF NOT EXISTS `source` (
  `id` int NOT NULL AUTO_INCREMENT,
  `code` varchar(32) NOT NULL COMMENT '全仓库引用源的短码：mondo/icdo3/seer_statfacts/gbd_results/…',
  `name` varchar(128) NOT NULL,
  `org` varchar(96) DEFAULT NULL,
  `source_type` enum('ontology','code_table','statistics','literature','trial_registry','association_db','html_doc') NOT NULL,
  `dimensions` json NOT NULL COMMENT '该源覆盖的维度：anatomy/identity/stat/survival/risk/trial/literature/symptom/narrative',
  `home_url` varchar(255) NOT NULL,
  `download_url` varchar(255) DEFAULT NULL COMMENT '机器可读入口；只有 HTML 正文的源留 NULL',
  `auth` enum('none','api_key','register','login') NOT NULL DEFAULT 'none',
  `license` varchar(96) DEFAULT NULL,
  `commercial_use` tinyint(1) NOT NULL DEFAULT '1',
  `attribution_required` tinyint(1) NOT NULL DEFAULT '1',
  `legal_note` varchar(255) NOT NULL COMMENT 'robots/ToS/再分发限制。为空视为未通过 P0 门禁',
  `robots_url` varchar(255) DEFAULT NULL,
  `rate_note` varchar(128) DEFAULT NULL,
  `fetch_mode` enum('once','annual','quarterly','monthly','weekly','daily') NOT NULL DEFAULT 'once',
  `reliability` enum('high','medium','low') NOT NULL DEFAULT 'medium',
  `status` enum('candidate','active','paused','dead','rejected') NOT NULL DEFAULT 'candidate' COMMENT 'candidate=待探针裁定；rejected=探针判空或授权不允许',
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_source_code` (`code`),
  KEY `idx_source_status` (`status`,`fetch_mode`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2) 已取到的数据集版本。存在的唯一目的是回答"上游变了吗"：
--    L3 抽取的作业不允许覆盖已确认行，只能在上游版本前进时重新出草稿，判定依据是这张表。
CREATE TABLE IF NOT EXISTS `dataset_release` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL COMMENT '一个源可发多个数据集：SEER 的 stat-facts 与 fast-stats 是两回事',
  `upstream_version` varchar(64) NOT NULL DEFAULT '' COMMENT '源自己的版本号/发布日期；取不到就留空串，不用 NULL——否则唯一键允许多行 NULL，同版本会被重复登记',
  `release_date` date DEFAULT NULL,
  `fetched_at` datetime NOT NULL,
  `bytes` bigint DEFAULT NULL,
  `sha256` char(64) DEFAULT NULL COMMENT '增量判定：同 sha 直接跳过重新解析',
  `rows_seen` int DEFAULT NULL,
  `raw_path` varchar(255) DEFAULT NULL COMMENT 'data/raw 下的归档路径，归一化规则改了要能重放',
  `note` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_release` (`source_id`,`dataset_code`,`upstream_version`),
  KEY `idx_release_fetched` (`source_id`,`fetched_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 3) 探针结果。P0 的产出物就是这张表 + docs/数据源覆盖度.md。
CREATE TABLE IF NOT EXISTS `source_probe_log` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `source_id` int NOT NULL,
  `dataset_code` varchar(64) NOT NULL DEFAULT '',
  `probed_at` datetime NOT NULL,
  `http_status` smallint DEFAULT NULL,
  `reachability` enum('direct','proxy','blocked','error') NOT NULL DEFAULT 'error' COMMENT 'direct/proxy/blocked 三态而非布尔：本机直连 Wikimedia 全线超时，无人值守的 scheduler 不能靠临时代理',
  `latency_ms` int DEFAULT NULL,
  `bytes` bigint DEFAULT NULL,
  `rows_seen` int DEFAULT NULL COMMENT '0 有两种含义（源给空集 / 解析器没匹配上），靠 message 区分，不要在报告里当成一回事',
  `diseases_covered` int DEFAULT NULL COMMENT '20 病基准里被覆盖到的个数，基准清单在 etl/onco_etl/targets.py',
  `diseases_total` int DEFAULT NULL,
  `fields_seen` json DEFAULT NULL COMMENT '实测到的列名/字段路径，写 stat_cohort 映射时照着它来',
  `sample` json DEFAULT NULL COMMENT '3~5 条真实样本行，避免写映射时反复去敲源站额度',
  `verdict` enum('ok','partial','empty','dead','blocked','unlicensed') NOT NULL,
  `criteria` varchar(255) DEFAULT NULL COMMENT '本次探针的判据原文，出报告时要能一一对上',
  `message` text,
  `raw_path` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_probe_source` (`source_id`,`probed_at`),
  KEY `idx_probe_verdict` (`probed_at`,`verdict`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 4) 任务运行史。与 db_va / db_r2r 同构：running 行单独提交，收口状态另一笔事务写，
--    verify 这类不落数的任务 written 传 NULL 而不是 0。
CREATE TABLE IF NOT EXISTS `etl_job_log` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_name` varchar(64) NOT NULL,
  `started_at` datetime NOT NULL,
  `finished_at` datetime DEFAULT NULL,
  `status` enum('running','success','failed') NOT NULL,
  `message` text,
  `stats` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_job_name` (`job_name`,`started_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
