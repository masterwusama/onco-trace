-- 0003：把 stat_fact / survival 两处的列注释对齐到 C2c 实际装载的形状。
-- 五年存活率整维（分期档 + 全期头条 + 逐年序列的观测与拟合）都落 survival，
-- 长表里不再有 survival_rate_5y；逐年序列那一层的 window_label 是拼出来的标签，
-- 拼它而不是取源那句年份窗原文，是因为观测行与拟合行共用原文会撞 uk_survival。
-- 列定义逐字取自 db/schema.sql，改注释只改这一处就会三份分叉。

ALTER TABLE `stat_fact` MODIFY COLUMN `metric` varchar(32) NOT NULL COMMENT '装载器写进去的值见 docs/MVP裁定.md：incidence/mortality/prevalence 各带 _asr/_crude_rate/_total，SEER 的 new_case_rate/death_rate 年度序列（五年存活率不在长表里，整维在 survival），age_case_pct/age_death_pct，研究层的 trial_count/publication_count。不用 enum 是因为每接一个新接口都会添值，改 enum 要一次迁移';

ALTER TABLE `survival` MODIFY COLUMN `stage` varchar(64) NOT NULL COMMENT '实体瘤与骨髓瘤是 Localized/Regional/Distant/Unknown 四档（骨髓瘤同为血液肿瘤，源用的却是 SEER 汇总档），NHL 是 Ann Arbor 五档，白血病整页没有分期表（源不提供，不是解析失败），它只有 All stages 的头条与逐年序列';

ALTER TABLE `survival` MODIFY COLUMN `window_label` varchar(64) NOT NULL DEFAULT '' COMMENT '分期档与头条是 SEER 标的年份窗原文；逐年序列那一层的标签是拼的（队列 + Observed/Modeled Trend + 该列非空格子的跨度），同年两个值靠它分开——源写在这一列下的年份窗句子只说 1975–2018，观测行与拟合行共用它就会撞唯一键';
