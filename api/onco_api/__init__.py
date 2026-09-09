"""onco-trace 服务层：只读 FastAPI，把 db/schema.sql 里的行按页面需要的形状吐出去。

分工写在 README 约定 1：这一层与 etl/ 互不引用，只通过 MySQL 表结构对话。
所以这里没有一条会改数的 SQL，也没有一处 import onco_etl。
"""
