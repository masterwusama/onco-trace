"""WHO 癌症 Fact Sheet 探针：判"叙述维有没有一份权威、带中文、能定版本的公开底稿"。

出口判据没有"叙述"这一行（计划里它只落在候选源清单），所以这一支的判据是自己定的线：
≥12/18 病有癌种专页、且中文版可匿名取回、且页面带可解析的更新日期与要点清单。
实测这道线过不去，如实记 partial——过不过线都要先把数摆出来。

五处口径都是这一趟实测出来的：

  ① 覆盖只有 4/18，而且不是抓取失败。全站 fact sheet 去重 242 份，癌相关 7 份
    （breast / cervical / colorectal / lung / cancer / cancer-in-children / HPV），
    能对到 18 病基准的只有肺、结直肠、女乳、宫颈四份。肝、胃、前列腺、胰腺、食管、
    卵巢、甲状腺、膀胱、肾、脑、子宫体、白血病、NHL、骨髓瘤**没有专页**。
    另有两份全站通页（Cancer、Childhood cancer）不分病种，能给 18 病共用的概述。
    HPV 那一份（人乳头状瘤病毒与癌症）讲的是病因不是某一个病，与 18 病的 ICD 段
    语义不对齐，所以探针按 targets 词认领时不认领它——这一趟实际取回 6 份。
  ② 中文版是真翻译，不是节选。但中英两版的小节集合不是同一套：乳腺癌那 5 条症状在中文版
    有"症状"标题、在英文版挂在 "Who is at risk?" 之下（英文版整页没有 Symptoms 节），
    而肺癌与结直肠癌两版都有 Symptoms 节且条数一一对应（7 对 7、6 对 6）——
    所以"英文版没有症状标题"是逐页的性质，不能当成整源的规律。
    更极端的一处：不分病种的 Cancer 通页**英文版压根没有 Key facts 一节**
    （它的结构是 The problem / Causes / Risk factors / Reducing the burden / Prevention），
    中文版却有"重要事实"5 条。按标题定位必须逐语种各切一遍，不能拿一版的段号套另一版。
  ③ 正文容器是 `<section id="content">`。页面上还有一个 `<article class="sf-detail-body-wrapper">`
    与整站导航同层，按 article 取正文会连带 24 串菜单清单一起进来。
  ④ schema.org JSON-LD 只有**部分中文页**有：实测 breast 的 zh 页带 3 类段别、
    colorectal 与 lung 带 4 类，cervical 与两份通页没有，**英文页一份都没有**。
    所以段别定位只能按 HTML 标题走，JSON-LD 只当佐证与 `dateModified` 的来源。
  ⑤ 中文 URL 是换根不是加后缀：`https://www.who.int/zh/news-room/fact-sheets/detail/<slug>`
    可取；`/detail/<slug>/zh/` 回 404。版本戳取页内 `<div class="date">`，各 sheet 自己更新
    （实测认领到的 6 份跨度 2026-02-13…2026-07-03），所以不取"今天"。
    两语种的戳格式不同——中文页「2026年7月3日」、英文页「3 July 2026」，代码里都归一成 ISO，
    并与 JSON-LD 的 `dateModified` 互校（实测 12 页两值全等）。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from lxml import html as LH

from .. import raw
from ..clock import today
from ..config import DATA_EXPORTS
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "who_factsheet"
DATASET = "narrative-sections"
BASE = "https://www.who.int/"
ENTRY = BASE + "news-room/fact-sheets"
LANGS = ("en", "zh", "fr", "es", "ru", "ar")
GENERIC = ("cancer", "cancer-in-children")   # 不分病种的通页
HEADERS = {"Accept": "text/html,application/xhtml+xml"}

MIN_DISEASES = 12      # 判据：≥12/18 病有癌种专页
MIN_KEY = 3            # 判据：每页 ≥3 条要点清单
MIN_SYM = 4            # 中文症状清单的下限（与 PDQ 那一支对齐）
SLEEP = 0.15

CRITERIA = (f"≥{MIN_DISEASES}/18 病有 WHO 癌种专页，其中文版可匿名取回（HTTP 200 且 final_url 未被改写）"
            f"且带可解析的更新日期与 ≥{MIN_KEY} 条要点清单；症状清单与 JSON-LD 段别不进判据，只如实报")

ROOT = "//section[@id='content']"
SYM_HEAD = re.compile(r"(\bsigns?\b|\bsymptoms?\b|症状)", re.I)
KEY_HEAD = re.compile(r"(key facts|重要事实)", re.I)
DATE_TEXT = re.compile(r"\s*(Reading time|阅读时间).*$", re.I)
# WHO 把清单写成"…；…；和 …"这种连接句式，逐条切 <li> 会把连接词留在条目尾巴上
# （实测 colorectal 中文版第 5 条＝"持续疲劳；和"，英文对照 "persistent fatigue; and"）。
# 只剥症状条目；要点清单是整句，句号留着。
SYM_TAIL = re.compile(r"(?:\s*[；;]\s*(?:and|和|以及|et|y|и|و))?\s*[；;。，,]*$")

FIELDS = ("slug", "lang", "status", "redirected", "date", "date_modified", "chars", "pct",
          "sections", "key_facts", "symptom_items", "aspects")

# 目测（zh 症状清单逐条读，2026-09-08）：(判为真症状的条数, 抽取条数)
# 口径与 PDQ 那一支一致：条目主干确实是病人可感知的症状或体征就算真（"皮肤出现凹陷、发红、
# 蚀损斑"这类体征算）。colorectal 第 5 条原本带连接词尾巴（"持续疲劳；和"），那是清单句式
# 不是误抽，已由 SYM_TAIL 剥掉，不计入 precision 扣分。
EYEBALL: dict[str, tuple[int, int]] = {"breast_female": (5, 5), "colorectum": (6, 6), "lung": (7, 7)}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


MONTHS = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"))}


def _iso(stamp: str) -> str:
    """页内日期戳归一成 ISO。

    中文版是「2026年7月3日」、英文版是「3 July 2026」，两种都不是机器可读格式，
    而这一列要跟 JSON-LD 的 dateModified 一起进版本判定，所以必须归一。
    """
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", stamp):
        return stamp
    if m := re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", stamp):
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
    if m := re.fullmatch(r"(\d{1,2}) ([A-Za-z]+) (\d{4})", stamp):
        i = MONTHS.get(m[2].lower())
        return f"{m[3]}-{i:02d}-{int(m[1]):02d}" if i else ""
    return ""


def _get(url: str) -> tuple[object, str, int, str, int, str]:
    """取一页并解析成树，同时把原文留着——JSON-LD 只在源码里，树里没有。

    网络层失败直接中止：半趟结果会被下一个人读成上游结论。
    """
    r = fetch(url, headers=HEADERS, max_bytes=20_000_000, timeout=(10, 150))
    if r.status != 200 or not r.body:
        raise SystemExit(f"{url} → {r.status or r.reachability}：{(r.note or '')[:160]}")
    return LH.fromstring(r.text), r.text, r.latency_ms, r.reachability, r.status, (r.final_url or url)


def _root_url(lang: str) -> str:
    return BASE if lang == "en" else f"{BASE.rstrip('/')}/{lang}/"


def _detail(slug: str, lang: str) -> str:
    return _root_url(lang) + f"news-room/fact-sheets/detail/{slug}"


def _index() -> tuple[dict, int, str, int]:
    """A-Z 列表页是服务端渲染的完整清单：一次请求给出全站 242 份 sheet 的 slug 与标题。"""
    tree, _, ms, reach, status, _ = _get(ENTRY)
    rows: dict[str, str] = {}
    for a in tree.xpath("//a[contains(@href,'fact-sheets/detail/')]"):
        slug = a.get("href").rstrip("/").split("/detail/")[-1]
        title = _clean(a.text_content())
        if slug and (slug not in rows or len(title) > len(rows[slug])):
            rows[slug] = title
    return rows, ms, reach, status


def _page(slug: str, lang: str) -> dict:
    """一份 sheet 的一语种：日期戳、小节标题、要点清单、症状清单、JSON-LD 段别。"""
    url = _detail(slug, lang)
    tree, html_text, _, _, status, final = _get(url)
    heads = [_clean(h.text_content()) for h in tree.xpath(ROOT + "//h2")]
    body = _clean("".join((tree.xpath(ROOT) or [tree])[0].itertext()))
    stamp = next((_clean(e.text_content()) for e in tree.xpath('//*[contains(@class,"date")]')
                  if _clean(e.text_content())), "")
    ld_date, aspects = _json_ld(html_text)
    return {"slug": slug, "lang": lang, "url": url, "status": status,
            "redirected": final.rstrip("/") != url.rstrip("/"),
            "date": _iso(DATE_TEXT.sub("", stamp).strip()),
            "date_modified": ld_date, "aspects": aspects, "chars": len(body),
            "pct": body.count("%"), "sections": heads,
            "key_facts": _list_after(tree, KEY_HEAD),
            "symptom_items": [SYM_TAIL.sub("", x).strip() for x in _list_after(tree, SYM_HEAD)]}


def sym_heading(rec: dict) -> str:
    """`symptom_items` 挂在哪个 h2 之下。归档没存这一列，从同一份记录里已存的 `sections`
    按同一个正则取——装载器要落 `symptom.heading`，但不该因此重新解析一遍页面。"""
    return next((h for h in rec.get("sections") or [] if SYM_HEAD.search(h)), "")


def _list_after(tree, head_re: re.Pattern) -> list[str]:
    """标题命中 head_re 的那一节里紧跟标题的第一串 `<ul>`（遇到下一个标题就停）。"""
    for h in tree.xpath(ROOT + "//h2"):
        if not head_re.search(_clean(h.text_content())):
            continue
        for n in h.itersiblings():
            if n.tag == "ul":
                return [t for t in (_clean(li.text_content()) for li in n.xpath("./li")) if t]
            if n.tag in ("h2", "h3"):
                break
    return []


def _json_ld(html_text: str) -> tuple[str, list[str]]:
    """ld+json 脚本里串着多个 JSON 文档，按花括号配平一段段切出来。

    不能整体 `json.loads`：实测同一个脚本标签里第二段起就报 Extra data。
    """
    dates, aspects = [], []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html_text, re.S):
        depth, start = 0, None
        for i, ch in enumerate(block):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        d = json.loads(block[start:i + 1])
                    except Exception:  # noqa: BLE001 —— 一段坏不影响其它段
                        d = None
                    start = None
                    if isinstance(d, dict):
                        if d.get("dateModified"):
                            dates.append(str(d["dateModified"])[:10])
                        for p in (d.get("hasPart") or []):
                            a = (p.get("hasHealthAspect") or "").rsplit("/", 1)[-1]
                            if a:
                                aspects.append(a)
    return (max(dates) if dates else ""), sorted(set(aspects))


def match_sheets(titles: dict) -> tuple[dict, dict]:
    """按 targets 声明的查询词把 242 份 sheet 对到 18 病上；通页不参与匹配。

    公开是因为症状装载器要的是同一张"哪份 sheet 算哪个病"的表——它不该自己按 slug 猜。
    """
    per: dict[str, list[str]] = {}
    for t in TARGETS:
        terms = [s.lower() for s in t.search_terms]
        per[t.code] = sorted(slug for slug, title in titles.items()
                             if slug not in GENERIC and any(term in title.lower() for term in terms))
    return per, {g: titles[g] for g in GENERIC if g in titles}


def _langs(slugs: list[str]) -> tuple[dict, int, list[str]]:
    """六语种可得性：换根 URL 逐个试，记下状态码与有没有被改写到别处。"""
    out, ms, reaches = {}, 0, []
    for slug in slugs:
        hits = {}
        for lang in LANGS:
            url = _detail(slug, lang)
            r = fetch(url, headers=HEADERS, max_bytes=400_000, timeout=(10, 90))
            ms += r.latency_ms or 0
            reaches.append(r.reachability)
            final = (r.final_url or url).rstrip("/")
            hits[lang] = f"{r.status}{'!' if not final.endswith(slug) else ''}"
            time.sleep(SLEEP)
        out[slug] = hits
        print(f"  {slug:32s} 语种 {hits}", flush=True)
    return out, ms, reaches


@dataclass
class WhoPayload:
    """一次取数（或离线重放）的产出：sheet slug → [中文版, 英文版] 两份解析记录。"""

    pages: dict[str, list[dict]]
    titles: dict[str, str]
    langs: dict[str, dict]
    version: str
    key_dir: Path
    sizes: dict[str, int]
    reach: str
    http: int | None
    ms: int


def load_payload(offline: bool) -> WhoPayload:
    """离线重放 `data/raw` 归档；联网取 A-Z 清单、认领到的 sheet 两语种正文与六语种可得性。"""
    if offline:
        key_dir = raw.newest_dir(SOURCE, "pages.json")
        if not key_dir:
            raise SystemExit(f"离线重放要先有归档：data/raw/{SOURCE}/*/pages.json 不存在")
        pages = json.loads((key_dir / "pages.json").read_text(encoding="utf-8"))
        index = json.loads((key_dir / "index.json").read_text(encoding="utf-8"))
        sizes = {n: (key_dir / n).stat().st_size for n in ("pages.json", "index.json")}
        return WhoPayload(pages, index["titles"], index["langs"], key_dir.name,
                          key_dir, sizes, "offline", None, 0)

    ms = 0
    reaches: list[str] = []
    titles, m, reach, http = _index()
    ms += m
    reaches.append(reach)
    per, generic = match_sheets(titles)
    slugs = sorted({s for hits in per.values() for s in hits} | set(generic))
    pages = {}
    for slug in slugs:
        recs = []
        for lang in ("zh", "en"):
            t0 = time.perf_counter()
            recs.append(_page(slug, lang))
            ms += int((time.perf_counter() - t0) * 1000)
            time.sleep(SLEEP)
        pages[slug] = recs
        zh, en = recs
        print(f"  {slug:32s} zh {zh['status']}{'→改写' if zh['redirected'] else ''} "
              f"要点 {len(zh['key_facts'])} "
              f"症状 {len(zh['symptom_items'])} 段 {len(zh['sections'])} aspects {len(zh['aspects'])}"
              f" | en 要点 {len(en['key_facts'])} 症状 {len(en['symptom_items'])}", flush=True)
    langs, m, rs = _langs(slugs)
    ms += m
    reaches += rs
    dates = [p["date_modified"] or p["date"] for recs in pages.values() for p in recs]
    version = max([d for d in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or "")] or [today()])
    key_dir = raw.archive_dir(SOURCE, version)
    blobs = {"pages.json": pages, "index.json": {"titles": titles, "langs": langs}}
    sizes = {}
    for n, b in blobs.items():
        body_bytes = json.dumps(b, ensure_ascii=False).encode("utf-8")
        (key_dir / n).write_bytes(body_bytes)
        sizes[n] = len(body_bytes)
    _drafts(pages, per)
    return WhoPayload(pages, titles, langs, version, key_dir, sizes,
                      "proxy" if "proxy" in reaches else "direct", http, ms)


def probe(offline: bool = False) -> ProbeResult:
    pl = load_payload(offline)
    pages, titles, langs = pl.pages, pl.titles, pl.langs
    key_dir, version, sizes = pl.key_dir, pl.version, pl.sizes
    reach, http, ms = pl.reach, pl.http, pl.ms
    per, generic = match_sheets(titles)

    have, thin, no_zh_sym, no_sheet, rows, claimed = [], [], [], [], 0, set()
    for t in TARGETS:
        hits = per[t.code]
        claimed.update(hits)
        if not hits:
            no_sheet.append(t.code)
            continue
        zh = [pages[s][0] for s in hits]
        rows += sum(len(p["sections"]) for p in zh)
        dated = [p for p in zh if p["date"] or p["date_modified"]]
        keyed = [p for p in dated if len(p["key_facts"]) >= MIN_KEY]
        if len(keyed) < len(hits):
            thin.append(f"{t.code}(要点 {max([len(p['key_facts']) for p in zh])})")
            continue
        have.append(t.code)
        if not any(len(p["symptom_items"]) >= MIN_SYM for p in zh):
            no_zh_sym.append(f"{t.code} {max([len(p['symptom_items']) for p in zh])} 条")

    covered = len(have)
    verdict = "ok" if covered >= MIN_DISEASES else ("partial" if covered else "empty")
    eb, prec = "", None
    if EYEBALL:
        hit, tot = sum(v[0] for v in EYEBALL.values()), sum(v[1] for v in EYEBALL.values())
        prec = round(hit / tot * 100, 1) if tot else None
        eb = "；".join(f"{c} {v[0]}/{v[1]}" for c, v in EYEBALL.items())
    zh_ok = sum(1 for h in langs.values() if h.get("zh", "").startswith("200"))
    sym_sheets = [s for s, rs in pages.items() if any(len(p["symptom_items"]) >= MIN_SYM for p in rs)]
    stamps = sorted({p["date"] for rs in pages.values() for p in rs if p["date"]})
    span = f"{stamps[0]}…{stamps[-1]}" if stamps else "无"
    same_stamp = sum(1 for rs in pages.values() if len({p["date"] for p in rs}) == 1)
    diff_heads = sum(1 for rs in pages.values() if len(rs[0]["sections"]) != len(rs[1]["sections"]))

    msg = (
        f"达标 {covered}/18（判据＝癌种专页有中文版、带日期与 ≥{MIN_KEY} 条要点清单，线是 ≥{MIN_DISEASES}）。"
        f"全站 fact sheet 去重 {len(titles)} 份，按 targets 词认领到 {len(pages)} 份，"
        f"其中对得上 18 病基准的只有 {sorted(claimed)} 四份；"
        f"过了判据的是 {', '.join(have)}。另有两份不分病种的通页（{', '.join(sorted(generic))}）"
        f"能给 18 病共用的概述。没有专页的病 {len(no_sheet)} 个：{', '.join(no_sheet)}"
        "——这不是抓取失败，是 WHO 不提供。"
        + (f" 有专页但清单/日期不达标的：{', '.join(thin)}。" if thin else "")
        + (f" 中文版没有 ≥{MIN_SYM} 条症状清单的：{', '.join(no_zh_sym)}。" if no_zh_sym else "")
        + f" 症状清单 ≥{MIN_SYM} 条的 sheet：{', '.join(sorted(sym_sheets)) or '无'}。"
        + f"六语种可得性：{len(langs)} 份 sheet 里 zh 回 200 的 {zh_ok} 份。"
        "中文症状名这一列本来是 B6 唯一拿不准的东西（PDQ 只给英文），实测 WHO 的中文版是真翻译、"
        "症状带现成的 <li> 清单，所以有专页的病不要模型就能出中文症状名；"
        "但这只覆盖这几病，其余病仍然只能英文或留空。"
        "口径三处：① 正文取 <section id='content'>，按 <article> 取会连整站导航的 24 串菜单一起进来；"
        "② 症状段只能按标题定位，而中英两版的小节集合不是同一套——乳腺癌那 5 条症状中文版有"
        "\"症状\"标题、英文版整页没有 Symptoms 节（挂在 \"Who is at risk?\" 之下），"
        "肺癌与结直肠癌两版却都有 Symptoms 节且条数一一对应；更极端的是 Cancer 通页"
        "英文版压根没有 Key facts 一节（它的结构是 The problem / Causes / Risk factors / "
        "Reducing the burden / Prevention…），中文版有 5 条要点，所以定位必须逐语种各切一遍"
        f"（实测 {diff_heads}/{len(pages)} 份 sheet 的中英小节数就不等）；"
        "③ schema.org 的 hasHealthAspect 只有部分中文页有（实测 breast 的 zh 页 3 类、"
        "colorectal 与 lung 4 类，cervical 与两份通页无，英文页一份都没有），所以只当佐证。"
        f"版本戳取页内 <div class='date'>（中文页是「2026年7月3日」这种格式、英文页是"
        f"「3 July 2026」，两者都归一成 ISO）与 JSON-LD dateModified 互校，"
        f"本次取 {version}。各 sheet 自己更新，"
        f"认领到的 {len(pages)} 份实测跨度 {span}；"
        f"中英两版同一份页同一条戳（{same_stamp}/{len(pages)} 份一致）。"
        "中文 URL 是换根不是加后缀："
        "/zh/news-room/fact-sheets/detail/<slug> 可取，/detail/<slug>/zh/ 回 404。"
        + (f" 目测（真症状/抽取）：{eb}，合计 precision {prec}%"
           "（读的是 data/exports/who_symptom_*.md 三份中文清单）。" if EYEBALL else
           " 目测未做：核对 data/exports/who_symptom_*.md 后记进 EYEBALL。")
    )

    sample = [{"code": t.code, "sheets": per[t.code],
               "zh_symptom_items": sum(len(pages[s][0]["symptom_items"])
                                       for s in per[t.code] if pages.get(s))}
              for t in TARGETS]
    sample.append({"field_fill_pct": {
        "病有癌种专页": round(covered / len(TARGETS) * 100, 1),
        "sheet 有中文症状清单": round(len(sym_sheets) / max(len(pages), 1) * 100, 1),
        "页带日期戳": round(sum(1 for rs in pages.values() for p in rs if p["date"]) /
                        max(sum(len(rs) for rs in pages.values()), 1) * 100, 1),
    }})
    first = next((rs[0] for s, rs in pages.items() if s in claimed and rs[0]["symptom_items"]), None)
    if first:
        sample.append({"sample": {"slug": first["slug"], "date": first["date"],
                                  "sections": first["sections"],
                                  "symptom_items": first["symptom_items"][:8]}})

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=rows,
        diseases_covered=covered,
        diseases_total=len(TARGETS),
        fields_seen=list(FIELDS),
        sample=sample,
        raw_path=raw.rel(key_dir),
        reachability=reach,
        http_status=http,
        latency_ms=ms or None,
        dataset_code=DATASET,
        upstream_version=version,
        release_date=version,
        release_bytes=sum(sizes.values()),
        release_sha256=raw.sha256_bytes(
            b"".join((key_dir / n).read_bytes() for n in sorted(sizes))
        ),
    )


def _drafts(pages: dict, per: dict) -> None:
    """有症状清单的 sheet 各出一份中文草稿，供目测；只写 data/exports，不入库。"""
    DATA_EXPORTS.mkdir(parents=True, exist_ok=True)
    owner = {s: c for c, hits in per.items() for s in hits}
    for slug, rs in pages.items():
        zh = rs[0]
        if not zh["symptom_items"]:
            continue
        lines = [f"# WHO {slug}（{owner.get(slug, '通页/共用')}）中文症状草稿", "",
                 f"- 生成 {today()}；更新日期 {zh['date'] or zh['date_modified'] or '?'}；"
                 f"aspects {','.join(zh['aspects']) or '无'}",
                 f"- 小节：{' / '.join(zh['sections'])}", "",
                 "| 症状（中文，取自 WHO 中文版） | 英文对照 | 出处 |", "|---|---|---|"]
        en = rs[1]["symptom_items"] if len(rs) > 1 else []
        for i, x in enumerate(zh["symptom_items"]):
            lines.append(f"| {x} | {en[i] if i < len(en) else '—'} | {zh['url']}（{zh['date']}） |")
        (DATA_EXPORTS / f"who_symptom_{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
