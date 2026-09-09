-- 0007：研究层装载（C2f）把 18 个病的试验、文献、靶点与药物整批取回归档之后，按那四份
-- 全量归档（data/raw/{ctgov_v2/rows-2026-09-08, europepmc/rows-2026-09-09,
-- opentargets/rows-26.6.3, opentargets/rows-26.6.3-drug}）重拍三张表的列宽与列注释。
-- C1c 与 0006 那两版是拿一两个病的抽样拍的，这轮对着全量抓到三类问题：
-- 一、列读错了源字段（这台最要命的一处）：trial.enrollment 读 enrollmentInfo.value，而真归档
--   里这个对象的形状是 {count, type}，value 根本不存在——整列会静默全 NULL，且不报错。
--   跑测器原本的 fixture 也是照"value/citation"猜的形状写的，于是一起自证通过；这次两边都
--   按归档改成 count/type，并把字段存在性对四份归档整批过了一遍（唯一恒空的一条是
--   trial.why_stopped，那是"只落在招"的口径所致，不是路径写错）。
-- 二、装不下：trial.primary_outcome 的 varchar(512) 被 6.5% 的行（1,546/23,705）撑破，最长
--   14,429 字符，整段改 text；publication.title 的 512 差 30 字符（一条 "Re: …" 通信 542），
--   放到 1,024；drug 的 191 药名宽会被前列腺癌一行 193 字符的描述性名撑破，而这列在
--   uk_drug 里——截断是把两个不同的药折成同一个键，不是少几个字。
-- 三、说错了话：publication.journal 存的是 EPMC 顶层的 source，那字段是库别代码（9,000 行只有
--   MED/PPR/PMC/AGR 四个值），期刊名在 journalInfo.journal.title 里（实测 8,448 行有值、最长
--   221 字符），所以列宽 191→255 并允许 NULL（预印本没期刊）；publication.ext_key 与 is_oa 的
--   注释挂着抽样时代的 8~12% 与 31.7%，本轮全量是 7.7% 与 38.5%；trial.status_bucket 写着
--   "三档走"而装载器只取在招，实测 23,705 行全落 active 一档；publication.pub_year 没说过
--   这一批含 2027（在印记录提前给年份），按"最近一年"筛会筛出问题行。
--   uk_trial 与 uk_drug 两把键的注释也按全量重抄：前者要说出"行数不等于试验数"
--   （23,705 行是 19,254 个 NCT、2,469 个跨病出现），后者原话"八个取值、183/263 行有机制"
--   数的是肺癌一病的 263 行，全量是 11 个取值、4,128/6,323 行有机制。
-- 列定义逐字取自 db/schema.sql，0002 与 schema.sql 那段同步改。

ALTER TABLE `trial` DROP INDEX `uk_trial`;

ALTER TABLE `trial`
  MODIFY COLUMN `status_bucket` enum('active','idle','completed','other') NOT NULL COMMENT '在招三档（RECRUITING / NOT_YET_RECRUITING / ENROLLING_BY_INVITATION）都落 active：装载器只取在招，实测 18 病 23,705 行全落这一档，其余三档要等接全状态时才有值，别拿它当"整库试验的状态分布"。原文另存 overall_status，分档表在装载器的 STATUS_BUCKET',
  MODIFY COLUMN `enrollment` int DEFAULT NULL COMMENT '招募数，取 enrollmentInfo.count（实测 23,704/23,705 行有这个对象，形状就是 {count, type}，没有 value 这个键）。估算还是实际标在 enrollmentInfo.type 里，混不得，所以整块留在 design_info：实测在招行 23,704 行是 ESTIMATED——在招的试验还没招完，源给不出实际数，这一列不能当"实际入组规模"用；没给的 1 行留 NULL',
  MODIFY COLUMN `primary_outcome` text COMMENT '主要结局并成一句（每条 measure [timeFrame]，用分号连）。varchar(512) 装不下：实测 18 病 23,705 行里 1,546 行（6.5%）超 512，最长 14,429 字符（有的试验把几十项都标成了主要结局），所以整段存进 text；列表页只渲染首句。装载器仍按 16,000 字符兜一道截（TEXT 的 65,535 字节上限按最坏四字节字符算），没标主要结局是空串';

