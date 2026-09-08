"""症状维装载器：PDQ 英文清单、中文维基条目、WHO 中文版三份清单写一张 `symptom`。

三源合在一台而不是拆三台，因为它们填的是同一张表的同一批列，而这一维唯一带判断的两步
（英文条目归一、维基条目剔非症状）都必须对着同一个唯一键 `uk_symptom` 算——拆三台只会把
`prov()` 与"哪一病目测过"的口径抄三遍。

五条口径：

1. **中文名列不是翻译列。** `symptom` 按 `(disease_id, source_id, name_lang, name)` 落，
   PDQ 行 `name_lang='en'`、维基与 WHO 行 `'zh'`，缺就空。这是 B6c 的裁定：Wikidata 子树词典
   贴 PDQ 原文表面能落 78.9%，但逐条目测 precision 只有 37.5%，错出来的中文在 UI 上看着
   完全正常（`weight loss → 减肥`、`jaundice → 弥散性血管内凝血`）——所以中文名只能来自
   **本来就是中文的那两份清单**，不能来自翻译。同一个病的英文 13 条与中文 4 条是两份不同来源
   的观察，压成同一行的 `name` + `name_zh` 会假装它们是同一份。
2. **繁简混排原样存。** 中文维基五病里有 `頭暈` 也有 `黄疸`（`languages=zh` 取回的就是混排），
   OpenCC 转换会把"源里写的字"换成"我们挑的字"，而 `idx_symptom_lookup` 的反查按字面串做。
   所以这一列存源里的写法，混排如实留着。
3. **只落 `<li>` 清单，散文句不落。** PDQ 有 4 条来自句子切分（实测全在 `types/breast/symptoms`
   那一页，该页没有 `<ul>`）：其中两条是"症状因乳腺癌类型而异"与"早期乳腺癌往往没有症状"——
   它们讲的是症状这件事本身，不是任何一个症状项；且四条的 anchor 都只有 `main-content`，
   前端点不出对应段落。所以 `extract_kind` 这一批恒为 `list_item`。
4. **维基那 67 条按逐条目测拆成"真症状 + 判非症状留痕"两半。** 规则解析只数 `<li>`/`<dd>`，
   分不清症状项与挂在同一名目下的分期定义（结直肠 17 条里 13 条是 0–IV 期与 A–D/B1–C2 定义）。
   剔哪几条写在 `wikidata.WIKI_DROP`，保留几条写在 `WIKI_EYEBALL`，装载时由 `wiki_drops()`
   核对两者是否自洽——源改版对不上就中止，不按老序号静默错标。被剔的行照样落库，
   只把 `review_status` 置 `rejected`：源确实给了这一条，库里该留着"人看过并判它不是症状"。
5. **WHO 这一源只取中文版。** 它的英文版与中文版是同一份 sheet 的同一串清单（实测肺 7 对 7、
   结直肠 6 对 6），而英文侧 PDQ 已经 18/18 且分组织学档，WHO 英文只覆盖 4 病；同时乳腺癌
   那份 sheet 的英文版压根没有 Symptoms 节（症状清单在中文版才有"症状"标题）。两边都落，
   同一源在同一病上就会出现两份条数还不对齐的英文清单，而这一源的价值全在补中文缺口。

`freq_band` 与 `provenance` 两列全空：PDQ 症状小节里百分号出现数实测 0（整页那 1,857 个
是生存率与缓解率），全站只有 Orphanet 有六档频率而它对常见上皮癌 0 命中；`provenance`
是 L3 兜底抽取的标记位，这一批没有一行用到模型。
"""
from __future__ import annotations

import re

from ..probes import nci_pdq_html, who_factsheet, wikidata
from ..targets import TARGETS
from .base import Ctx, LoadResult, prov, replace_scope

PDQ = nci_pdq_html.SOURCE
PDQ_DATASET = nci_pdq_html.DATASET
WHO = who_factsheet.SOURCE
WHO_DATASET = who_factsheet.DATASET
WIKI = wikidata.SOURCE
# 中文维基那一路挂在 wikidata 这条源登记下：B7a 把三条路（子树词典 / 逐条查 / 条目章节）
# 落成同一支探针，只有第三条给得出症状行。要分开报口径得先在 source 表添一行，
# 而这三条路的许可与归属本来就是同一份（Wikimedia CC BY-SA）。

