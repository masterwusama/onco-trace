-- 0005：危险因素装载（C2e）之前，把两张表的列对着"真正要落的那 6,280 行"重拍。
-- C1c 那版是按 source_probe_log 的 fields_seen 拍的，没量过命中声明档的那些行，三处装不下：
-- INITIAL SAMPLE SIZE 实测最长 357、REPLICATION 298（列宽 48）；CHR_ID 与 CHR_POS 在多 SNP 行
-- 是分号串（最长 17 / 53），而 CHR_POS 原本是 int——装不下就会截断或报错。
-- 另外 uri_tier 改成可空（CRA 的关联没有档位概念）、role 去掉默认值（默认 genetic 会把 CRA 的
-- 71 行伪装成遗传关联）。列定义逐字取自 db/schema.sql，0002 与 schema.sql 那段同步改。

ALTER TABLE `risk_factor` MODIFY COLUMN `kind` enum('genetic_locus','exposure') NOT NULL COMMENT 'genetic_locus＝GWAS 的位点/基因；exposure＝可干预暴露，取 CRA A2 表 Risk 列的 REI 名（实测 33 个、71 条关联、17/18 病）。两类的缺口分开看：前者不是可干预暴露，后者没有强度';
ALTER TABLE `risk_factor` MODIFY COLUMN `label` varchar(191) NOT NULL COMMENT 'genetic_locus 存 MAPPED_GENE 整串（实测最长 48 字符；一行多个基因是分号相连的，不拆成多个节点——拆开要把同一条关联复制成几行，行数虚增），整列为空的（613/6,210 行）退到 SNPS；exposure 存 CRA 的危险因素名（实测最长 57 字符）';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `role` enum('genetic','exposure') NOT NULL COMMENT '不给默认值：默认 genetic 会让 CRA 那 71 行看起来像遗传关联。genetic 是遗传易感性不是可干预暴露，页面文案不许写成"危险因素排行"；exposure 有清单无强度';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `assoc_key` char(40) NOT NULL COMMENT 'GWAS：sha1(病码|STUDY ACCESSION|SNPS|STRONGEST SNP-RISK ALLELE|P-VALUE)——一行是一个关联不是一个位点，键必须含研究：实测按 PUBMEDID 构造 6,210 命中行只剩 5,669 组，折掉的 541 行里有 496 组研究号与 p 值都不同（一篇论文登记多个研究），那是漏写不是去重。只到研究号又太粗：同一次录入会把一个位点按两个 p 值报两次（17 组），加上 P-VALUE 才是 6,208 行。剩下 2 组只差一个连接号写法（`–` 与 `-`），本就是同一条关联，该折。CRA：sha1(cra|cause_id|rei_id)——两支形状本就不同，分开构造才不会互相撞键';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `uri_tier` enum('main','declared') DEFAULT NULL COMMENT 'GWAS 专用：这一行挂在主条目还是 targets.GWAS_URI 的声明档上，两个口径的覆盖数是 14/18 与 18/18，混成一个数就是虚报。CRA 的关联没有档位概念，留 NULL 而不是造一个 n/a 值';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `trait_label` varchar(191) NOT NULL DEFAULT '' COMMENT '源里这一行管这个病叫什么：GWAS 存 MAPPED_TRAIT 原文（声明档命中时它不等于本病的 name_en），CRA 存 GBD 的 Cause 名';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `trait_uri` varchar(32) NOT NULL DEFAULT '' COMMENT 'GWAS 存命中的 MONDO 档尾段（MONDO_0008903，13 字符），CRA 存 GBD:426 这种病因档';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `snps` varchar(96) NOT NULL DEFAULT '' COMMENT '实测最长 62（一行六个 rs 号分号相连）。CRA 行留空';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `risk_allele` varchar(128) NOT NULL DEFAULT '' COMMENT 'STRONGEST SNP-RISK ALLELE，形如 rs1051730[A]，实测最长 74。CRA 行留空';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `chr_id` varchar(24) NOT NULL DEFAULT '' COMMENT '多 SNP 行是分号串而不是单值（实测最长 17 字符，形如 14;14;14;14;14;14），原先按 varchar(4) 拍会截断';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `chr_pos` varchar(64) NOT NULL DEFAULT '' COMMENT '与 chr_id 同形状的分号串（实测最长 53 字符）。原先拍成 int：一行六个位点要么装不下要么报错，宁可用字符串也不截断';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `risk_allele_freq` decimal(6,4) DEFAULT NULL COMMENT '实测命中行 2,818/6,210 有，值域 0.0002–0.9998';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `p_value_text` varchar(24) NOT NULL DEFAULT '' COMMENT '原样存 "1E-245" 这类写法（实测最长 6）：小数存不下这个量级，画图上界用 mlog';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `pvalue_mlog` decimal(8,2) DEFAULT NULL COMMENT 'GWAS 自己算好的 -log10(p)，排序与画轴用它（实测 5.0–321.7，命中行全有）';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `or_beta` decimal(16,6) DEFAULT NULL COMMENT '实测命中行 0.0168–550.2、6,083/6,210 行有值';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `effect_kind` enum('or','beta','unknown') NOT NULL DEFAULT 'unknown' COMMENT '源把 OR 与 β 装进同一列 `OR or BETA`，方向只写在 CI 文本的 unit increase/decrease 注记里，实测无法自动判定（命中行负值 0 个，全表只有 6 行）——整列默认未判定，不许按"多数是 OR"猜';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `ci95_text` varchar(64) NOT NULL DEFAULT '' COMMENT '95% CI (TEXT) 原文，区间与方向注记都在这一串里（实测最长 35）。注意非空不等于有可解析区间：`[1.09-1.22] unit increase` 那种前缀形态能解析，6,210 命中行里解析不出的 458 行分三种——254 行写成 `(1.164-1.456)` 没有方括号、182 行只有 `[NR]`、22 行的连接号是 `–` 不是 `-`。三者都只影响"能不能自动算区间"，原文照存不丢，别按能解析的比例报覆盖';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `pubmedid` int NOT NULL DEFAULT 0 COMMENT 'CRA 行没有文献号，留 0';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `study_accession` varchar(16) NOT NULL DEFAULT '' COMMENT '实测最长 12（GCST90090980）。CRA 行留空';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `initial_sample` varchar(380) NOT NULL DEFAULT '' COMMENT '实测最长 357：源写的是 "1,352 African American cases, 9,610 African American controls, …" 这种逐层串，原样存不解析';
ALTER TABLE `disease_risk_factor` MODIFY COLUMN `replication_sample` varchar(380) NOT NULL DEFAULT '' COMMENT '实测最长 298，命中行 1,971/6,210 有（很多研究只在 INITIAL 里写了后续队列）';
