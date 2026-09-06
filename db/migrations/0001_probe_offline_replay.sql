-- 专项探针支持 --offline 离线重放之后，reachability 这一列缺一个状态。
--
-- 原来 record() 把 reachability 写死成 'direct'，离线重放也被记成"直连取到了"。
-- 这一列存在的全部意义就是让覆盖度报告能区分"源没了"和"本机网络到不了"，
-- 进而决定 scheduler 要不要配代理；混进一批根本没联网的行，这个判断就没有依据了。
-- 顺带把 diseases_covered 注释里的病种数改对：基准是 18 个恶性肿瘤（targets.py），不是 20。

ALTER TABLE `source_probe_log`
  MODIFY COLUMN `reachability` enum('direct','proxy','offline','blocked','error')
    NOT NULL DEFAULT 'error'
    COMMENT 'direct/proxy/offline/blocked/error 而非布尔：本机直连 Wikimedia 全线超时，无人值守的 scheduler 不能靠临时代理；offline 表示这一趟是用 data/raw 归档重放的，没联网，不能当成可达性证据',
  MODIFY COLUMN `diseases_covered` int DEFAULT NULL
    COMMENT '18 病基准里被覆盖到的个数，基准清单在 etl/onco_etl/targets.py';