# 归一时剥掉的尾部标点：PDQ 的清单条目有的带句号有的不带，同一症状就会以两种写法各落一行
TRAILING = re.compile(r"[\s.,;:!?'\"]+$")


def _norm(s: str) -> str:
    """归一键：折叠空白、去尾部标点、转小写。

    刻意与 `uk_symptom` 的排序规则 `utf8mb4_0900_ai_ci` 同口径（大小写不敏感）。归一若比
    唯一键细，`Fatigue` 与 `fatigue` 就会被当成两条传进去，MySQL 把第二条更新成第一条，
    而 `written` 照实报 2 行——装载器报的行数与库里的行数就分了叉。
    """
    return TRAILING.sub("", re.sub(r"\s+", " ", s).strip()).lower()


def _row(*, disease_id: int, source_id: int, release_id: int | None, name: str, lang: str,
         heading: str, url: str, anchor: str, lastmod: str, review: str) -> dict:
    return {
        "disease_id": disease_id,
        "name": name,
        "name_lang": lang,
        "heading": heading,
        "source_url": url,
        "anchor": anchor,
        "extract_kind": "list_item",
        "page_lastmod": lastmod or None,
        "freq_band": None,
        "provenance": "",
        **prov(source_id=source_id, dataset_release_id=release_id,
               extract_method="l2_rule", review_status=review),
    }


def pdq_rows(pl: nci_pdq_html.PdqPayload, sid: int, rid: int, ids: dict[str, int]) -> tuple[list[dict], int, int]:
    """PDQ 英文清单。返回 (行, 归一去重掉的条数, 散文句条数)。

    同一症状在两个组织学文档里各出现一次（实测肺的 NSCLC 与 SCLC 各 11 条、白血病四页 28 条），
    那是源的分档形状，不是两条观察：按 `targets.pdq_pages` 的**声明顺序**留第一个，
    于是 `source_url` 与 `anchor` 指向的也是那一份。
    """
    rows, collapsed, sentences = [], 0, 0
    for t in TARGETS:
        by_path = {r["path"]: r for r in pl.pages.get(t.code, [])}
        seen: set[str] = set()
        for path in t.pdq_pages:
            p = by_path.get(path)
            if not p:
                continue
            if p["mode"] != "list":
                sentences += len(p["items"])
                continue
            for it in p["items"]:
                key = _norm(it["text"])
                if key in seen:
                    collapsed += 1
                    continue
                seen.add(key)
                rows.append(_row(
                    disease_id=ids[t.code], source_id=sid, release_id=rid, name=it["text"],
                    lang="en", heading=it["heading"], url=nci_pdq_html.BASE + p["path"],
                    anchor=it["anchor"], lastmod=p["lastmod"] or p["updated"],
                    review="spot_checked" if t.code in nci_pdq_html.EYEBALL else "unreviewed"))
    return rows, collapsed, sentences


def wiki_rows(wiki: dict[str, dict], sid: int, ids: dict[str, int]) -> list[dict]:
    """中文维基路线③。五个病的条目挂 `dataset_release_id=NULL`——这一支没有上游版本戳。"""
    rows = []
    for code in wikidata.WIKI_TITLE:
        rec = wiki.get(code) or {}
        items = rec.get("items") or []
        if not items:
            continue
        drops = wikidata.wiki_drops(code, len(items))
        url = wikidata.wiki_url(rec["title"])
        # 维基的段锚点就是段标题（`/wiki/大腸癌#症狀及診斷`），所以 heading 与 anchor 同值
        for i, text in enumerate(items, 1):
            rows.append(_row(
                disease_id=ids[code], source_id=sid, release_id=None, name=text, lang="zh",
                heading=rec["sec"], url=url, anchor=rec["sec"], lastmod="",
                review="rejected" if i in drops else "spot_checked"))
    return rows


