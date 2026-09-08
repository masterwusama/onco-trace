"""Wikidata / 中文维基探针：判"中文症状名到底有没有可匿名取回的公开路"。

B6 把症状维定在 L2（PDQ 的清单是现成 `<li>`），但 PDQ 只给英文。这一支回答的是
"剩下 15 病的中文症状名怎么来"，出口判据是自己定的线（八维表里没有"中文名列"这一行）：
**词典逐条 precision ≥80%，或 ≥12/18 病有 ≥4 条现成中文症状清单**。实测两条都不过，
如实记 partial。三条路各自的数是这一支跑出来的，不是别人家的结论：

  ① **Wikidata 症状子树当英中词典**（一次 SPARQL，`wdt:P279*` 从 Q169872 symptom 往下）：
     子树 10,045 个实体、2,158 个带 zh label，CC0 许可、不用人写一个中文字。
     按英文 label + alias 子串匹配能落 78.9% 的 PDQ 条目，但那个 78.9% 是假的——
     逐条配对目测只有 37.5% 是"对"的，36.9% 是**另一个概念的译名**
     （`weight loss → 减肥` 取的是节食义项、`jaundice → 弥散性血管内凝血`、
     `a bloated feeling after eating → 瘤胃臌氣` 是牛的反刍气胀），25.6% 只翻出句里的泛词
     （所有 `*pain → 疼痛`、所有 `swelling → 水肿`，症状身份丢掉）。
     根因：Wikidata 的 label/alias 是**实体名**不是**翻译记忆**，拿它去匹配临床散文，
     命中的永远是含泛词的最长别名实体。错出来的中文在 UI 上看着完全正常，这是最坏的一种失败。
  ② **拿 PDQ 原文逐条查**（`wbsearchentities`）：天花板由词形分布决定——PDQ 的去重写法里
     ≤2 词只占 21.3%，≥5 词占 53.8%，Wikidata 查的是词条不是短语。36 个短词样本
     （代表"最顺利的情况"）里 34 个命中实体、30 个带 zh label，但 `anemia` 命中
     Q3353348 → 密穗蕨科（一个蕨类科）、`fever` 命中的 Qid 根本没有中文标签，
     且 `languages=zh` 取回的是繁简混排（`頭暈` 与 `黄疸` 同屏）。
  ③ **中文维基条目自己的症状章节**（不翻译，另找一个中文源）：只有 5 病有 ≥4 条真症状清单
     （白血病 10、骨髓瘤 8、子宫体 9、胰腺 5、结直肠 4），中文本身全对；但解析条数虚高，
     结直肠解析 17 条里 13 条是分期定义、骨髓瘤那节标题本身就叫「症狀及併發症」。
     另 11 病**有"症狀"章节却写成散文**，规则解析一条取不到；要抠出来就得上中文分句 +
     症状判定的 L3，而 L3 已随 B6 一起撤掉；2 病连章节都没有（肝癌、非霍奇金淋巴瘤）。
     标题这一步有坑：`action=parse` 不认 `converttitles`，而我们写的简体标题到真页面之间
     有三种边（`normalized` / `converted` / `redirects`），只按字面标题匹配会让
     宫颈癌/甲状腺癌/肾癌/多发性骨髓瘤 稳定 missingtitle，被误报成"这病没有症状章节"。

裁定（不在本模块执行，写在这里是为了让日志与结论同源）：中文症状名**不做成翻译列**，
`symptom` 按 `(source_id, disease_code, name, name_lang)` 落，缺就空着。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse

from lxml import html as LH

from .. import raw
from ..clock import today
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "wikidata"
DATASET = "zh-label-symptom"
WD_API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
ZH_API = "https://zh.wikipedia.org/w/api.php"
SYM_ROOT = "Q169872"          # symptom。实测 wbsearchentities 里 label 恰好等于 "symptom" 的那一个
MIN_PRECISION = 80.0          # 出口判据之一：词典逐条 precision
MIN_DISEASES = 12             # 出口判据之二：≥12/18 病有现成中文症状清单
MIN_SYM = 4                   # "清单"的下限，与 PDQ 那一支对齐
CRITERIA = (f"中文症状名可匿名取回：词典逐条目测 precision ≥{MIN_PRECISION:.0f}%，"
            f"或 ≥{MIN_DISEASES}/18 病有 ≥{MIN_SYM} 条现成中文症状清单")

# 本趟每次请求的 (reachability, status)，probe() 开头清空。放在模块级是因为
# 三支路线各自发十几个请求，逐个往上传返回值只会让签名变脏；探针是单发作业，
# 不存在并发复用。收口时取出现最多的那一态如实上报，不硬写 "proxy"。
_TRACE: list[tuple[str, int]] = []

# B6b 实测：WHO 中文版有 ≥4 条现成症状清单的病。写死在这里而不是去查另一支探针的库行，
# 是为了让"哪些病已经有中文清单"这件事在本模块里可读、可比对；
# 它变了 nci_pdq/who_factsheet 那边不会通知这里，所以覆盖度矩阵里这一列以本探针自述为准。
WHO_ZH_LIST = ("breast_female", "colorectum", "lung")

# 路线③的目测（2026-09-08，逐条读完五病解析回来的清单条目）。规则解析只能数 <li>/<dd>，
# 分不清"症状项"与"挂在同一名目下的分期定义"，所以这一层判定按 nci_pdq_html 的 EYEBALL
# 同一口径处理：人读过的结论作为声明进代码，不进任何录入界面，探针只按这份声明算达标。
# 值 = (真症状条数, 为什么不是解析出来的那个数)。
WIKI_EYEBALL: dict[str, tuple[int, str]] = {
    "colorectum": (4, "解析 17 条里 13 条是 0–IV 期与 A–D 期/B1–C2 的分期定义，症状只有前 4 条"),
    "leukemia": (10, "解析 15 条里 5 条是白血病亚型描述（慢性骨髓性／慢性淋巴性…），不是症状"),
    "myeloma": (8, "解析 19 条是 10 个名目 + 9 段释义（dl/dt/dd 交替），且章节名为「症狀及併發症」，"
                   "高血鈣症與腎功能減退属并发症"),
    "uterus": (9, "9 条全是症状，五病里最干净的一份"),
    "pancreas": (5, "解析 7 条里 糖尿病／遊走性血栓靜脈炎／重性抑郁障碍 是共病与体征，不算症状项"),
}

# 逐条查路线的样本：36 个"最顺利情况"的短词（PDQ 里真实出现过的症状说法）
SEARCH_TERMS = (
    "cough", "chest pain", "shortness of breath", "wheezing", "hoarseness",
    "loss of appetite", "weight loss", "fatigue", "difficulty swallowing",
    "blood in stool", "diarrhea", "constipation", "abdominal pain", "vomiting",
    "back pain", "headache", "seizure", "nausea", "dizziness", "frequent urination",
    "painful urination", "bone pain", "swelling", "numbness", "weakness",
    "coughing up blood", "anemia", "urinary retention", "blurred vision",
    "fever", "night sweat", "unexplained weight loss", "jaundice", "itching",
    "bloated abdomen", "loss of bladder control",
)

# 中文维基路线：病 → 条目名。填简体是刻意的——规范标题由探针先解析一次拿回，
# 因为 action=parse 不认 converttitles，硬写繁体在条目改名时会静默失效
WIKI_TITLE = {
    "lung": "肺癌", "colorectum": "结直肠癌", "liver": "肝癌", "stomach": "胃癌",
    "breast_female": "乳腺癌", "pancreas": "胰腺癌", "esophagus": "食管癌",
    "prostate": "前列腺癌", "cervix": "宫颈癌", "ovary": "卵巢癌", "thyroid": "甲状腺癌",
    "bladder": "膀胱癌", "kidney": "肾癌", "brain": "脑癌", "uterus": "子宫内膜癌",
    "leukemia": "白血病", "nhl": "非霍奇金淋巴瘤", "myeloma": "多发性骨髓瘤",
}
SYM_HEAD = re.compile(r"(症状|症狀|病徵|病征|症候|臨床表現|临床表现|體徵|体征|徵象|征象)")
# 参考文献与引用模板的产物。中文维基的症状段里挂着大量 cite 模板，
# 不过滤会把 <style> 块和 "引用错误：没有为名为 X 的参考文献提供内容" 当成症状项
NOISE = re.compile(r"(引用错误|PMID|S2CID|doi:|ISBN|Accessed|原始内容存档|参考文献|"
                   r"延伸閱讀|外部連結|^\^|^ \d+\.\d+)")
BAD_CLASS = re.compile(r"(references|reflist|mw-references|navbox|footer|thumb|gallery|infobox)", re.I)

# 词典路线的目测（2026-09-08，逐条读过去重后的 135 个「PDQ 原文 → 中文」配对，
# 它们给 168 条命中行计权）。
# 键是 "原文小写|中文"，值：对＝中文确实是这一条说的症状；泛＝只翻出句里的泛词，丢了症状身份；
# 错＝另一个概念的译名。探针只按这份声明算 precision，没声明过的配对如实计为"未判"，
# 不猜——猜就会把上游换了措辞之后的小幅漂移读成大幅好转。
ZH_JUDGE: dict[str, str] = {
    "a cough that doesn't go away or gets worse over time|咳嗽": "对",
    "a swollen abdomen|腹脹": "对",
    "an open wound (ulcer) in the skin of the breast|皮膚潰瘍": "对",
    "anemia|贫血": "对",
    "ascites (buildup of fluid in the abdomen)|腹水": "对",
    "blood in the stool|黑便": "对",
    "confusion or trouble thinking.|神智混亂": "对",
    "constipation|便秘": "对",
    "constipation.|便秘": "对",
    "dark urine|深色尿液": "对",
    "diarrhea|腹瀉": "对",
    "diarrhea.|腹瀉": "对",
    "drenching night sweats|夜間盜汗": "对",
    "drenching night sweats.|夜間盜汗": "对",
    "fatigue|疲倦": "对",
    "fatigue (feeling very tired)|疲倦": "对",
    "feeling thirsty.|口渴": "对",
    "fever|发热": "对",
    "fever and infection.|发热": "对",
    "fever for no known reason or frequent infections.|发热": "对",
    "fever for no known reason.|发热": "对",
    "fever or drenching night sweats|发热": "对",
    "frequent urination|頻尿症": "对",
    "frequent urination.|頻尿症": "对",
    "heartburn|胸口灼熱": "对",
    "hoarseness|嘶哑": "对",
    "hoarseness.|嘶哑": "对",
    "indigestion and heartburn.|消化不良": "对",
    "loss of balance and trouble walking.|姿態不穩": "对",
    "lower back pain on one side of the body|下背痛": "对",
    "mild nausea|恶心": "对",
    "muscle weakness.|乏力": "对",
    "seizures.|癲癇發作": "对",
    "shortness of breath|呼吸困难": "对",
    "shortness of breath, feeling very tired, fast heartbeat, dizziness, or pale skin caused by anemia.|呼吸困难": "对",
    "skin rash or itchy skin.|疹": "对",
    "swelling caused by fluid in your body's tissues.|水肿": "对",
    "swollen lymph nodes under the arm or near the collarbone|淋巴结肿大": "对",
    "tingling or numbness in your legs and feet.|觸覺遲鈍": "对",
    "trouble swallowing|吞嚥": "对",
    "trouble swallowing.|吞嚥": "对",
    "unusual sleepiness or change in activity level.|嗜睡": "对",
    "unusual tiredness or weakness|乏力": "对",
    "vaginal discharge that is watery and has a strong odor or that contains blood|阴道分泌物": "对",
    "vomiting|呕吐": "对",
    "weakness of the arms or legs.|乏力": "对",
    "weakness or fatigue|乏力": "对",
    "weakness or feeling tired|乏力": "对",
    "weakness or feeling tired.|乏力": "对",
    "weakness.|乏力": "对",
    "a pain in the back that doesn't go away|疼痛": "泛",
    "a pain in the side that doesn't go away|疼痛": "泛",
    "bleeding|出血": "泛",
    "chest discomfort or pain|不适": "泛",
    "difficult or painful bowel movements or bleeding from the rectum when having a bowel movement|出血": "泛",
    "difficult or painful urination or blood in the urine|疼痛": "泛",
    "difficult or painful urination.|疼痛": "泛",
    "discomfort in the upper abdomen on the right side|不适": "泛",
    "dull backache|疼痛": "泛",
    "frequent nausea and vomiting.|呕吐": "泛",
    "gastrointestinal problems, such as gas, bloating, or constipation.|便秘": "泛",
    "general abdominal discomfort (frequent gas pains, bloating, fullness, or cramps)|不适": "泛",
    "hoarseness and cough.|咳嗽": "泛",
    "indigestion and stomach discomfort|不适": "泛",
    "morning headache or headache that goes away after vomiting.|呕吐": "泛",
    "nausea and vomiting|呕吐": "泛",
    "nausea or vomiting.|呕吐": "泛",
    "pain behind the breastbone.|疼痛": "泛",
    "pain during sexual intercourse.|疼痛": "泛",
    "pain in the abdomen|疼痛": "泛",
    "pain in the bones or stomach|疼痛": "泛",
    "pain in the chest, abdomen, or bones for no known reason.|疼痛": "泛",
    "pain in the upper or middle abdomen and back|疼痛": "泛",
    "pain near the right shoulder blade or in the back|疼痛": "泛",
    "pain or burning during urination|疼痛": "泛",
    "pain when swallowing.|吞嚥": "泛",
    "painful or difficult swallowing.|吞嚥": "泛",
    "painful or frequent urination|疼痛": "泛",
    "pale, chalky bowel movements and dark urine|深色尿液": "泛",
    "petechiae (flat, pinpoint spots under the skin, caused by bleeding)|出血": "泛",
    "shortness of breath, feeling very tired, fast heartbeat, dizziness, or pale skin|呼吸困难": "泛",
    "stomach pain|疼痛": "泛",
    "swelling in the face and veins in the neck|水肿": "泛",
    "swelling in the face and/or veins in the neck|水肿": "泛",
    "swelling in the feet|水肿": "泛",
    "swelling of the legs|水肿": "泛",
    "vaginal bleeding after menopause|出血": "泛",
    "vaginal bleeding after menopause.|出血": "泛",
    "vaginal bleeding after sex|出血": "泛",
    "vaginal bleeding between periods or periods that are heavier or longer than normal|出血": "泛",
    "weakness or numbness in the arms or legs.|乏力": "泛",
    "a bloated feeling after eating|瘤胃臌氣": "错",
    "a lump in the pelvic area.|原發性體液淋巴瘤": "错",
    "a lump or change in the breast can be a symptom of breast cancer.|乳癌": "错",
    "back pain or pain that spreads from the back towards the arms or legs.|急性呼吸窘迫症候群": "错",
    "being unable to urinate|無β脂蛋白血症": "错",
    "blood in sputum (mucus coughed up from the lungs)|咳嗽": "错",
    "bone pain or tenderness|中毒性表皮壞死鬆解症": "错",
    "bone pain, especially in the back or ribs.|大腸激躁症": "错",
    "change in appetite|耳咽管開放症": "错",
    "easy bruising or bleeding|出血": "错",
    "easy bruising or bleeding.|出血": "错",
    "enlarged tongue.|非淋病性尿道炎": "错",
    "extreme tiredness|紅斑": "错",
    "frequent urination (especially at night).|急性淋巴性白血病": "错",
    "however, early breast cancer often has no symptoms, which is why breast cancer screening is important.|中毒性表皮壞死鬆解症": "错",
    "jaundice (yellowing of eyes and skin)|弥散性血管内凝血": "错",
    "jaundice (yellowing of the skin and whites of the eyes)|弥散性血管内凝血": "错",
    "loss of appetite|耳咽管開放症": "错",
    "loss of appetite or feelings of fullness after eating a small meal|急性淋巴性白血病": "错",
    "loss of appetite.|耳咽管開放症": "错",
    "pain in the back, hips, or pelvis that doesn't go away.|原發性體液淋巴瘤": "错",
    "pain in the pelvic area.|原發性體液淋巴瘤": "错",
    "pain or a feeling of fullness below the ribs on the left side|大腸激躁症": "错",
    "pain or a feeling of fullness below the ribs.|大腸激躁症": "错",
    "pain or feeling of fullness below the ribs|大腸激躁症": "错",
    "pain, swelling, or a feeling of pressure in the abdomen or pelvis.|原發性體液淋巴瘤": "错",
    "painless lumps in the neck, underarm, stomach, or groin|疼痛": "错",
    "painless swelling of the lymph nodes in the neck, underarm, stomach, or groin.|水肿": "错",
    "pelvic pain or pain during sex|原發性體液淋巴瘤": "错",
    "petechiae (flat, pinpoint, dark-red spots under the skin caused by bleeding).|出血": "错",
    "purple spots on the skin.|姿勢性心搏過速": "错",
    "scaly, red, or swollen skin on the breast, nipple, or areola|心停止": "错",
    "signs and symptoms may vary, based on the type of breast cancer as well as how advanced the cancer is.|乳癌": "错",
    "signs and symptoms of breast cancer may include a lump or change in your breast.|乳癌": "错",
    "swelling in the lymph nodes in the neck, underarm, groin, or stomach.|水肿": "错",
    "unintended weight loss and loss of appetite|中毒性表皮壞死鬆解症": "错",
    "urinating often during the night|中毒性表皮壞死鬆解症": "错",
    "vaginal bleeding or discharge not related to menstruation (periods).|出血": "错",
    "vision, hearing, and speech problems.|嗜酸性粒細胞增多肌痛症": "错",
    "weight loss for no known reason|减肥": "错",
    "weight loss for no known reason.|减肥": "错",
    "weight loss or loss of appetite|减肥": "错",
    "weight loss with no known reason|减肥": "错",
    "weight loss.|减肥": "错",
}


def _json(url: str, accept: str, timeout, tries: int = 3) -> tuple[object, int, str, int]:
    """取一个 JSON 接口。重试到顶还是不成才中止：半趟结果会被下一个人读成上游结论。

    这一支要连发 70 多个请求走本机代理，其中一发 CONNECT 抖动不代表源坏了；
    但每一次尝试都记进 _TRACE（含失败的），收口时才知道这趟到底是怎么过去的。
    """
    status = reach = note = None
    for k in range(tries):
        r = fetch(url, headers={"Accept": accept}, max_bytes=40_000_000, timeout=timeout)
        _TRACE.append((r.reachability, r.status))
        status, reach, note = r.status, r.reachability, r.note
        if r.status == 200 and r.body:
            try:
                return json.loads(r.text), r.latency_ms, r.reachability, r.status
            except json.JSONDecodeError as e:
                raise SystemExit(f"{url} 不是 JSON：{e} {r.text[:160]}") from e
        time.sleep(1 + k)
    raise SystemExit(f"{url} → {status or reach}（重试 {tries} 次仍失败）：{(note or '')[:160]}")


def _pdq_items() -> list[tuple[str, str]]:
    """PDQ 已归档的症状条目——本探针的分母。它是另一支探针的产物，刻意不重复下载。

    依赖写在这里而不是悄悄假设：data/raw/nci_pdq_html/*/pages.json 不在就没法判
    "词典能不能覆盖 PDQ 的真实措辞"，那种时候必须中止而不是拿一份自造的短词表蒙混。
    """
    d = raw.newest_dir("nci_pdq_html", "pages.json")
    if not d:
        raise SystemExit("缺 data/raw/nci_pdq_html/*/pages.json：先跑 probe --code nci_pdq_html")
    pages = json.loads((d / "pages.json").read_text(encoding="utf-8"))
    return [(code, re.sub(r"\s+", " ", it["text"]).strip())
            for code, pl in pages.items() for p in pl for it in p.get("items", [])]


