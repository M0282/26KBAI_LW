"""데모 판독 결과 저장소 — API 키 없이도 시연이 돌아가게 한다.

왜 필요한가: 제출본을 받은 사람은 Anthropic API 키가 없다. 키 검사에 막혀
화면조차 못 보면 프로토타입을 평가할 수 없다. 그렇다고 우리 키를 제출물에
넣을 수는 없다(저장소에 키 노출·비용·서로 다른 결과).

그래서 데모 서류의 **판독 결과**를 미리 저장해 함께 배포한다.
키가 없으면 저장된 결과를 재생해 전체 검증 흐름을 그대로 보여준다.

키를 프롬프트가 아니라 **파일 내용 해시**로 잡는 이유:
프롬프트 기반 키는 PDF 파서 버전이 바뀌어 추출 텍스트가 한 글자만 달라져도
빗나간다. 파일 해시는 배포 환경이 달라도 동일하다.

저장 대상은 최종 ExtractionResult(문서유형 + 필드)다. 좌표·하이라이트는
로컬 파싱으로 얻으므로 키가 없어도 동작한다.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from src.common.schemas import ParsedField

STORE_DIR = Path(os.environ.get("DEMO_RESULT_DIR", "data/demo_results"))


def content_key(raw_bytes: bytes) -> str:
    """파일 내용 기준 키. 같은 파일이면 어느 환경에서도 같은 값."""
    return hashlib.sha256(raw_bytes).hexdigest()


def save_result(raw_bytes: bytes, file_name: str, result) -> Path:
    """판독 결과를 저장한다(키를 가진 사람이 사전 실행할 때 사용)."""
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = STORE_DIR / f"{content_key(raw_bytes)}.json"
    path.write_text(
        json.dumps(
            {
                "file_name": file_name,
                "doc_type": result.doc_type,
                "fields": [
                    {
                        "name": f.name,
                        "value": f.value,
                        "page": f.page,
                        "confidence": f.confidence,
                    }
                    for f in result.fields
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def load_result(raw_bytes: bytes):
    """저장된 판독 결과를 ExtractionResult로 복원. 없으면 None."""
    path = STORE_DIR / f"{content_key(raw_bytes)}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    from src.parser.financial_extractor import ExtractionResult

    fields = [
        ParsedField(
            name=item["name"],
            value=item.get("value"),
            page=item.get("page"),
            confidence=item.get("confidence"),
        )
        for item in payload.get("fields", [])
    ]
    return ExtractionResult(
        doc_type=payload.get("doc_type", "unknown"),
        fields=fields,
        used_llm=True,  # 원본은 LLM으로 판독한 결과다
    )


def available_count() -> int:
    return len(list(STORE_DIR.glob("*.json"))) if STORE_DIR.exists() else 0