ALTER TABLE `trial` ADD UNIQUE KEY `uk_trial` (`disease_id`,`source_id`,`nct_id`) COMMENT '一个 NCT 命中多个声明词时合并成一行，词记在 matched_terms；同一试验跨病出现是允许的（肺与支气管的试验也常被算进胸膜），实测 18 病 23,705 行是 19,254 个唯一 NCT、2,469 个跨病出现，最多一个试验挂在 17 个病上——所以行数不等于试验数';

ALTER TABLE `publication`
  MODIFY COLUMN `ext_key` varchar(64) NOT NULL COMMENT 'UPSERT 的业务键：pmid 优先，退到 doi，再退到标题哈希。实测 18 病 9,000 行里 697 行（7.7%）没 pmid，其中 540 行退到 doi、157 行只能标题哈希；用 NULL 进唯一键会长双份',
  MODIFY COLUMN `title` varchar(1024) NOT NULL DEFAULT '' COMMENT '标题原文。512 装不下：实测最长 542 字符（一条 "Re: …" 通信），整列放到 1,024',
  MODIFY COLUMN `journal` varchar(255) DEFAULT NULL COMMENT '期刊名，取 journalInfo.journal.title（实测 8,448/9,000 行有值、1,793 个刊名、最长 221 字符）。顶层的 source 字段不是期刊名，是 EPMC 的库别代码（9,000 行只有 MED/PPR/PMC/AGR 四个值），别拿它当期刊；没期刊名的 552 行里 540 行是预印本（PPR）',
  MODIFY COLUMN `pub_year` smallint DEFAULT NULL COMMENT '源标的出版年。实测这一批跨 2021→2027——2027 是在印记录提前给的年份，不是脏数据，所以"最近一年"不能按它截',
  MODIFY COLUMN `is_oa` tinyint(1) DEFAULT NULL COMMENT '记录级 OA 标记（isOpenAccess=Y）。整维的"全文可得率"有两个口径：查询 facet 50.8% 与本轮落库 9,000 行实测的 38.5%（同批里全文进了 EPMC 的占 41.4%），页面用 facet、判断能不能挖正文看记录级，所以逐行标记要留着';

ALTER TABLE `drug` DROP INDEX `uk_drug`;

ALTER TABLE `drug`
  MODIFY COLUMN `drug_id` varchar(32) NOT NULL DEFAULT '' COMMENT 'OT 给的 CHEMBL 码。实测 18 病 6,323 行零行缺、前缀全是 CHEMBL，所以空串只是兜底；业务键不含它——同一个码会在多个病、多个阶段上重复，这列只用来回查源',
  MODIFY COLUMN `drug_name` varchar(255) NOT NULL COMMENT '药名原文（只 strip，不改大小写：实测 2,437 个不同药名里没有仅大小写不同的碰撞，按小写收拢与按原文存结果一致）。191 装不下：最长是前列腺癌一行 193 字符的描述性药名（自体细胞疗法那种整句名），而这列在 uk_drug 里，截断会把两个不同的药折成同一个键',
  MODIFY COLUMN `phase` varchar(32) NOT NULL DEFAULT '' COMMENT 'OT 的 maxClinicalStage 原文，形如 PHASE_2 / PHASE_1_2 / EARLY_PHASE_1（实测 11 个取值、最长 13 字符），不映射成有序档：源自己就没排过序',
  MODIFY COLUMN `moa` json DEFAULT NULL COMMENT '机制数组，装载器把同 (病, 药, 阶段) 多行的 mechanismsOfAction 去重并起来（源给 6,323 行三元组、收拢成落库 6,309 行，其中 4,128 行有机制、单行最多 15 条）；没机制是 NULL，不是空数组';

ALTER TABLE `drug` ADD UNIQUE KEY `uk_drug` (`disease_id`,`source_id`,`drug_name`,`phase`) COMMENT '药物反查要的是"这个药在哪些病上做到哪一期"，所以按 (病, 药, 阶段) 存而不是按靶点存。行级字段按 18 病全量归档实测（data/raw/opentargets/rows-26.6.3-drug，6,323 行）：maxClinicalStage 11 个取值（最多的 PHASE_2 占 2,668 行、最少的 PREAPPROVAL 只有 1 行）、机制 4,128 行有值、0 行缺 drug.id；源给的是 (药, 靶点, 阶段) 三元组，同 (病, 药, 阶段) 最多撞 14 行，机制并成数组后 6,323 行收成 6,309 行；这个字段没有分页参数，每病 len(rows)==count 全等，装载器对每病比这个等式，不等就中止';
