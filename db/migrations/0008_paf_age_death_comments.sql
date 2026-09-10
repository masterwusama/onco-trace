-- 0008：GBD Results 授权取数走通之后，把六处"账号到位前/建而不填"的列注释翻成实测事实。
-- 纯注释迁移，列的形状一个没动：paf 的 decimal(6,2) 本就装得下 -7.33~100.00 的百分比
-- （PAF 源值是 0~1 的小数，×100 落两位小数；负值是保护方向照落）。列定义逐字取自
-- db/schema.sql，0002 与 schema.sql 那段同步改（表头那两段块注释只改两份 DDL 文件，
-- 不进迁移——它们不落库）。
--
-- 实测数字（2026-09-10，GBD 2023.0.0）：死亡年龄组 ZIP 951 行＝19 病因（18 癌＋410
-- Neoplasms 汇总层）× 3 性别 × 20 档（<5 到 95+）；410/Both 的 20 档求和
-- 2,401,092.52 与全年龄单行相等，分母用在场档求和有锚。18 病声明性别合计 319 行落
-- age_death_pct。PAF ZIP 71 对 (cause_id, rei_id) 与 CRA A2 骨架逐对相等，值域
-- -0.0733~1.0（×100 后 -7.33~100.00，负值 3 条＝保护方向，Lung|Smoking 最大 64.83）。

ALTER TABLE `disease` MODIFY COLUMN `gbd_cause` varchar(16) NOT NULL DEFAULT '' COMMENT 'GBD 病因层级 L3 档，GBD Results 授权取数的对齐键：死亡年龄组与 PAF 两份 ZIP 都按它取回（实测 18/18 有数），不再是钉住对齐不出数的空列';
ALTER TABLE `risk_factor` MODIFY COLUMN `kind` enum('genetic_locus','exposure') NOT NULL COMMENT 'genetic_locus＝GWAS 的位点/基因；exposure＝可干预暴露，取 CRA A2 表 Risk 列的 REI 名（实测 33 个、71 条关联、17/18 病）。两类的缺口分开看：前者不是可干预暴露，后者的强度是 PAF（人群归因分数），与 OR 不可比';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `role` enum('genetic','exposure') NOT NULL COMMENT '不给默认值：默认 genetic 会让 CRA 那 71 行看起来像遗传关联。genetic 是遗传易感性不是可干预暴露，页面文案不许写成"危险因素排行"；exposure 有清单、强度是 PAF（人群归因分数）不是效应量';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `paf` decimal(6,2) DEFAULT NULL COMMENT 'exposure 行填 GBD 2023 的年龄标化 PAF（Deaths、2021、中国；71/71 条全中，值域 -7.33~100.00，负值＝保护方向照落）；genetic 行留 NULL——GWAS 没有 PAF。它是人群归因分数不是效应量，与 or_beta 不可比，不许把两类混成一个榜';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `paf_basis` varchar(64) DEFAULT NULL COMMENT 'paf 的口径串（如 "GBD 2023 Deaths 年龄标化 2021"）：哪个 measure、哪一年算出来的记在这里，换口径重取时这一列跟着变';
ALTER TABLE `stat_fact` MODIFY COLUMN `age_band` varchar(32) NOT NULL DEFAULT '' COMMENT '按源原样存。SEER 只有 8 档宽分组（<20 到 >84），GCO 是 18 档，GBD 是 20 档（<5 到 95+），三套混画会得出假的年龄梯度';
