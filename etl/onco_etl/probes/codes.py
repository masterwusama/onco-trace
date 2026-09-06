"""编码展开：把 SEER、MONDO、targets.py 三处不同写法的码段变成可相交的集合。

同一个 ICD-O-3 拓扑码，SEER 写成 `C160-C166,C168-C169`（无小数点），
targets.py 写成 `C16.0-C16.9`（有小数点），ICD-10 又是 `C18-C21`（两位章节）。
不统一就没法做"这个病能不能挂到那个器官分组"的判定，而这类判定一旦写错，
错的是静默的：挂载率看起来是 100%，其实全挂到了错的节点上。
"""
from __future__ import annotations


def expand_topo(spec: str) -> set[int]:
    """ICD-O-3 拓扑码 → C 码后三位整数的集合。

    必须先按 '-' 拆开再逐侧校验：整段 'C160-C166' 拿去 isdigit() 一定是 False，
    那样所有区间写法都会被静默跳过，展开成空集，挂载率直接归零。

    展开出 C190-C199 这类实际不存在的码是无害的：真值集在 SEER 那一侧，
    这里只做相交，多余元素不会凭空造出匹配。
    """
    out: set[int] = set()
    for part in spec.replace(".", "").upper().replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        if not (lo.startswith("C") and lo[1:].isdigit()):
            continue
        if not hi:
            out.add(int(lo[1:]))
        elif hi.startswith("C") and hi[1:].isdigit():
            out.update(range(int(lo[1:]), int(hi[1:]) + 1))
    return out


def expand_icd10(spec: str) -> set[str]:
    """ICD-10 → 章节前缀集合，如 'C18-C21' → {'C18','C19','C20','C21'}。

    ICD-10 的层级不等长（C34 与 C88 都是三位章节，但 C7 不是合法前缀），
    所以按字母 + 两位数字展开，够覆盖肿瘤章节；展开出来的前缀用 startswith 匹配，
    'C18' 能同时命中 'C18'、'C180'（即 C18.0）与 'C189'。
    """
    out: set[str] = set()
    for part in spec.upper().replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            letter, d1, d2 = a[0], int(a[1:]), int(b[1:])
            out.update(f"{letter}{n:02d}" for n in range(d1, d2 + 1))
        elif part:
            out.add(part)
    return out


def icd10_matches(code: str, prefixes: set[str]) -> bool:
    """MONDO 的 xref 形如 'C34.9'，去小数点后与前缀集合比。"""
    c = code.upper().replace(".", "").strip()
    return any(c.startswith(p) for p in prefixes)


def expand_icd9(spec: str) -> set[str]:
    """ICD-9-CM → 前缀集合。允许 3 位章节（'153'）和 4 位亚目（'1622'）混用。

    需要 4 位是因为 ICD-9 的章节比 ICD-10 粗：162 章含气管（162.0，对应 C33），
    而 targets 里肺的口径是 C34，不收气管。整章前缀会把气管癌当成肺癌的候选 term，
    器官树就挂错了——这类错法在报告里表现为"命中率很高"，比命中不了更难发现。
    """
    return {
        p.strip()
        for p in spec.replace(".", "").replace(" ", "").split(",")
        if p.strip().isdigit()
    }


def icd9_matches(code: str, prefixes: set[str]) -> bool:
    """MONDO 写 '162.3'，去小数点后按前缀比，与 icd10_matches 同一套语义。"""
    c = code.replace(".", "").strip()
    return any(c.startswith(p) for p in prefixes)