def _search_route() -> dict[str, dict]:
    """路线②：拿 PDQ 的说法逐条查 Wikidata，再批量取 zh label。"""
    out: dict[str, dict] = {}
    for t in SEARCH_TERMS:
        u = (f"{WD_API}?action=wbsearchentities&format=json&language=en&uselang=en"
             f"&limit=1&type=item&search=" + urllib.parse.quote(t))
        d, ms, _reach, _st = _json(u, "application/json", (8, 25))
        hits = d.get("search") or []
        out[t] = {"qid": hits[0]["id"] if hits else "", "ms": ms}
        time.sleep(0.2)
    ids = sorted({v["qid"] for v in out.values() if v["qid"]})
    labels: dict[str, str] = {}
    for i in range(0, len(ids), 25):
        u = (f"{WD_API}?action=wbgetentities&format=json&props=labels&languages=zh&ids="
             + "|".join(ids[i:i + 25]))
        d, _ms, _reach, _st = _json(u, "application/json", (8, 60))
        ents = d.get("entities", {})
        for q in ids[i:i + 25]:
            labels[q] = (ents.get(q, {}).get("labels", {}).get("zh") or {}).get("value", "")
    for v in out.values():
        v["zh"] = labels.get(v["qid"], "")
    return out


def _dict_route() -> tuple[dict[str, dict], int]:
    """路线①：一次 SPARQL 拉 symptom 子树的 en label / en alias / zh label。"""
    # 四段都必须带 f 前缀：{{ }} 只在 f-string 里才是转义。之前只有首段是 f-string，
    # 后三段把双花括号原样送进查询，Wikidata 直接回 400
    q = (f"SELECT DISTINCT ?e ?en ?zh ?alt WHERE {{ ?e wdt:P279* wd:{SYM_ROOT} . "
         f'OPTIONAL {{ ?e rdfs:label ?en FILTER (lang(?en) = "en") }} '
         f'OPTIONAL {{ ?e rdfs:label ?zh FILTER (lang(?zh) = "zh") }} '
         f'OPTIONAL {{ ?e skos:altLabel ?alt FILTER (lang(?alt) = "en") }}}}')
    d, ms, _reach, _st = _json(SPARQL + "?format=json&query=" + urllib.parse.quote(q),
                               "application/sparql-results+json", (10, 240))
    ent: dict[str, dict] = {}
    for b in d["results"]["bindings"]:
        e = b["e"]["value"].rsplit("/", 1)[-1]
        row = ent.setdefault(e, {"en": "", "zh": "", "alt": []})
        if "en" in b:
            row["en"] = b["en"]["value"]
        if "zh" in b:
            row["zh"] = b["zh"]["value"]
        if "alt" in b and b["alt"]["value"] not in row["alt"]:
            row["alt"].append(b["alt"]["value"])
    return ent, ms


