"""Study design from PubMed publication types. A rough guide, not a quality appraisal."""

from __future__ import annotations

from .models import Evidence

META = Evidence("meta_analysis", 1, "Systematic review / meta-analysis", "系统综述 / 荟萃分析")
GUIDELINE = Evidence("guideline", 1, "Clinical guideline", "临床指南")
RCT = Evidence("rct", 2, "Randomized controlled trial", "随机对照试验")
TRIAL = Evidence("clinical_trial", 3, "Clinical trial (not randomized)", "临床试验（非随机）")
OBSERVATIONAL = Evidence("observational", 4, "Observational study", "观察性研究")
CASE = Evidence("case_report", 5, "Case report", "病例报告")
REVIEW = Evidence("narrative_review", 5, "Narrative review", "叙述性综述")
OPINION = Evidence("opinion", 6, "Editorial / comment / letter", "社论 / 评论 / 通信")
ANIMAL = Evidence("animal", 6, "Animal study", "动物研究")
UNKNOWN = Evidence("unknown", 9, "Not classified", "未分类")

_BY_TYPE = [
    (META, {"Meta-Analysis", "Systematic Review"}),
    (GUIDELINE, {"Practice Guideline", "Guideline", "Consensus Development Conference"}),
    (RCT, {"Randomized Controlled Trial", "Pragmatic Clinical Trial", "Equivalence Trial"}),
    (
        TRIAL,
        {
            "Clinical Trial",
            "Controlled Clinical Trial",
            "Clinical Trial, Phase I",
            "Clinical Trial, Phase II",
            "Clinical Trial, Phase III",
            "Clinical Trial, Phase IV",
        },
    ),
    (OBSERVATIONAL, {"Observational Study", "Twin Study"}),
    (CASE, {"Case Reports"}),
    (REVIEW, {"Review"}),
    (OPINION, {"Editorial", "Comment", "Letter"}),
]


def evidence_level(pub_types: list[str], humans: bool, animals: bool) -> Evidence:
    types = set(pub_types)
    if animals and not humans:
        return ANIMAL
    for evidence, names in _BY_TYPE:
        if types & names:
            return evidence
    return UNKNOWN
