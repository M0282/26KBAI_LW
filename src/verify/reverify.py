"""재검증 diff — 규정/정책이 바뀐 뒤 판정이 어떻게 변했는지 비교.

규정 개정 감지(src/ingest/regulation_version.py) 후, 개정 전/후 검증 결과를
diff 하여 '신규 위반 / 해소 / 상태 변화'를 산출한다. 발표에서
"규정이 개정되니 이 서류가 새로 위반이 됩니다"를 보여주는 근거.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.common.schemas import CheckStatus, RuleCheck

_BLOCKING = (CheckStatus.RISK, CheckStatus.MISSING)


@dataclass
class ReverifyDiff:
    added: list[RuleCheck]              # 개정 후 새로 발생한 위반·누락
    resolved: list[RuleCheck]           # 개정 후 해소된 위반·누락
    changed: list[tuple[RuleCheck, RuleCheck]]  # (개정 전, 개정 후) 상태 변화

    @property
    def has_change(self) -> bool:
        return bool(self.added or self.resolved or self.changed)

    def summary_line(self) -> str:
        if not self.has_change:
            return "규정 개정 재검증: 판정 변화 없음."
        return (
            f"규정 개정 재검증: 신규 위반 {len(self.added)}건, "
            f"해소 {len(self.resolved)}건, 상태 변화 {len(self.changed)}건."
        )


def diff_checks(old: list[RuleCheck], new: list[RuleCheck]) -> ReverifyDiff:
    """동일 서류 패키지의 개정 전(old) / 후(new) 검증 결과를 비교."""
    old_by = {c.rule_id: c for c in old}
    new_by = {c.rule_id: c for c in new}
    added: list[RuleCheck] = []
    resolved: list[RuleCheck] = []
    changed: list[tuple[RuleCheck, RuleCheck]] = []

    for rule_id, nc in new_by.items():
        oc = old_by.get(rule_id)
        if oc is None:
            if nc.status in _BLOCKING:
                added.append(nc)
            continue
        if oc.status == nc.status:
            continue
        was_block = oc.status in _BLOCKING
        now_block = nc.status in _BLOCKING
        if now_block and not was_block:
            added.append(nc)
        elif was_block and not now_block:
            resolved.append(nc)
        else:
            changed.append((oc, nc))

    for rule_id, oc in old_by.items():
        if rule_id not in new_by and oc.status in _BLOCKING:
            resolved.append(oc)

    return ReverifyDiff(added=added, resolved=resolved, changed=changed)