def _wiki_titles() -> dict[str, str]:
    """一次解析 18 个条目的规范标题。跳过这步就会在 宫颈癌/甲状腺癌/肾癌 上 missingtitle。

    从我们写的简体标题走到真页面，响应里可能有三种边，缺一种就会把"标题没解析出来"
    报成"这病没有症状章节"：`normalized` 是大小写与下划线，`converted` 是
    `converttitles` 做的繁简变体（甲状腺癌→甲狀腺癌），`redirects` 是条目改名或重定向
    （宫颈癌→子宮頸癌）。只认 `normalized` 时后两种边断链，四病落回原写法去 parse，
    稳定 missingtitle。
    """
    u = (f"{ZH_API}?action=query&format=json&redirects=1&converttitles=1&titles="
         + urllib.parse.quote("|".join(WIKI_TITLE.values())))
    d, _ms, _reach, _st = _json(u, "application/json", (8, 40))
    q = d.get("query", {})
    back: dict[str, str] = {}
    for key in ("normalized", "converted", "redirects"):
        for e in q.get(key) or []:
            if e.get("from") and e.get("to"):
                back[e["to"]] = e["from"]
    canon: dict[str, str] = {}
    for page in (q.get("pages") or {}).values():
        title, hops = page.get("title", ""), 0
        seen = {title}
        while title in back and hops < 8:  # 沿链回溯到最初那个写法；维基的重定向会串两跳
            title = back[title]
            hops += 1
            if title in seen:
                break
            seen.add(title)
        code = next((c for c, t in WIKI_TITLE.items() if t in seen), None)
        if code:
            canon[code] = page.get("title", "")
    return canon