def who_rows(pl: who_factsheet.WhoPayload, per: dict[str, list[str]],
             sid: int, rid: int, ids: dict[str, int]) -> list[dict]:
    """WHO 中文版症状清单。认领关系直接用它 `match_sheets` 那张表，装载器不按 slug 猜。"""
    zh = {}
    for slug, recs in pl.pages.items():
        rec = next((r for r in recs if r["lang"] == "zh"), None)
        if rec:
            zh[slug] = rec
    rows: list[dict] = []
    counts: dict[str, int] = {}
    for code in sorted(per):
        for slug in per[code]:
            rec = zh.get(slug)
            if not rec or not rec["symptom_items"]:
                continue
            head = who_factsheet.sym_heading(rec)
            for text in rec["symptom_items"]:
                counts[code] = counts.get(code, 0) + 1
                rows.append(_row(
                    disease_id=ids[code], source_id=sid, release_id=rid, name=text, lang="zh",
                    heading=head, url=rec["url"], anchor="",
                    lastmod=rec["date_modified"] or rec["date"],
                    review="spot_checked" if code in who_factsheet.EYEBALL else "unreviewed"))
    # 目测过的那三病，抽取数与判真数都该等于 EYEBALL 写的两个数。中文版没有"剔几条"这一步，
    # 两个数本就相等；不等就是清单变了形状，那份逐条读过的结论不再成立
    for code, (real, extracted) in who_factsheet.EYEBALL.items():
        got = counts.get(code, 0)
        if got != extracted or real != extracted:
            raise SystemExit(
                f"WHO {code} 中文症状清单漂移：取回 {got} 条，"
                f"EYEBALL 声明的是 {real}/{extracted}——重读该页再改声明")
    return rows


def load(ctx: Ctx) -> LoadResult:
    pp = nci_pdq_html.load_payload(ctx.offline)
    wp = who_factsheet.load_payload(ctx.offline)
    wiki = wikidata.load_wiki_list(ctx.offline)
    per, _generic = who_factsheet.match_sheets(wp.titles)

    with ctx.tx() as conn:
        sid_pdq, sid_who, sid_wiki = (ctx.source_id(PDQ), ctx.source_id(WHO), ctx.source_id(WIKI))
        # 复用探针登记的那一版。这一台的解析结果不落自己的发布：三源都是重放探针的归档，
        # 装载器再登记一版只会造出同一份字节的第二个版本号
        rid_pdq = ctx.latest_release(conn, PDQ, PDQ_DATASET)
        rid_who = ctx.latest_release(conn, WHO, WHO_DATASET)
        ids = ctx.disease_ids(conn)
        prows, collapsed, sentences = pdq_rows(pp, sid_pdq, rid_pdq, ids)
        wrows = wiki_rows(wiki, sid_wiki, ids)
        hrows = who_rows(wp, per, sid_who, rid_who, ids)
        # 逐源先删后写：这三段各自完全由本装载器负责，源里删掉的一条不该留在库里
        n_pdq = replace_scope(conn, "symptom", {"source_id": sid_pdq}, prows)
        n_wiki = replace_scope(conn, "symptom", {"source_id": sid_wiki}, wrows)
        n_who = replace_scope(conn, "symptom", {"source_id": sid_who}, hrows)

    rejected = sum(1 for r in wrows if r["review_status"] == "rejected")
    kept = prows + [r for r in wrows if r["review_status"] != "rejected"] + hrows
    covered = len({r["disease_id"] for r in kept})
    ctx.job.set(written=n_pdq + n_wiki + n_who)
    msg = (
        f"PDQ 英文 {n_pdq}（归一吃掉 {collapsed} 条同症状跨组织学档的复述，"
        f"散文句 {sentences} 条不落）/ 中文维基 {n_wiki}（其中判非症状留痕 {rejected} 条："
        f"分期定义、亚型描述、并发症与释义段）/ WHO 中文 {n_who}（仅 3 病有症状节）。"
        f"合计 {n_pdq + n_wiki + n_who} 行、{covered}/18 病有症状行。"
        "中文侧只有 7/18 病有现成中文清单（维基 5 病 ∪ WHO 中文 3 病，结直肠两边都有），"
        "另 11 病的中文名留空——"
        "B6c 实测三条翻译路都不过关，故不做翻译列；freq_band 与 provenance 全空。"
        f"三份归档 {pp.version} / {wp.version} / 维基条目当日解析，"
        "维基那一支不登记 dataset_release（Wikidata 天天在改但那不叫一次发布）。"
    )
    return LoadResult(
        written={"symptom": n_pdq + n_wiki + n_who},
        covered=covered,
        total=len(TARGETS),
        message=msg,
    )
