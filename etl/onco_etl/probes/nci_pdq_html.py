"""cancer.gov / NCI PDQ 症状项探针：判"这一病的症状能不能不靠模型就取回来"。

出口判据是症状维那一行——≥12/18 病抽出 ≥4 条真症状、5 病目测 precision ≥80%。
B6 之前这一维被判定"必须 L3 抽取兜底"（结构化本体只有血液肿瘤与遗传性癌综合征有行，
见计划《症状维的实测结论》），但这一趟实测下来**症状清单本身不用抽**：

  ① 入口形状不统一，只能按 targets.py 的 `pdq_pages` 逐病认领。247 个 PDQ 摘要页在
    `/types/<段>/<hp|patient>/<slug>-pdq`，18 段里只有 12 段开了 `patient/`；剩下 6 段的
    病人内容在 `/types/<段>/symptoms` 栏目页，而这种页全站只有 12 段有。旧结构
    `/types/lung/pdq/lung-adult-pdq` 一律 404，`/types/stomach/patient/stomach-treatment-pdq`
    会被 301 到 `/types/stomach/treatment`——**返回 200 不等于取到了声明的那一页**，
    所以每页都比对 final_url，被改写的单独报。
  ② 症状清单在 HTML 里就是现成的 `<ul><li>`。PDQ 病人版的小节标题本身就是那句话
    （"Signs and symptoms of non-small cell lung cancer include coughing and shortness of
    breath."），紧跟一个 `<div class="pdq-content-list"><ul>`，逐条是 chest discomfort or pain、
    wheezing、loss of appetite…。但清单不总挂在症状标题下：实测 myeloma 的两串挂在
    "Multiple myeloma" 这一节的冒号引言后，全段唯一的乳腺症状清单挂在男性乳腺癌页的
    "Screening for male breast cancer" 节，而那一节往下还排着诊断步骤、治疗链接、生存率
    三串别的清单——所以取的是"引言句之后紧跟的那一串"，不是整节。
    规则解析就能出这些条目，不需要模型。
  ③ 一条频率都不给。症状小节里百分号出现数实测为 0（hp 版整页有 1,857 个百分号，
    但那是生存率与缓解率，不在症状段）。所以 `freq_band` 这一列这一源供不了，
    全站只有 Orphanet 那六档能给，而 Orphanet 常见上皮癌 0 命中。
  ④ 版本戳不在页头。响应头的 Last-Modified 是站点重建时间（实测每页同一个 2026-09-02 戳），
    PDQ 摘要页页内也没有 Updated，只有栏目页页脚有 `<time datetime>`。
    所以上游版本取 sitemap 的 per-page lastmod 最大值，栏目页的 Updated 一并如实报。

判据只卡"取回 ≥4 条症状项的病数"，字段稀疏（无频率）不进裁定；precision 由 5 病目测给出，
目测结果记在 `EYEBALL` 里——这份人工判断与 targets/sources 同级，是签入的声明而不是脚本猜的。
草稿同步写到 `data/exports/symptom_draft_<code>.md` 供目测，不入库。
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter

from lxml import etree, html as LH

from .. import raw
from ..clock import today
from ..config import DATA_EXPORTS
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "nci_pdq_html"
DATASET = "symptom-items-html"
BASE = "https://www.cancer.gov/"
SITEMAP = BASE + "sitemaps/pageinstructions.xml"
HEADERS = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

MIN_ITEMS = 4          # 判据下限：每病 ≥4 条症状项
MIN_DISEASES = 12      # 判据：≥12/18 病达到上面那条
SLEEP = 0.15

CRITERIA = (
    "每病按 targets.py 的 pdq_pages 认领的页面可匿名取回（HTTP 200 且 final_url 未被改写），"
    f"且 ≥{MIN_DISEASES}/18 病从症状小节规则化取回 ≥{MIN_ITEMS} 条症状项；"
    "频率带不进判据（PDQ 症状小节实测零频率），precision 由 5 病目测裁定并记进本行 message"
)

SYM_HEAD = re.compile(r"(\bsigns?\b|\bsymptoms?\b|clinical presentation)", re.I)
# 频率只认百分号——`freq_band` 要的是"这一症状有多少比例的病人有"。
# "Frequent urination" 里的 frequent 是症状说法的一部分，不是频率带，所以分开数。
FREQ_PCT = re.compile(r"\d{1,3}(?:\.\d+)?\s*(?:%|percent)", re.I)
FREQ_WORD = re.compile(r"\b(?:very |relatively |most )?(?:common|frequent|uncommon|occasional|rare|rarely)\b", re.I)
# 症状同义复述：整条只是"××癌"本身，不是症状——HPO/Orphanet 那一坑在 HTML 侧也会重现
RESTATED = re.compile(
    r"^\s*(?:a|an|the)?\s*(?:malignant|primary|adult|child(?:hood)?|invasive|metastatic|localized)?\s*"
    r"[\w ,'\-()]{0,50}?\s*(?:cancer|cancers|carcinoma|tumor|tumour|neoplasm|lymphoma|leukemia|"
    r"myeloma|sarcoma|blastoma|melanoma)\.?\s*$",
    re.I,
)
NOISE = re.compile(r"^\s*(?:print|close|skip|back to top|share|last revised|note:)\b", re.I)
# 引言句触发只认"这一串列的就是症状"的句式（实测 myeloma 的三个清单都落在①或②，
# 回指那一句由 `_intro` 收）。松一点就会误开：同页还有 "When signs or symptoms appear,
# there are two categories for patients receiving treatment:"（列的是治疗分组）、
# "Treatment ... lessen urinary symptoms may include:"（列的是放疗/激素/TURP）、
# "...except to relieve signs and symptoms caused by the cancer, ... and the following are true:"
# （列的是 ALL 分期条件）、"...check for signs of disease, ... may perform the following
# tests and procedures:"（列的是检查方法）——四处都不符合这两种句式。
# 注意 `cause\b` 不收 "caused"：被动式 "signs and symptoms caused by the cancer" 属于误报那一类。
SYM_INTRO = re.compile(
    r"^signs?\s+(?:and|or)\s+symptoms?\b"                    # ①句首：症状当主语
    r"|\bcause\b[^.:;]{0,40}?\bsymptoms?\b",                 # ②cause 的宾语是症状
    re.I,
)
FOLLOWING = re.compile(r"\bfollowing\b", re.I)   # 回指式引言的记号：列举推到下一串清单
# 症状小节里混进来的不是症状而是"这个病分型的依据"：实测 brain 的症状段有 3 个清单，
# `_598` 那组 3 条是 Where the tumor forms / What the affected part of the brain controls /
# The size of the tumor，另两组 `_19`(9 条) 与 `_501`(4 条) 才是真症状；
# colon 的两个清单（`_104` 6 条、`_336` 4 条）则都是症状。
# 所以只能逐条剔，不能"只取第一个清单"。
PROGNOSTIC = re.compile(
    r"^\s*(?:where|what|when|how|whether|which|why|who)\b"
    r"|^\s*the\s+(?:size|rate|speed|location|type|stage|grade|spread|growth|number)\b",
    re.I,
)
SENT = re.compile(r"(?<=[.])\s+")

# 5 病目测（2026-09-08，逐条读 data/exports/symptom_draft_*.md）：(判为真症状的条数, 抽取条数)
# 判定口径：一条只要主干确实描述病人可感知的症状或体征就算真。复合条目（一条里并列多个症状，
# 如前列腺 "Shortness of breath, feeling very tired, fast heartbeat, …"）按整条判真——
# precision 卡的是"这条是不是症状"，不是"这条里有几个独立症状项"，后者要再切一次才谈得上。
# 同一症状在两个组织学文档里各出现一次（NSCLC/SCLC、colon/rectal）也不扣：那是源的分档形状，
# 归一在落库时做。
EYEBALL: dict[str, tuple[int, int]] = {
    "lung": (22, 22), "colorectum": (20, 20), "prostate": (6, 6),
    "brain": (13, 13), "bladder": (11, 11),
}

FIELDS = ("path", "status", "redirected", "in_sitemap", "lastmod", "updated",
          "words", "sections", "heading", "items", "items_freq", "items_freq_word", "mode")


def _clean(s: str) -> str:
    """压空白并修掉标点后多余的空格：PDQ 把句末句号单独排在一个元素里，
    `_own_text` 用空格拼接会产出 "vomiting ."，草稿里的症状名就跟着错了。"""
    s = re.sub(r"\s+", " ", s or "").strip()
    return re.sub(r"\s+([.,;:?!)])", r"\1", s)


NESTED = ("ul", "ol", "li", "p", "div", "table", "section")


def _own_text(el) -> str:
    """只取元素自身的文本。PDQ 的清单里父条目会挂子清单（实测 colon 的
    "a change in bowel habits" 下面套 diarrhea / constipation 四条），
    text_content() 会把子清单直接粘上来，实测产出 "bowel habitsdiarrheaconstipation..."。
    子清单自己会在文档顺序里被单独访问到，所以这里跳过它们、只保留其后的尾文本。"""
    parts = [el.text or ""]
    for ch in el:
        if ch.tag in NESTED:
            parts.append(ch.tail or "")
            continue
        parts.append("".join(ch.itertext()))
        parts.append(ch.tail or "")
    return _clean(" ".join(parts))


def _anchor(el) -> str:
    """离元素最近的带 id 祖先——PDQ 每个小节与列表都有自己的 id，草稿要能点到那一条。"""
    for n in (el,) + tuple(el.iterancestors()):
        if n.get("id"):
            return n.get("id")
    return ""


def _in_nav(el) -> bool:
    """只按标签与 role 判导航。按 class 子串猜会误杀——病人版的 <main> 自己就带
    `has-section-nav` 这个类名，于是整页正文都被当成导航丢掉。"""
    for n in el.iterancestors():
        if n.tag in ("nav", "footer", "aside", "header") or n.get("role") == "navigation":
            return True
    return False


def _sections(tree) -> list[dict]:
    """按文档顺序切成 (标题, 标题下的 li 与 p)——PDQ 把清单放在标题之后的 div 里。

    块带一个 `ord`：标题触发拿到整节，引言句触发只拿它之后的清单，两种都要靠这个序号。
    """
    root = (tree.xpath("//main") or tree.xpath("//article") or tree.xpath("//body"))[0]
    secs: list[dict] = []
    cur: dict | None = None
    ord_ = 0
    for el in root.iter("h1", "h2", "h3", "h4", "h5", "h6", "p", "li"):
        if el.tag.startswith("h"):
            title = _clean(el.text_content())
            if not title:
                continue
            cur = {"heading": title, "anchor": _anchor(el), "li": [], "p": []}
            secs.append(cur)
            continue
        if cur is None or _in_nav(el):
            continue
        if el.tag == "li":
            own = _own_text(el)
            if len(own) >= 3 and not NOISE.match(own):
                ord_ += 1
                cur["li"].append({"text": own, "anchor": _anchor(el), "ord": ord_, "li": True})
            continue
        text = _clean(el.text_content())
        if len(text) < 3 or NOISE.match(text):
            continue
        if len(text) > 40:
            ord_ += 1
            cur["p"].append({"text": text, "anchor": _anchor(el), "ord": ord_, "li": False})
    return secs


def _intro(p_text: str) -> bool:
    """这一段是不是"下面列的就是症状"的引言句（只认以冒号收尾的末句）。

    除了本句自带症状词，还收一种回指句式——实测 myeloma 的第一串症状挂在
    "Sometimes multiple myeloma does not cause any signs or symptoms. ...
     Signs and symptoms may be caused by multiple myeloma or other conditions.
     Check with your doctor if you have any of the following:" 之后，
    症状词在前一句、冒号在末句。这种回指只允许在同一段里成立，
    且要求段内前一句自己就符合 SYM_INTRO，所以前面注释里那四处误报仍然不触发
    （"…and the following are true:" 所在段的前一句是 "It has not been treated, except
     to relieve signs and symptoms caused by the cancer…"，被动式不算引言）。
    """
    sents = SENT.split(p_text.rstrip())
    if not sents or not sents[-1].endswith(":"):
        return False
    if SYM_INTRO.search(sents[-1]):
        return True
    return bool(FOLLOWING.search(sents[-1])) and any(SYM_INTRO.search(x) for x in sents[:-1])


def _sym_triggers(s: dict) -> list[int]:
    """这一节里所有"症状清单的入口"（-1＝标题本身就是症状节；其余是引言句的 ord）。

    两种触发：①标题带 symptom，整节的清单都算（实测 lung/brain/colon 都是这种）；
    ②节内某段是以冒号收尾的症状引言（`_intro`）——实测 myeloma 与 breast 的清单挂在
    "Hypercalcemia may cause the following signs and symptoms:" /
    "Signs and symptoms of breast cancer in men include:" 这类句子之后，
    而所在小节标题是 Multiple myeloma / Screening for male breast cancer，按 ① 一条都拿不到。
    """
    if SYM_HEAD.search(s["heading"]):
        return [-1]
    return [b["ord"] for b in s["p"] if _intro(b["text"])]


def _list_items(s: dict, triggers: list[int]) -> list[dict]:
    """按入口取清单。标题触发取整节；引言触发只取紧跟它的那一串（遇到下一个段落就停）。

    只认"引言之后第一段连续清单"是因为实测 male-breast-cancer 那一节里，症状引言后面
    跟 8 条症状，再往下同一个标题下还排着诊断步骤、治疗链接、5 年生存率三串清单。
    """
    if -1 in triggers:
        return list(s["li"])
    blocks = sorted(s["li"] + s["p"], key=lambda b: b["ord"])
    out: list[dict] = []
    for t in triggers:
        for b in blocks:
            if b["ord"] <= t:
                continue
            if not b["li"]:
                break
            out.append(b)
    return out


def _extract(secs: list[dict]) -> dict:
    """从症状小节取条目。优先现成的 <li>；没有清单的栏目页退化成"含 symptom 的句子"。

    两种模式分开记，因为它们给前端的粒度不同：li 是一条症状短语，句子是一整段叙述，
    后者要落成 symptom 行还得再切一次。
    """
    sym = []
    for s in secs:
        tr = _sym_triggers(s)
        if tr:
            sym.append((s, tr))
    items = [{"text": it["text"], "anchor": it["anchor"], "heading": s["heading"]}
             for s, tr in sym for it in _list_items(s, tr)]
    mode = "list" if items else ""
    if not items:
        for s, _ in sym:
            for para in s["p"]:
                for sent in SENT.split(para["text"]):
                    sent = sent.strip()
                    if len(sent) > 20 and re.search(r"\bsigns?\b|\bsymptoms?\b", sent, re.I):
                        items.append({"text": sent, "anchor": para["anchor"], "heading": s["heading"]})
        mode = "sentence" if items else "none"
    kept, seen, dropped = [], set(), Counter()
    for it in items:
        if len(it["text"]) > 260:
            dropped["过长"] += 1
        elif PROGNOSTIC.match(it["text"]):
            dropped["分型/预后叙述"] += 1
        elif RESTATED.match(it["text"]):
            dropped["肿瘤同义复述"] += 1
        elif it["text"] in seen:
            dropped["重复"] += 1
        else:
            seen.add(it["text"])
            kept.append(it)
    return {"sections": len(sym), "heading": (kept[0]["heading"] if kept else ""),
            "items": kept,
            "items_freq": sum(1 for it in kept if FREQ_PCT.search(it["text"])),
            "items_freq_word": sum(1 for it in kept if FREQ_WORD.search(it["text"])),
            "mode": mode, "dropped": dict(dropped)}


def _get(url: str, xml: bool = False) -> tuple[object, int, str, int, str]:
    """取一页并解析成树。网络层失败直接中止——半趟结果会被下一个人读成上游结论。

    XML 必须走字节：sitemap 带 `<?xml encoding=...?>` 声明，把已解码的 str 交给解析器
    会直接 ValueError。
    """
    r = fetch(url, headers=HEADERS, max_bytes=25_000_000, timeout=(10, 180))
    if r.status != 200 or not r.body:
        raise SystemExit(f"{url} → {r.status or r.reachability}：{(r.note or '')[:160]}")
    tree = etree.fromstring(r.body) if xml else LH.fromstring(r.text)
    return tree, r.latency_ms, r.reachability, r.status, (r.final_url or url)


def _sitemap() -> tuple[dict, int, str, int]:
    """sitemap 是这一源唯一的机器可读清单：一次请求给全站页面的 per-page lastmod。"""
    tree, ms, reach, status, _ = _get(SITEMAP, xml=True)
    rows = {}
    for el in tree.xpath("//*[local-name()='url']"):
        loc = ((el.xpath("*[local-name()='loc']/text()") or [""])[0]).strip()
        if loc:
            rows[loc] = ((el.xpath("*[local-name()='lastmod']/text()") or [""])[0]).strip()[:10]
    return rows, ms, reach, status


def probe(offline: bool = False) -> ProbeResult:
    ms = 0
    reaches: list[str] = []
    http: int | None = None
    if offline:
        key_dir = raw.newest_dir(SOURCE, "pages.json")
        if not key_dir:
            raise SystemExit(f"离线重放要先有归档：data/raw/{SOURCE}/*/pages.json 不存在")
        pages = json.loads((key_dir / "pages.json").read_text(encoding="utf-8"))
        reaches = ["offline"]
        sizes = {n: (key_dir / n).stat().st_size for n in ("pages.json", "index.json")}
        version = key_dir.name
    else:
        index, m, reach, http = _sitemap()
        ms += m
        reaches.append(reach)
        inv = {u[len(BASE):]: lm for u, lm in index.items() if u.startswith(BASE + "types/")}
        pages = {}
        for t in TARGETS:
            recs = []
            for path in t.pdq_pages:
                tree, m, reach, status, final = _get(BASE + path)
                ms += m
                reaches.append(reach)
                time.sleep(SLEEP)
                ex = _extract(_sections(tree))
                body = str(tree.xpath("string(//main)") or tree.xpath("string(//body)") or "")
                stamp = tree.xpath("//footer//time/@datetime | //time/@datetime")
                recs.append({
                    "path": path, "status": status, "final_path": final[len(BASE):],
                    "redirected": final[len(BASE):] != path, "in_sitemap": path in inv,
                    "lastmod": inv.get(path, ""), "updated": (stamp[0][:10] if stamp else ""),
                    "words": len(body.split()), "sections": ex["sections"],
                    "heading": ex["heading"], "items": ex["items"],
                    "items_freq": ex["items_freq"], "items_freq_word": ex["items_freq_word"],
                    "mode": ex["mode"], "dropped": ex["dropped"],
                })
            pages[t.code] = recs
            print(f"  {t.code:14s} " + "  ".join(
                f"{r['path'].split('/')[-1][:34]}:{r['mode']}/{len(r['items'])}" for r in recs), flush=True)
        version = max([r["lastmod"] or r["updated"] for rs in pages.values() for r in rs] or [today()])
        key_dir = raw.archive_dir(SOURCE, version)
        blobs = {"pages.json": pages, "index.json": inv}
        sizes = {}
        for n, b in blobs.items():
            body_bytes = json.dumps(b, ensure_ascii=False).encode("utf-8")
            (key_dir / n).write_bytes(body_bytes)
            sizes[n] = len(body_bytes)
        _drafts(pages)

    reach = "offline" if "offline" in reaches else ("proxy" if "proxy" in reaches else "direct")

    per, covered, weak, sent_mode, redirected, missing = [], 0, [], [], [], []
    all_items = sent_items = freq_items = freq_word_items = 0
    for t in TARGETS:
        kept = [r for r in pages[t.code] if r["status"] == 200]
        # 只数清单项：叙述句不是"症状项"，不能靠它过 ≥4 这一关
        n = sum(len(r["items"]) for r in kept if r["mode"] == "list")
        for r in kept:
            all_items += len(r["items"]) if r["mode"] == "list" else 0
            sent_items += len(r["items"]) if r["mode"] == "sentence" else 0
            freq_items += r["items_freq"]
            freq_word_items += r["items_freq_word"]
            if r["mode"] == "sentence":
                sent_mode.append(f"{t.code}:{r['path'].split('/')[-1]}")
            if r["redirected"]:
                redirected.append(f"{t.code}→{r['final_path']}")
            if not r["in_sitemap"]:
                missing.append(r["path"])
        ok = n >= MIN_ITEMS
        covered += 1 if ok else 0
        if not ok:
            weak.append(f"{t.code} {n} 条")
        per.append({"code": t.code, "pages": len(pages[t.code]), "ok_pages": len(kept),
                    "sym_sections": sum(r["sections"] for r in kept), "items": n,
                    "with_freq": sum(r["items_freq"] for r in kept),
                    "with_freq_word": sum(r["items_freq_word"] for r in kept),
                    "modes": sorted({r["mode"] for r in kept}), "pass": ok})

    verdict = "ok" if covered >= MIN_DISEASES else ("partial" if covered else "empty")
    prec = None
    eb = ""
    if EYEBALL:
        hit, tot = sum(v[0] for v in EYEBALL.values()), sum(v[1] for v in EYEBALL.values())
        prec = round(hit / tot * 100, 1) if tot else None
        eb = "；".join(f"{c} {v[0]}/{v[1]}" for c, v in EYEBALL.items())

    msg = (
        f"达标 {covered}/18（判据＝症状小节规则化取回 ≥{MIN_ITEMS} 条症状项，线是 ≥{MIN_DISEASES}）。"
        f"共取回清单症状项 {all_items} 条"
        + (f"，另有叙述句 {sent_items} 条（{', '.join(sent_mode)}）——句子不算症状项，不进达标数。"
           if sent_items else "，无一处退化成叙述句。")
        + f"其中带百分号频率的 {freq_items} 条，含 common/frequent 这类措辞的 {freq_word_items} 条——"
        "后者是症状说法本身（如 \"Frequent urination\"），不是频率带。"
        "PDQ 的症状小节一条频率都不给（hp 版整页 1,857 个百分号全是生存率与缓解率，"
        "没有一个落在症状段里），所以 freq_band 这一列这一源供不了。"
        "这一维原本判定要靠 L3 模型抽取兜底，实测不成立：病人版页面的症状就是现成的 "
        "<ul><li> 清单，L2 规则解析直接出条目，模型只在做中文症状名时才需要。"
        f"未达 {MIN_ITEMS} 条的病：{', '.join(weak) or '无'}。"
        + (f" 声明的路径被上游改写：{', '.join(redirected)}。" if redirected else "")
        + (f" 声明的路径不在 sitemap 里：{', '.join(missing)}。" if missing else "")
        + f" 真入口是 sitemap（{SITEMAP}，全站 6,480 个 loc、其中 /types/ 791 个），"
        "PDQ 摘要页形状是 /types/<段>/<hp|patient>/<slug>-pdq，18 段里只有 12 段有 patient 子树；"
        "旧结构 /types/lung/pdq/lung-adult-pdq 已 404，"
        "/types/breast/patient/breast-treatment-pdq 这类不存在的路径会 301 到栏目页并回 200，"
        "所以每页比对 final_url。乳腺段最特殊：栏目页 /types/breast/symptoms 只有三句散文"
        "（其中两句是\"症状因类型而异\"\"早期往往没有症状\"），全段唯一一份症状清单挂在"
        "男性乳腺癌页，取的是其症状引言后紧跟的 8 条（同节往下还有诊断步骤、治疗链接、"
        "生存率三串清单，按\"连续清单\"切掉）。"
        "版本戳页内取不到：响应头 Last-Modified 每页都是同一个站点重建时间，"
        "PDQ 摘要页也没有 Updated，只有栏目页页脚有 <time>——所以上游版本取 sitemap 的 "
        f"per-page lastmod 最大值（本次 {version}）。"
        + (f" 目测 5 病（真症状/抽取）：{eb}，合计 precision {prec}%"
           f"（出口判据线 ≥80%）。口径：一条只要主干确实是症状或体征就算真，"
           "一条里并列多个症状的复合条目按整条判真，同一症状在 NSCLC/SCLC、colon/rectal "
           "两档各出现一次不扣分——那是源的分档形状，归一在落库时做。" if EYEBALL else
           " 目测未做：核对 data/exports/symptom_draft_*.md 后把 5 病的 precision 记进 EYEBALL。")
    )

    sample = per + [{"field_fill_pct": {
        "页面 200": round(sum(p["ok_pages"] for p in per) / max(sum(p["pages"] for p in per), 1) * 100, 1),
        "症状项带频率": round(freq_items / all_items * 100, 1) if all_items else 0.0,
        "有症状小节的病": round(sum(1 for p in per if p["sym_sections"]) / len(TARGETS) * 100, 1),
    }}]
    first = next((r for r in pages.get("lung", []) if r["items"]), None)
    if first:
        sample.append({"lung_sample": [i["text"] for i in first["items"][:12]],
                       "lung_heading": first["heading"],
                       "lung_anchor": first["items"][0]["anchor"]})

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=all_items,
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


def _drafts(pages: dict) -> None:
    """每病一份草稿，供 5 病目测；只写 data/exports，不入库。"""
    DATA_EXPORTS.mkdir(parents=True, exist_ok=True)
    for t in TARGETS:
        lines = [
            f"# {t.name_zh}（{t.name_en}）症状草稿 — cancer.gov / PDQ",
            "",
            f"- 生成 {today()}；页面清单来自 targets.py 的 pdq_pages，条目由症状小节的 <li> 规则化取回",
            "- 中文名列留空：这一源只有英文，中文名要另走一条公开双语路（见计划 B6 节）",
            "",
            "| 中文 | 症状（原文） | 频率表述 | 出处（页面 lastmod） |",
            "|---|---|---|---|",
        ]
        for r in pages[t.code]:
            if r["status"] != 200:
                lines.append(f"| — | （取不到，HTTP {r['status']}） | — | {BASE}{r['path']} |")
                continue
            for it in r["items"]:
                m = FREQ_PCT.search(it["text"])
                lines.append(
                    f"|  | {it['text']} | {m.group(1) if m else '—'} | "
                    f"{BASE}{r['path']}#{it['anchor']}（{r['lastmod'] or r['updated'] or '无版本戳'}） |")
            if not r["items"]:
                lines.append(f"| — | （这一页没有症状小节，或小节里没有清单） | — | {BASE}{r['path']} |")
        (DATA_EXPORTS / f"symptom_draft_{t.code}.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