def _wiki_items(html: str) -> list[str]:
    tree = LH.fromstring(html)
    # 先物化再删：lxml 的 iter() 走的是活树，删着删着就会跳过还没看过的节点
    for bad in list(tree.iter("style", "script", "sup")):
        if bad.getparent() is not None:
            bad.getparent().remove(bad)
    for box in list(tree.iter()):
        if box.tag in ("ul", "ol", "div", "table") and BAD_CLASS.search(box.get("class") or ""):
            if box.getparent() is not None:
                box.getparent().remove(box)
    out: list[str] = []
    for li in tree.iter("li", "dd"):
        x = re.sub(r"\s+", " ", li.text_content()).strip(" ,，。；;：:")
        if len(x) >= 2 and not NOISE.search(x) and x not in out:
            out.append(x)
    return out


def _wiki_route(canon: dict[str, str]) -> dict[str, dict]:
    """路线③：中文维基条目自己的症状章节。"""
    out: dict[str, dict] = {}
    for code in WIKI_TITLE:
        title = canon.get(code) or WIKI_TITLE[code]
        u = (f"{ZH_API}?action=parse&format=json&redirects=1&prop=sections"
             f"&page=" + urllib.parse.quote(title))
        d, _ms, _reach, _st = _json(u, "application/json", (8, 40))
        secs = (d.get("parse") or {}).get("sections")
        if secs is None:
            out[code] = {"title": title, "sec": "", "n": 0, "items": [],
                         "why": "章节表失败：" + str((d.get("error") or {}).get("info", ""))[:80]}
            continue
        heads = [re.sub(r"\s+", " ", s.get("line", "")).strip() for s in secs]
        sec = next((s for s in secs if SYM_HEAD.search(s.get("line", ""))), None)
        if not sec:
            out[code] = {"title": title, "sec": "", "n": 0, "items": [],
                         "why": "无症状章节", "heads": heads[:8]}
            time.sleep(0.2)
            continue
        u = (f"{ZH_API}?action=parse&format=json&redirects=1&prop=text&disableeditsection=1"
             f"&page=" + urllib.parse.quote(title) + "&section=" + str(sec["index"]))
        d, _ms, _reach, _st = _json(u, "application/json", (8, 60))
        html = (d.get("parse") or {}).get("text", {}).get("*", "")
        items = _wiki_items(html) if html.strip() else []
        out[code] = {"title": title, "sec": sec["line"], "n": len(items), "items": items,
                     "why": "" if items else "有症状章节但无清单（散文）"}
        time.sleep(0.2)
    return out


