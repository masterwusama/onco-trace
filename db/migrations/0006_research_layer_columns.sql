-- 0006：研究层装载（C2f）之前，把 trial / disease_target / drug / stat_fact 四处
-- 对着真正要落的那一批行重拍。C1c 那版是按探针的 fields_seen 拍的，没量过行级取值，
-- 四处装不下或说错了话：
-- novelty 实测是 19 位小数（0.0001003876489362007），varchar(16) 在严格模式下直接拒；
-- eligibility 列注释写的是"条目数组"，而 CT 的 eligibilityCriteria 是一整段自由文本，
-- 注释说错形状比装不下更坏——后端会按数组去解析然后静默拿到 NULL；
-- uk_drug 的注释挂着"行级字段没实测过"这句 P0 前置，本轮已实测（drug.id 是 CHEMBL 形、
-- maxClinicalStage 见到八个取值、机制数组 183/263 行有值、这个字段没有分页参数），把结论落在原位；
-- stat_fact.metric 的取值单少列了 target_count/drug_count 两个新度量。
-- 列定义逐字取自 db/schema.sql，0002 与 schema.sql 那段同步改。

ALTER TABLE `stat_fact` MODIFY COLUMN `metric` varchar(32) NOT NULL COMMENT '装载器写进去的值见 docs/MVP裁定.md：incidence/mortality/prevalence 各带 _asr/_crude_rate/_total，SEER 的 new_case_rate/death_rate 年度序列（五年存活率不在长表里，整维在 survival），age_case_pct/age_death_pct，研究层的 trial_count/publication_count/target_count/drug_count。不用 enum 是因为每接一个新接口都会添值，改 enum 要一次迁移';

ALTER TABLE `trial` MODIFY COLUMN `eligibility` json DEFAULT NULL COMMENT '入排标准原文：CT 的 eligibilityCriteria 是一整段自由文本（带换行与项目符号），不是条目数组，所以存成 JSON 字符串而不是数组——前端要分条得自己按行拆';

ALTER TABLE `disease_target` MODIFY COLUMN `novelty` decimal(8,6) DEFAULT NULL COMMENT 'OT 的新颖度是 0–1 的比例，实测数量级到 1e-4（0.0001003876489362007 这种），varchar(16) 装不下这么长的小数；装载器按 6 位取整，NULL 表示源没给';

ALTER TABLE `drug` DROP INDEX `uk_drug`, ADD UNIQUE KEY `uk_drug` (`disease_id`,`source_id`,`drug_name`,`phase`) COMMENT '药物反查要的是"这个药在哪些病上做到哪一期"，所以按 (病, 药, 阶段) 存而不是按靶点存。行级字段已实测：drug.id 是 CHEMBL 形、maxClinicalStage 见到八个取值、机制数组 183/263 行有值；这个字段没有分页参数（实测 1,036 行与 263 行两个病都整表返回），装载器对每病比 len(rows)==count，不等就中止';
