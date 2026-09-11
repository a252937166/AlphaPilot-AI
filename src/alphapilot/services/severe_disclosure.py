"""Rule-based recognition of severe negative regulatory disclosures.

Only a few CNInfo announcement families carry a large, reproducible negative
drift after publication: the start of a CSRC investigation, an advance notice
of an administrative penalty, and delisting-risk notices. Their titles follow
fixed formulas, so a handful of title rules identify them without any model.
Routine statements that merely mention penalties ("最近五年未被处罚"), inquiry
letters, lawsuits and the *lifting* of a risk warning are deliberately excluded.

Evidence (CNInfo, 2026-08-03..25, returns from the first tradable open, excess
versus the all-market median): investigation notices 15 events / 8 companies,
5-session −8.3%, 20-session excess −28.6%, 80% down; advance penalty notices 6
events, 5-session −6.3%; delisting-risk notices 19 events, 20-session excess
−14.3%; the formal penalty decision itself ≈ 0, being priced at the advance
notice. This is an avoid/sell screen, not a buy signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SEVERE_EVENT_TYPE = "regulatory_severe"

# Anything matching these is not the event itself: routine compliance
# statements, the lifting of a warning, lawsuits, opinions about someone else.
_EXCLUDE = re.compile(
    r"最近五年|不存在被|未被|未受到|未曾|无违法违规|承诺|撤销|解除|自查|"
    r"法律意见书|律师事务所|会计师事务所|回复|诉讼|仲裁|案件受理|受理立案|"
    r"公开发行|申请文件|募集说明书|问询函|"
    # A listing that ends because the company is absorbed in a merger is not a
    # delisting risk; a penalty on a director for matters unrelated to the
    # company is not a penalty on the company.
    r"吸收合并|换股|非本公司事项"
)


@dataclass(frozen=True, slots=True)
class SevereDisclosure:
    """A recognised severe disclosure with its registered event shape."""

    subtype: str
    label: str
    direction: float
    strength: float
    keyword: str


# Ordered by severity: the first matching rule wins. ``strength`` below 0.6
# stays out of notifications (see services.notifications.push_event); the
# formal penalty decision is recorded for the event stream only.
_RULES: tuple[tuple[str, str, float, float, re.Pattern[str]], ...] = (
    (
        "investigation",
        "立案调查",
        -1.0,
        0.9,
        re.compile(r"立案告知书|被立案|立案调查|立案侦查|立案审查"),
    ),
    (
        "delisting_risk",
        "退市风险",
        -0.9,
        0.8,
        re.compile(
            r"退市风险警示|终止上市风险|可能被终止上市|可能因.{0,12}终止上市|"
            r"强制退市|终止上市的风险提示|终止上市暨"
        ),
    ),
    (
        "penalty_notice",
        "行政处罚事先告知",
        -0.8,
        0.7,
        re.compile(r"处罚事先告知书|行政处罚事先告知"),
    ),
    (
        "penalty_decision",
        "行政处罚决定",
        -0.4,
        0.4,
        re.compile(r"行政处罚决定书|处罚决定书"),
    ),
)


def classify_severe_disclosure(title: str | None) -> SevereDisclosure | None:
    """Return the severe-disclosure shape for a CNInfo title, or None.

    Routine mentions of penalties and every excluded family return None even
    when a severe keyword is present, so a "最近五年未被处罚" statement or the
    lifting of a delisting-risk warning never becomes a negative event.
    """

    if not title:
        return None
    text = re.sub(r"\s+", "", title)
    if _EXCLUDE.search(text):
        return None
    for subtype, label, direction, strength, pattern in _RULES:
        match = pattern.search(text)
        if match:
            return SevereDisclosure(
                subtype=subtype,
                label=label,
                direction=direction,
                strength=strength,
                keyword=match.group(0),
            )
    return None


def severe_event_summary(disclosure: SevereDisclosure, title: str) -> str:
    """Human-readable summary stored with the event."""

    return f"规则识别“{disclosure.keyword}”（{disclosure.label}）：{title}"[:200]
