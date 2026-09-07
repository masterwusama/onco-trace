"""P0 覆盖度基准：18 个恶性肿瘤。

探针的 `diseases_covered / diseases_total` 分母就是这个清单，所以它必须是
仓库里的一份声明式配置，而不是散在各个探针脚本里的字符串——否则每个源的
"覆盖了几个病"口径不一致，矩阵没法横向比。

范围先只做恶性肿瘤：ICD-O-3 与 SEER/GBD 的口径在肿瘤上最规整，生存率有成熟同源数据。
非肿瘤高致死病（缺血性心脏病、卒中、COPD、阿尔茨海默、糖尿病、CKD）等 P0 结论出来再排，
它们缺分期与同源生存率数据，`stat_cohort.stage` 维度会大面积为 NA。

字段说明：
  code      仓库内短码，同时是前端 slug
  icd10     GBD / WHO / 中国统计年鉴都按 ICD-10 归口，是跨源对齐的第一把钥匙
  icdo3     ICD-O-3 拓扑码段，器官树的挂载依据
  icd9      ICD-9-CM 前缀（可 3 位或 4 位，逗号分隔）。加它不是为了做美国老数据，
            而是 2026-09-01 版 MONDO 实测：MONDO 只把 ICD 码挂在亚部位/组织学粒度的
            term 上（bronchus cancer 带 ICD9:162.3），而 grouping 父类才挂 ICD10CM:C34。
            按 ICD-10 去命中会稳定落到上一层分类词，器官树就挂到"呼吸系统"而不是"肺"。
            ICD-9 能落到正确粒度，所以用它把亚部位 term 捞出来当器官/组织学下钻的子节点。
            位数取舍按 icd10 的范围收窄：C34 不含气管（C33），所以肺给 162.2/3/4/5/8/9
            而把 162.0 排除在外；C56 只是卵巢，所以只给 183.0 而不是整章 183。
  category  histology 与生存率口径的粗分类
  sex       both/female/male。声明在这里而不是各个探针里，因为按性别分层的源不止一个
            （SEER Stat Facts、GBD、GLOBOCAN），器官树与反查也要知道宫颈只属于女性。
            不给默认值：漏填必须当场报错，不能悄悄落成 both——性别决定了一个源该出几张
            分性别的表，猜错的代价是把女性专属癌当成两性通用。
            性别特异癌的页面上往往没有性别标签，性别只出现在口径行里
            （SEER 的 "All Races, Females"），所以这一列同时是校验口径行的依据。
  mondo_id  疾病主条目的 MONDO ID，B2 实测后声明在这里，探针只负责校验。

            本来想让探针自动推导，做不到：MONDO 把 ICD 码全挂在亚部位 term 上，
            真正的疾病主条目一个 ICD 码都没有——colorectal cancer(MONDO:0005575) 的
            xref 只有 DOID/MEDGEN/NCIT/OMIM/Orphanet/SCTID/UMLS，
            non-Hodgkin lymphoma(MONDO:0018908) 同样没有 ICD9/ICD10CM。
            所以"按 ICD-9 捞"只能捞到 anal canal cancer、descending colon cancer
            这些子节点，捞不到结直肠癌本身。

            选取原则是语义范围对齐 icd10，不是挑 xref 最富的那个：
            子宫体用 uterine corpus cancer(C54-C55) 而不是 uterine cancer——后者把宫颈
            也包含进去（cervical cancer is_a uterine cancer）；乳腺用 female breast
            carcinoma 而不是 breast cancer——后者不分性别。
            代价是这几个主条目的 xref 偏穷（uterine corpus cancer 连 UMLS 都没有），
            这部分缺口由探针如实上报，不靠换个更宽的 term 来粉饰。
  gbd_cause GBD 病因层级里的 cause_id（`IHME_GBD_2021_HIERARCHIES` 的 Cause Hierarchy 表）。
            不靠名字匹配：GBD 的叫法与我们的 name_en 对不上（Colon and rectum cancer vs
            Colorectal），而按 ICD-10 反查又会在两处错口径——GBD 的 426 把气管和支气管肺
            并成一档（我们的 C34 不含气管 C33），417 肝癌则按肝炎/酒精/NASH 再分 6 个 L4
            亚档（五个按病因，另一个是肝母细胞瘤）。亚档是危险因素维想要的东西，所以这一列
            只声明 L3 主档，展开交给探针。
  gco_today GCO "Cancer Today"（GLOBOCAN 估算，`gco-api…/api/globocan/v3/<版本>/`）的癌种码。
  gco_time  GCO "Cancer Over Time"（登记处年度序列，`…/api/overtime/v2/<版本>/`）的癌种码。
            两列分开声明而不是共用一个：这两套码是各自独立的码空间，同一个 ICD-O-3 段落在
            两边归到不同的病——C88 在 Today 里算进 Non-Hodgkin lymphoma(34)，在 Over Time 里
            算进 Multiple myeloma(27=「C88+C90」)；Today 把结直肠单列 41(C18-C21)，Over Time
            另有 106 而同表的 Colon/Rectum 是分开的两档。所以 GBD 探针那套按病因 ID 反查的
            做法在这里不通用，两个探针各自核对各自那一列。
  search_terms 研究层（临床试验与文献）的查询词表，第一项固定是 MeSH 主题词，其余是自由文本同义词。
            B5 实测下来这一列必须有：ClinicalTrials.gov v2 与 Europe PMC 都没有可用的疾病 ID 键——
            CT 的 condition 是自由文本（`conditionsModule.meshTerm`、`derivedSection` 的 MeSH 段
            在 `fields=` 白名单里全被拒），Europe PMC 的 `MH:` 字段虽然存在但只覆盖到 3,474 篇
            （同一病自由文本 26 万篇，差两个量级），所以"按主题词检索"这条路查全率不够。
            既然只能按词查，词就得是一份声明式配置，两个探针共用同一份，否则"这一病有没有试验"
            和"这一病有没有文献"会各自用各自的词，矩阵两列没法横向比。
            两项取舍记在这里：
              · CT 只对**裸写**的第一项做 MeSH 概念展开（实测 `Lung Neoplasms` 与 `lung cancer`
                裸写都回 14,501，加引号只有 13,263），后面的自由文本同义词一律加引号，
                于是它们是真增量：18 病合计比只查主题词多并进来 2,823 项、13 病有增量
                （脑瘤一项就多 1,021）。所以这一列不能按"CT 只用第一项、其余是给 EPMC 的"来理解，
                两侧共用同一份词表是为了让矩阵两列口径可比，不是为了省词。
              · 主题词一律取"语义等于 icd10 段"的那一档，不取更宽的父词：子宫体用
                Endometrial Neoplasms 而不是 Uterine Neoplasms（后者把宫颈包进去），
                乳腺用 Breast Neoplasms。MeSH 这一档本身不分性别，而 CT 的
                `aggFilters=sex:female` 实测回 0 行（`studyType:Interventional` 同样 0 行、
                `phase:1,2` 直接 400，只有 `ages:child` 给了 900 行），EPMC 侧没有性别过滤器，
                所以男性乳腺癌那不到 1% 的份额在这一路没法剔——如实记成口径缺口，不靠换词解决。
            不收的词也记在这里：`NHL` 这种缩写会在文献库里捞进国家冰球联盟，
            `uterine cancer` 会在标题摘要里把宫颈癌算进子宫体。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    code: str
    name_zh: str
    name_en: str
    icd10: str
    icdo3: str
    icd9: str
    category: str
    sex: str
    mondo_id: str
    gbd_cause: str
    gco_today: str
    gco_time: str
    search_terms: tuple[str, ...]


TARGETS: tuple[Target, ...] = (
    Target("lung", "肺与支气管恶性肿瘤", "Lung and Bronchus", "C34", "C34.0-C34.9", "1622,1623,1624,1625,1628,1629", "carcinoma", "both", "MONDO:0008903", "426", "15", "11", ("Lung Neoplasms", "lung cancer", "bronchus cancer", "non-small cell lung carcinoma", "small cell lung carcinoma")),
    Target("colorectum", "结直肠恶性肿瘤", "Colorectal", "C18-C21", "C18.0-C21.8", "153,154", "carcinoma", "both", "MONDO:0005575", "441", "41", "106", ("Colorectal Neoplasms", "colorectal cancer", "colon cancer", "rectal cancer")),
    Target("liver", "肝与肝内胆管恶性肿瘤", "Liver and Intrahepatic Bile Duct", "C22", "C22.0-C22.1", "155", "carcinoma", "both", "MONDO:0002691", "417", "11", "7", ("Liver Neoplasms", "liver cancer", "hepatocellular carcinoma", "intrahepatic cholangiocarcinoma")),
    Target("stomach", "胃恶性肿瘤", "Stomach", "C16", "C16.0-C16.9", "151", "carcinoma", "both", "MONDO:0001056", "414", "7", "3", ("Stomach Neoplasms", "gastric cancer", "stomach cancer")),
    Target("breast_female", "女性乳腺恶性肿瘤", "Breast (Female)", "C50", "C50.0-C50.9", "174", "carcinoma", "female", "MONDO:0004379", "429", "20", "14", ("Breast Neoplasms", "breast cancer", "female breast carcinoma")),
    Target("pancreas", "胰腺恶性肿瘤", "Pancreas", "C25", "C25.0-C25.9", "157", "carcinoma", "both", "MONDO:0009831", "456", "13", "9", ("Pancreatic Neoplasms", "pancreatic cancer", "pancreatic ductal adenocarcinoma")),
    Target("esophagus", "食管恶性肿瘤", "Esophagus", "C15", "C15.0-C15.9", "150", "carcinoma", "both", "MONDO:0007576", "411", "6", "2", ("Esophageal Neoplasms", "esophageal cancer", "esophageal squamous cell carcinoma", "esophageal adenocarcinoma")),
    Target("prostate", "前列腺恶性肿瘤", "Prostate", "C61", "C61.9", "185", "carcinoma", "male", "MONDO:0008315", "438", "27", "19", ("Prostatic Neoplasms", "prostate cancer")),
    Target("cervix", "宫颈恶性肿瘤", "Cervix Uteri", "C53", "C53.0-C53.9", "180", "carcinoma", "female", "MONDO:0002974", "432", "23", "16", ("Uterine Cervical Neoplasms", "cervical cancer", "cervix uteri cancer")),
    Target("ovary", "卵巢恶性肿瘤", "Ovary", "C56", "C56.9", "1830", "carcinoma", "female", "MONDO:0008170", "465", "25", "18", ("Ovarian Neoplasms", "ovarian cancer", "ovary cancer")),
    Target("thyroid", "甲状腺恶性肿瘤", "Thyroid", "C73", "C73.9", "193", "carcinoma", "both", "MONDO:0002108", "480", "32", "24", ("Thyroid Neoplasms", "thyroid cancer")),
    Target("bladder", "膀胱恶性肿瘤", "Urinary Bladder", "C67", "C67.0-C67.9", "188", "carcinoma", "both", "MONDO:0001187", "474", "30", "22", ("Bladder Neoplasms", "bladder cancer", "urinary bladder cancer")),
    Target("kidney", "肾与肾盂恶性肿瘤", "Kidney and Renal Pelvis", "C64-C65", "C64.9-C65.9", "1890,1891", "carcinoma", "both", "MONDO:0002367", "471", "29", "21", ("Kidney Neoplasms", "kidney cancer", "renal cell carcinoma", "renal pelvis cancer")),
    Target("brain", "脑与神经系统恶性肿瘤", "Brain and Other Nervous System", "C70-C72", "C70.0-C72.9", "191,192", "carcinoma", "both", "MONDO:0001657", "477", "31", "23", ("Brain Neoplasms", "brain cancer", "brain tumor", "nervous system cancer")),
    Target("uterus", "子宫体恶性肿瘤", "Uterus (Corpus)", "C54-C55", "C54.0-C55.9", "179,182", "carcinoma", "female", "MONDO:0006003", "435", "24", "17", ("Endometrial Neoplasms", "endometrial cancer", "uterine corpus cancer", "corpus uteri cancer")),
    Target("leukemia", "白血病", "Leukemia", "C91-C95", "C42.0-C42.4", "204,205,206,207,208", "heme", "both", "MONDO:0005059", "487", "36", "28", ("Leukemia", "leukaemia", "acute myeloid leukemia", "chronic lymphocytic leukemia")),
    Target("nhl", "非霍奇金淋巴瘤", "Non-Hodgkin Lymphoma", "C82-C85,C88,C96", "C42.0-C42.4", "200,202", "heme", "both", "MONDO:0018908", "485", "34", "26", ("Lymphoma, Non-Hodgkin", "non-hodgkin lymphoma", "follicular lymphoma", "diffuse large B-cell lymphoma")),
    Target("myeloma", "多发性骨髓瘤", "Myeloma", "C88,C90", "C42.0-C42.4", "203", "heme", "both", "MONDO:0009693", "486", "35", "27", ("Multiple Myeloma", "myeloma")),
)

BY_CODE = {t.code: t for t in TARGETS}


def codes() -> list[str]:
    return [t.code for t in TARGETS]