def _match(lex: list[tuple[str, str]], items: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """词典去贴 PDQ 原文：一条命中的中文按字典序取第一个（目测那趟同一规则）。

    刻意不按词长择优：子串匹配本来就偏爱长别名，再叠一层"选最长的中文"会让目测过的
    「原文 → 中文」配对键失效，`ZH_JUDGE` 就退化成一堆"未判"。
    """
    out = []
    for code, t in items:
        s = t.lower()
        got = {zh for name, zh in lex if name in s}
        if got:
            out.append((code, t, sorted(got)[0]))
    return out


def probe(offline: bool = False) -> ProbeResult:
    _TRACE.clear()
    items = _pdq_items()
    uniq = sorted({t for _c, t in items})
    n_short = sum(1 for t in uniq if len(t.split()) <= 2)
    n_long = sum(1 for t in uniq if len(t.split()) >= 5)
    ceiling = round(n_short / max(len(uniq), 1) * 100, 1)

    if offline:
        d = raw.newest_dir(SOURCE, requires="dict.json")
        if not d:
            raise SystemExit(f"缺 {SOURCE} 归档：先不带 --offline 跑一次")
        blob = {n: json.loads((d / n).read_text(encoding="utf-8"))
                for n in ("dict.json", "search.json", "wiki.json")}
        ent, search, wiki = blob["dict.json"], blob["search.json"], blob["wiki.json"]
        reach, http, ms, raw_path = "offline", None, None, raw.rel(d)
    else:
        t0 = time.perf_counter()
        ent, _sparql_ms = _dict_route()
        search = _search_route()
        wiki = _wiki_route(_wiki_titles())
        ms = int((time.perf_counter() - t0) * 1000)
        # 三态只按"这趟真正用上的那批响应"算：每台机直连 Wikimedia 都会先记一次 blocked，
        # 把失败尝试一起数就会在 proxy 跑通的趟上报出 blocked；写死成 proxy 又等于
        # 替 scheduler 做它该自己看的判断
        used = [r for r, s in _TRACE if s == 200]
        reach = max(set(used), key=used.count) if used else "error"
        http = 200 if used else next((s for _r, s in _TRACE if s), None)
        key_dir = raw.archive_dir(SOURCE, f"zh-{today()}")
        for name, obj in (("dict.json", ent), ("search.json", search), ("wiki.json", wiki)):
            (key_dir / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        raw_path = raw.rel(key_dir)

    # —— 路线①：子树词典贴 PDQ 原文
    lex = [(name.lower(), row["zh"])
           for row in ent.values() if row.get("zh")
           for name in ([row["en"]] + list(row.get("alt", [])))
           if len(name) >= 3]
    ents_n, zh_n = len(ent), sum(1 for v in ent.values() if v.get("zh"))
    matched = _match(lex, items)
    tally = {"对": 0, "泛": 0, "错": 0, "未判": 0}
    for _c, t, zh in matched:
        tally[ZH_JUDGE.get(f"{t.lower()}|{zh}", "未判")] += 1
    judged = sum(v for k, v in tally.items() if k != "未判")
    prec = round(tally["对"] / max(judged, 1) * 100, 1)
    cov1 = round(len(matched) / max(len(items), 1) * 100, 1)
    seen_ex: set[tuple[str, str]] = set()
    bad_examples = []
    for _c, t, z in matched:
        if ZH_JUDGE.get(f"{t.lower()}|{z}") == "错" and (t, z) not in seen_ex:
            seen_ex.add((t, z))
            bad_examples.append({"pdq": t, "zh": z})
    bad_examples = bad_examples[:4]

    # —— 路线②：逐条查
    hit_q = sum(1 for v in search.values() if v["qid"])
    hit_zh = sum(1 for v in search.values() if v.get("zh"))
    no_zh = [t for t, v in search.items() if v["qid"] and not v.get("zh")]
    miss = [t for t, v in search.items() if not v["qid"]]
    anemia = search.get("anemia", {})

    # —— 路线③：中文维基自己的清单，以及与 WHO 的并集
    # 达标按目测后的真症状条数判，不按解析条数：colorectum 解析回来 17 条、真症状只有 4 条，
    # 照解析数判就是把分期定义当症状喂进覆盖度矩阵
    wiki_real = {c: WIKI_EYEBALL.get(c, (v["n"], ""))[0] for c, v in wiki.items()}
    wiki_ok = sorted((c for c in wiki if wiki_real[c] >= MIN_SYM), key=lambda c: -wiki_real[c])
    wiki_prose = sorted(c for c in wiki if c not in wiki_ok and wiki[c].get("sec"))
    wiki_none = sorted(c for c in wiki if c not in wiki_ok and not wiki[c].get("sec"))
    zh_list = sorted(set(wiki_ok) | set(WHO_ZH_LIST))

    verdict = "ok" if (prec >= MIN_PRECISION and len(zh_list) >= MIN_DISEASES) else "partial"

    msg = (
        f"达标 {len(zh_list)}/18（判据＝{CRITERIA}）。三条路都不过：中文症状名没有可匿名取回的公开路。"
        f"① Wikidata 子树词典（{SYM_ROOT} 往下 wdt:P279*）：实体 {ents_n} 个、带 zh label {zh_n} 个，"
        f"贴到 PDQ 条目 {len(matched)}/{len(items)} = {cov1}%——但这 {cov1}% 是假的："
        f"逐条目测 {judged} 条只有 对 {tally['对']} / 泛 {tally['泛']} / 错 {tally['错']}"
        f"（未判 {tally['未判']}），precision {prec}% 对判据线 ≥{MIN_PRECISION:.0f}%。"
        "错的是另一个概念的译名（" + "、".join(f"{b['pdq'][:28]}→{b['zh']}" for b in bad_examples) + "），"
        "泛的是只翻出句里的泛词（所有 *pain→疼痛、所有 swelling→水肿），症状身份丢掉。"
        "根因：label/alias 是实体名不是翻译记忆，拿去匹配临床散文，命中的永远是含泛词的"
        "最长别名实体——而错出来的中文在 UI 上看着完全正常，这是最坏的一种失败。"
        f"② 拿 PDQ 原文逐条查的天花板 {ceiling}%：去重写法 {len(uniq)} 个里 ≤2 词仅 {n_short} 个、"
        f"≥5 词占 {n_long} 个，Wikidata 查词条不查短语。{len(SEARCH_TERMS)} 个短词样本"
        f"（已是「最顺利的情况」）命中实体 {hit_q}、带 zh label {hit_zh}，"
        f"未命中 {miss or '无'}，命中却没中文标签的 {no_zh or '无'}；"
        f"anemia 命中的是 {anemia.get('qid') or '?'}，中文「{anemia.get('zh') or '无'}」——一个蕨类科。"
        "languages=zh 取回的还是繁简混排（頭暈 与 黄疸 同屏）。"
        f"③ 中文维基条目自己的症状章节：≥{MIN_SYM} 条真症状的只有 {len(wiki_ok)} 病（"
        + "、".join(f"{c} {wiki_real[c]} 条" for c in wiki_ok) + "），"
        "命中的中文本身全对，但解析条数虚高——colorectum 解析 17 条里 13 条是分期定义、"
        "myeloma 的章节名本身就是「症狀及併發症」；"
        f"而且那是**另一份来源的另一份清单**，不是 PDQ 那 {len(items)} 条的中文名。"
        f"另 {len(wiki_prose)} 病有症状章节却写成散文（{', '.join(wiki_prose) or '无'}），"
        f"规则解析一条取不到——要抠得上中文分句 + 症状判定的 L3，而 L3 已随 B6 撤掉；"
        f"{len(wiki_none)} 病连章节都没有（{', '.join(wiki_none) or '无'}）。"
        f"④ 并集才等于「有现成中文症状清单的病」：WHO 中文版 {len(WHO_ZH_LIST)} 病"
        f"（{', '.join(WHO_ZH_LIST)}，禁商用）∪ 维基 {len(wiki_ok)} 病 = {len(zh_list)}/18。"
        "裁定：不做翻译列。symptom 按 (source_id, disease_code, name, name_lang) 落，"
        "PDQ 行 name_lang='en'、WHO 中文版行 'zh'，缺就空——它们是两份不同来源的观察，"
        "压成同一行的 name + name_zh 会假装是同一份。"
        "口径三处：本探针的分母是 PDQ 归档的症状条目（不自己重下 PDQ，所以跑它之前要先有"
        "nci_pdq_html 的归档）；titles 解析必须把 normalized/converted/redirects 三种边都走一遍"
        "再喂 action=parse（它不认 converttitles），只按字面标题匹配会让 宫颈癌/甲状腺癌/肾癌/"
        "多发性骨髓瘤 稳定 missingtitle、被记成「没有症状章节」；这一支没有上游版本戳，"
        "故不写 dataset_release——Wikidata 天天在改但那不叫一次发布，登记进去只会每天冒一个假版本。"
    )

    sample = [{"code": c, "pass": c in zh_list, "zh_list_diseases": len(zh_list),
               "wiki_items": wiki[c]["n"], "wiki_real": wiki_real[c],
               "eye_note": WIKI_EYEBALL.get(c, ("", ""))[1],
               "wiki_sec": wiki[c].get("sec", ""),
               "why": wiki[c].get("why", "")} for c in WIKI_TITLE]
    sample.append({"route_metrics": {
        "dict_precision_pct": prec, "dict_surface_cov_pct": cov1,
        "per_term_ceiling_pct": ceiling, "zhwiki_list_diseases": len(wiki_ok),
        "zh_list_union": len(zh_list), "eyeball": tally,
        "sparql_entities": ents_n, "sparql_with_zh": zh_n,
        "search_hit": hit_q, "search_with_zh": hit_zh}})
    sample.append({"wrong_examples": bad_examples})
    sample.append({"wiki_sample": {c: wiki[c]["items"][:6] for c in wiki_ok}})

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=len(matched),
        diseases_covered=len(zh_list),
        diseases_total=len(TARGETS),
        fields_seen=["wdt:P279* subtree en/zh label + en altLabel", "wbsearchentities.id",
                     "wbgetentities.labels.zh", "zh.wikipedia parse.sections.line",
                     "zh.wikipedia section <li>/<dd>", "PDQ pages.json items.text"],
        sample=sample,
        raw_path=raw_path,
        reachability=reach,
        http_status=http,
        latency_ms=ms,
        dataset_code=DATASET,
    )
