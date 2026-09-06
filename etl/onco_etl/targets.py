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
  category  histology 与生存率口径的粗分类
  mondo_id  留空。ID 主干由 B2 的 MONDO 探针回填，不在这里手填——
            手填的外部 ID 一旦写错，后面所有源的关联都会静默错位
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
    category: str
    mondo_id: str | None = None


TARGETS: tuple[Target, ...] = (
    Target("lung", "肺与支气管恶性肿瘤", "Lung and Bronchus", "C34", "C34.0-C34.9", "carcinoma"),
    Target("colorectum", "结直肠恶性肿瘤", "Colorectal", "C18-C21", "C18.0-C21.8", "carcinoma"),
    Target("liver", "肝与肝内胆管恶性肿瘤", "Liver and Intrahepatic Bile Duct", "C22", "C22.0-C22.1", "carcinoma"),
    Target("stomach", "胃恶性肿瘤", "Stomach", "C16", "C16.0-C16.9", "carcinoma"),
    Target("breast_female", "女性乳腺恶性肿瘤", "Breast (Female)", "C50", "C50.0-C50.9", "carcinoma"),
    Target("pancreas", "胰腺恶性肿瘤", "Pancreas", "C25", "C25.0-C25.9", "carcinoma"),
    Target("esophagus", "食管恶性肿瘤", "Esophagus", "C15", "C15.0-C15.9", "carcinoma"),
    Target("prostate", "前列腺恶性肿瘤", "Prostate", "C61", "C61.9", "carcinoma"),
    Target("cervix", "宫颈恶性肿瘤", "Cervix Uteri", "C53", "C53.0-C53.9", "carcinoma"),
    Target("ovary", "卵巢恶性肿瘤", "Ovary", "C56", "C56.9", "carcinoma"),
    Target("thyroid", "甲状腺恶性肿瘤", "Thyroid", "C73", "C73.9", "carcinoma"),
    Target("bladder", "膀胱恶性肿瘤", "Urinary Bladder", "C67", "C67.0-C67.9", "carcinoma"),
    Target("kidney", "肾与肾盂恶性肿瘤", "Kidney and Renal Pelvis", "C64-C65", "C64.9-C65.9", "carcinoma"),
    Target("brain", "脑与神经系统恶性肿瘤", "Brain and Other Nervous System", "C70-C72", "C70.0-C72.9", "carcinoma"),
    Target("uterus", "子宫体恶性肿瘤", "Uterus (Corpus)", "C54-C55", "C54.0-C55.9", "carcinoma"),
    Target("leukemia", "白血病", "Leukemia", "C91-C95", "C42.0-C42.4", "heme"),
    Target("nhl", "非霍奇金淋巴瘤", "Non-Hodgkin Lymphoma", "C82-C85,C88,C96", "C42.0-C42.4", "heme"),
    Target("myeloma", "多发性骨髓瘤", "Myeloma", "C88,C90", "C42.0-C42.4", "heme"),
)

BY_CODE = {t.code: t for t in TARGETS}


def codes() -> list[str]:
    return [t.code for t in TARGETS]
