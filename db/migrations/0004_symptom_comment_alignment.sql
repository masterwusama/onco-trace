-- 0004：症状维装载（C2d）之后，把 symptom 三处列注释对齐到实际落库的形状。
-- 这一批 270 行的来源与判读都在装载器与探针的声明里：PDQ 185 行（209 条 <li> 归一掉 24 条
-- 跨组织学档的同症状复述，4 条散文句不落）、中文维基 67 行（32 行按逐条序号判非症状置 rejected）、
-- WHO 中文版 18 行。列定义逐字取自 db/schema.sql，改注释只改一处就会三份分叉。

ALTER TABLE `symptom` MODIFY COLUMN `source_id` int NOT NULL COMMENT 'PDQ（英文 185 行 18/18 病）、中文维基条目（67 行 5 病）、WHO 中文版（18 行 3 病）是三个源，逐行带 source_id 才谈得上分开报口径与商用限制。维基那一路挂在 wikidata 那条登记下：三条中文路同一支探针检测、同一份许可（CC BY-SA），只有条目章节这一条给得出症状行';

ALTER TABLE `symptom` MODIFY COLUMN `extract_kind` enum('list_item','sentence') NOT NULL DEFAULT 'list_item' COMMENT 'sentence＝页面没有清单、从散文按句切出来的，粒度与清单条目不同。装载的这一批恒为 list_item：PDQ 那 4 条句子实测全在乳腺栏目页（该页没有 <ul>），讲的是"症状因类型而异""早期往往没有症状"——关于症状的句子不是症状项，故不落';

ALTER TABLE `symptom` MODIFY COLUMN `review_status` enum('unreviewed','spot_checked','confirmed','rejected') NOT NULL DEFAULT 'unreviewed' COMMENT '逐条目测过的（源, 病）组合记 spot_checked：PDQ 5 病、维基 5 病、WHO 中文 3 病，其余如实记 unreviewed；维基那 67 条里 32 条按 wikidata.WIKI_DROP 的解析序号判非症状（分期定义、白血病亚型描述、并发症、名目下的释义段），置 rejected 而不是删掉';
