"""예선 제출용 기술설명서(PPTX) 생성.

평가 배점에 맞춰 슬라이드를 배치한다.
  문제해결능력 50점 = 문제정의15 + 활용가능성15 + 창의성·효과20
  기술적완성도 50점 = 기술적정성20 + 개발계획구체성15 + 기술실현가능성15

숫자는 전부 이 저장소에서 실측한 값이다(추정치를 쓰지 않는다).
슬라이드 디자인은 파워포인트에서 다듬을 것을 전제로, 내용과 구조에 집중한다.

실행:
    py -3 -m pip install python-pptx
    py -3 -m scripts.make_deck
결과: docs/기술설명서_초안.pptx
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

OUT = Path("docs/기술설명서_초안.pptx")
SHOTS = Path("docs/screens")
# 캡처 원본은 3200x2400(사이드바 포함). 슬라이드에는 본문만 크게 싣는다.
SIDEBAR_FRACTION = 0.19

KB_YELLOW = RGBColor(0xFC, 0xAF, 0x17)
KB_GRAY = RGBColor(0x64, 0x5B, 0x4C)
INK = RGBColor(0x2B, 0x2B, 0x2B)
MUTED = RGBColor(0x6B, 0x6B, 0x6B)
RISK = RGBColor(0xC6, 0x28, 0x28)
OK = RGBColor(0x2E, 0x7D, 0x32)
BG_SOFT = RGBColor(0xFF, 0xF8, 0xDF)
LINE = RGBColor(0xE6, 0xE0, 0xD4)

W, H = Inches(13.333), Inches(7.5)


def _textbox(slide, left, top, width, height):
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    return frame


def _para(frame, text, *, size=16, bold=False, color=INK, space_after=6,
          level=0, first=False, align=PP_ALIGN.LEFT):
    para = frame.paragraphs[0] if first else frame.add_paragraph()
    para.alignment = align
    para.level = level
    para.space_after = Pt(space_after)
    run = para.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = "맑은 고딕"
    return para


def _slide(prs, title, subtitle=None, tag=None):
    """제목 띠가 있는 빈 슬라이드."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bar = slide.shapes.add_shape(1, 0, 0, W, Inches(0.09))
    bar.fill.solid()
    bar.fill.fore_color.rgb = KB_YELLOW
    bar.line.fill.background()

    frame = _textbox(slide, Inches(0.62), Inches(0.34), Inches(11.4), Inches(1.0))
    _para(frame, title, size=30, bold=True, color=KB_GRAY, first=True, space_after=2)
    if subtitle:
        _para(frame, subtitle, size=14, color=MUTED)
    if tag:
        chip = slide.shapes.add_shape(5, Inches(10.55), Inches(0.36), Inches(2.15), Inches(0.42))
        chip.fill.solid()
        chip.fill.fore_color.rgb = BG_SOFT
        chip.line.color.rgb = KB_YELLOW
        tf = chip.text_frame
        tf.word_wrap = True
        _para(tf, tag, size=11, bold=True, color=KB_GRAY, first=True,
              align=PP_ALIGN.CENTER, space_after=0)
    return slide


def _card(slide, left, top, width, height, heading, lines, *, accent=KB_YELLOW):
    box = slide.shapes.add_shape(5, left, top, width, height)
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    box.line.color.rgb = LINE
    strip = slide.shapes.add_shape(1, left, top, Inches(0.06), height)
    strip.fill.solid()
    strip.fill.fore_color.rgb = accent
    strip.line.fill.background()

    frame = _textbox(slide, left + Inches(0.28), top + Inches(0.16),
                     width - Inches(0.5), height - Inches(0.3))
    _para(frame, heading, size=15, bold=True, color=KB_GRAY, first=True, space_after=7)
    for line in lines:
        _para(frame, line, size=12, color=INK, space_after=5)


def _table(slide, left, top, width, rows, *, col_widths=None, head_size=12, body_size=11):
    shape = slide.shapes.add_table(len(rows), len(rows[0]), left, top, width,
                                   Inches(0.36) * len(rows))
    table = shape.table
    if col_widths:
        for i, cw in enumerate(col_widths):
            table.columns[i].width = Emu(int(cw * 914400))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            cell.text = ""
            frame = cell.text_frame
            frame.word_wrap = True
            _para(frame, str(value), size=head_size if r == 0 else body_size,
                  bold=(r == 0), color=KB_GRAY if r == 0 else INK, first=True, space_after=0)
            cell.fill.solid()
            cell.fill.fore_color.rgb = BG_SOFT if r == 0 else RGBColor(0xFF, 0xFF, 0xFF)
    return table


def _shot(slide, left, top, width, height, name, label, *, keep_sidebar=False, focus=0.0):
    """실제 캡처 화면을 넣는다. 파일이 없으면 자리표시자로 대체한다.

    캡처는 4:3인데 슬라이드 자리는 가로로 길다. 이미지를 늘이면 글자가 뭉개지므로
    비율을 지킨 채 잘라 넣는다(python-pptx의 crop_*는 원본 대비 비율).
    focus: 0이면 위쪽, 1이면 아래쪽을 남긴다.
    """
    path = SHOTS / name
    if not path.exists():
        _placeholder(slide, left, top, width, height, f"{label}\n(캡처 없음 — py -3.12 -m scripts.capture_screens)")
        return None

    picture = slide.shapes.add_picture(str(path), left, top, width=width, height=height)
    crop_left = 0.0 if keep_sidebar else SIDEBAR_FRACTION
    native_ratio = (picture.image.size[0] * (1 - crop_left)) / picture.image.size[1]
    target_ratio = width / height
    # 남길 세로 비율 = 가로 비율 / 목표 비율. 1을 넘으면 자를 것이 없다.
    keep = min(1.0, native_ratio / target_ratio)
    drop = 1.0 - keep
    picture.crop_left = crop_left
    picture.crop_top = drop * focus
    picture.crop_bottom = drop * (1 - focus)

    frame_box = slide.shapes.add_shape(5, left, top, width, height)
    frame_box.fill.background()
    frame_box.line.color.rgb = LINE
    return picture


def _placeholder(slide, left, top, width, height, label):
    """스크린샷을 나중에 붙일 자리."""
    box = slide.shapes.add_shape(5, left, top, width, height)
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0xF5, 0xF3, 0xEE)
    box.line.color.rgb = KB_YELLOW
    frame = box.text_frame
    frame.word_wrap = True
    _para(frame, label, size=12, color=MUTED, first=True, align=PP_ALIGN.CENTER)


def build() -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H

    # ── 1. 표지 ──────────────────────────────────────────────
    cover = prs.slides.add_slide(prs.slide_layouts[6])
    band = cover.shapes.add_shape(1, 0, 0, W, Inches(2.5))
    band.fill.solid()
    band.fill.fore_color.rgb = KB_GRAY
    band.line.fill.background()
    frame = _textbox(cover, Inches(0.9), Inches(0.75), Inches(11.5), Inches(1.6))
    _para(frame, "금융상품 판매서류 컴플라이언스 검증 AI Copilot",
          size=34, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF), first=True, space_after=8)
    _para(frame, "불완전판매 위험을 '판매 전에' 잡아내는 결정론적 검증 엔진",
          size=17, color=KB_YELLOW)

    frame = _textbox(cover, Inches(0.9), Inches(3.05), Inches(11.5), Inches(2.6))
    _para(frame, "한 건의 판매에는 서류가 4종 있습니다. "
                 "위반은 서류 하나가 아니라 서류 사이에서 드러납니다.",
          size=18, bold=True, color=KB_GRAY, first=True, space_after=14)
    _para(frame, "적합성 진단표의 '안정형'과 상품설명서의 '1등급'은 각각 보면 정상입니다. "
                 "두 서류를 맞대어야 비로소 부적합 판매가 보입니다.", size=14, color=INK, space_after=20)
    _para(frame, "제8회 KB Future Finance A.I. Challenge  |  팀명: 주식손절못함",
          size=13, color=MUTED)

    # ── 2. 문제 정의 ────────────────────────────────────────
    s = _slide(prs, "문제 정의", "서류 하나만 보면 원리적으로 잡을 수 없는 위반이 있다", tag="문제 정의·필요성")
    _card(s, Inches(0.62), Inches(1.6), Inches(3.85), Inches(2.35),
          "① 위반은 서류 사이에 있다",
          ["진단표: 고객 투자성향 '안정형'",
           "상품설명서: 위험등급 '1등급'",
           "→ 각 서류는 정상. 대조해야 위반",
           "금소법 17조 적합성원칙 위반 소지"], accent=RISK)
    _card(s, Inches(4.72), Inches(1.6), Inches(3.85), Inches(2.35),
          "② 사람이 눈으로 대조한다",
          ["판매 1건 = 서류 4종 교차 확인",
           "고객 성향·상품 등급·설명일·계약일·서명",
           "건수가 쌓이면 누락이 발생",
           "사후 분쟁 시 입증 부담은 판매사"])
    _card(s, Inches(8.82), Inches(1.6), Inches(3.9), Inches(2.35),
          "③ 서류 형태가 제각각",
          ["전자 PDF · 스캔본 · 모바일 스크린샷",
           "비대면은 확인서가 계약서에 통합",
           "위험등급이 표 안 체크(✓)로만 표시",
           "→ 텍스트 추출만으로는 못 읽는다"])

    frame = _textbox(s, Inches(0.62), Inches(4.25), Inches(12.1), Inches(2.4))
    _para(frame, "불완전판매가 성립하면 판매사는 배상·제재를 부담합니다. "
                 "그런데 위반의 상당수는 '서류 간 불일치'라, 서류를 한 장씩 검토하는 방식으로는 구조적으로 놓칩니다.",
          size=15, bold=True, color=KB_GRAY, first=True, space_after=10)
    _para(frame, "· 금융소비자보호법(2021 시행)은 적합성·설명의무 등 6대 판매원칙과 입증책임을 판매사에 부과합니다.",
          size=13, color=INK)
    _para(frame, "· 비대면 채널이 늘며 서류가 전자·통합 양식으로 다양해져, 사람이 형태별로 확인하는 부담이 커졌습니다.",
          size=13, color=INK)

    # ── 3. 시장 근거 ────────────────────────────────────────
    s = _slide(prs, "문제의 규모", "불완전판매 민원은 급증했고, 그 비용은 이미 판매사가 치르고 있다",
               tag="문제 정의·필요성")
    stats = [
        ("11만 6,338건", "2024년 금융민원 총계", "전년 대비 +24%"),
        ("+53.3%", "은행 권역 민원 증가", "24,043건 · ELS 불완전판매가 주원인"),
        ("+1,048%", "펀드·방카슈랑스 민원 증가", "신탁은 +1,459%"),
        ("약 1조 3,000억", "홍콩 ELS 자율배상 규모", "5개 은행 합계"),
    ]
    for i, (big, mid, small) in enumerate(stats):
        box = s.shapes.add_shape(5, Inches(0.62 + i * 3.08), Inches(1.55), Inches(2.86), Inches(1.75))
        box.fill.solid()
        box.fill.fore_color.rgb = BG_SOFT
        box.line.color.rgb = KB_YELLOW
        frame = box.text_frame
        frame.word_wrap = True
        _para(frame, big, size=24, bold=True, color=RISK, first=True,
              align=PP_ALIGN.CENTER, space_after=3)
        _para(frame, mid, size=12, bold=True, color=KB_GRAY, align=PP_ALIGN.CENTER, space_after=2)
        _para(frame, small, size=10, color=MUTED, align=PP_ALIGN.CENTER, space_after=0)

    _card(s, Inches(0.62), Inches(3.6), Inches(5.9), Inches(2.35),
          "이 문제는 KB국민은행이 직접 겪었습니다",
          ["홍콩H지수 ELS 불완전판매로 금감원 검사 대상이 된 5개 은행:",
           "KB국민 · 신한 · 하나 · NH농협 · SC제일",
           "과징금은 최초 약 4조원으로 산정된 뒤 논의를 거쳐",
           "6,000억원 수준으로 조정 (2026.7 기준 심의 진행)"], accent=RISK)
    _card(s, Inches(6.75), Inches(3.6), Inches(5.97), Inches(2.35),
          "제재 근거는 '판매 과정'입니다",
          ["금소법 제57조 — 위반 관련 계약으로 얻은 수입의 50% 이내 과징금",
           "즉 상품이 손실을 냈다는 사실이 아니라",
           "적합성·설명의무를 지켰는지가 제재를 가른다",
           "→ 판매 시점의 서류가 곧 방어 근거이자 위험 지점"], accent=KB_YELLOW)

    frame = _textbox(s, Inches(0.62), Inches(6.1), Inches(12.1), Inches(0.9))
    _para(frame, "출처: 금융감독원 2024년 금융민원 통계(2025.4 보도) · 홍콩 ELS 제재 관련 보도(2026.6~7). "
                 "과징금 규모는 심의 진행에 따라 변동될 수 있습니다.",
          size=10, color=MUTED, first=True, space_after=0)

    # ── 4. ELS 사태 대입 ────────────────────────────────────
    s = _slide(prs, "실제 사건에 대입하면", "홍콩 ELS 배상비율을 가른 항목이 곧 우리 검사 항목이다",
               tag="활용 가능성")
    _table(s, Inches(0.62), Inches(1.55), Inches(12.1), [
        ["홍콩 ELS 배상비율 산정 항목", "배상 가산", "대응하는 우리 검사", "서류로 판단 가능한가"],
        ["설명의무 위반", "기본 20%", "EXP-001 · DATE-001 · ACK-001",
         "가능 — 설명 항목·설명일·서명 확인"],
        ["적합성 원칙 위반", "+10%p", "FIT-001", "가능 — 진단표 성향 ↔ 설명서 등급 대조"],
        ["부당권유 금지 위반", "+10%p", "ADV-001", "가능 — 원금보장·확정수익 표현 검출"],
        ["고령자 보호기준 미준수", "+15%p", "REC-001", "가능 — 녹취 의무 대상 여부 표시"],
    ], col_widths=[3.5, 1.5, 3.6, 3.5], body_size=11)

    _card(s, Inches(0.62), Inches(4.35), Inches(5.9), Inches(2.3),
          "우리 규칙은 임의로 고른 것이 아닙니다",
          ["실제 분쟁에서 배상비율을 결정한 네 가지 항목이",
           "그대로 우리 검사 항목과 대응합니다",
           "즉 '실무에서 문제가 된 지점'을 검사하도록 설계했습니다",
           "설명의무는 3개 규칙으로 나눠 더 세밀하게 봅니다"], accent=OK)
    _card(s, Inches(6.75), Inches(4.35), Inches(5.97), Inches(2.3),
          "판매 시점에 걸렀다면",
          ["배상비율 산정 항목 = 판매 서류에 남는 흔적",
           "성향과 등급의 불일치, 빠진 서명, 뒤집힌 날짜는",
           "모두 계약 전에 확인할 수 있는 것들입니다",
           "사후 배상 산정이 아니라 사전 차단이 목표입니다"], accent=KB_YELLOW)

    frame = _textbox(s, Inches(0.62), Inches(6.75), Inches(12.1), Inches(0.5))
    _para(frame, "출처: 홍콩 ELS 분쟁조정 배상비율 산정기준 관련 보도(2024~2026). "
                 "배상비율은 개별 사안별로 달라집니다.", size=10, color=MUTED, first=True, space_after=0)

    # ── 5. 차별점 ───────────────────────────────────────────
    s = _slide(prs, "기존 접근과의 차별점", "역대 본선 진출작은 모두 '문서 하나' 또는 '규정↔규정'을 본다",
               tag="창의성·효과")
    _table(s, Inches(0.62), Inches(1.55), Inches(12.1), [
        ["회차", "과제", "검증 대상", "우리와의 차이"],
        ["4회 입상", "「cheKB」 계약서 독소조항 검토 AI", "계약서 1종 내부 조항", "문서 간 대조 없음"],
        ["4회 입상", "「KB Sallow」 직원 맞춤 법률 비서", "법령 검색·요약", "제출 서류를 보지 않음"],
        ["7회 입상", "금융 규제·법규 준수 모니터링", "내부규정 ↔ 변경 규제", "고객 판매 건이 아님"],
        ["제안", "판매서류 컴플라이언스 Copilot",
         "서류 4종 ↔ 서류 4종 ↔ 법령", "문서 간 교차 검증 (신규)"],
    ], col_widths=[1.3, 4.0, 3.4, 3.4])

    _card(s, Inches(0.62), Inches(4.35), Inches(5.9), Inches(2.35),
          "왜 교차 검증이 창의적인가",
          ["단일 문서 분석으로는 '적합성 위반'을 정의할 수 없다",
           "고객 정보(진단표)와 상품 정보(설명서)가 다른 문서에 있기 때문",
           "설명일 > 계약일(선후 역전)도 두 문서를 봐야 성립",
           "→ 문제 자체가 교차 검증을 요구한다"], accent=KB_YELLOW)
    _card(s, Inches(6.75), Inches(4.35), Inches(5.97), Inches(2.35),
          "판정은 AI가 하지 않는다",
          ["LLM은 '읽기'만, 판정은 결정론적 규칙",
           "같은 서류·같은 정책이면 언제나 같은 결과",
           "규제 도구에서 재현 불가능한 판정은 쓸 수 없다",
           "7회 입상작이 강조한 'XAI·근거 투명성'을 코드로 강제"], accent=OK)

    # ── 4. 솔루션 개요 ──────────────────────────────────────
    s = _slide(prs, "솔루션 개요", "판매 건 단위로 올리면, 8개 항목을 근거와 함께 판정한다", tag="활용 가능성")
    steps = [
        ("① 판매 건별 업로드", ["상품 계약 1건 = 칸 1개", "적합성 진단표 · 상품설명서", "가입신청서 · 설명확인서",
                            "PDF · 스캔 · 스크린샷 모두"]),
        ("② 판독·구조화", ["텍스트 → OCR → AI 비전 단계 폴백", "문서유형 자동 분류(수동 교정 가능)",
                       "판정에 필요한 값만 추출", "원문에 없는 값은 폐기"]),
        ("③ 규칙 판정", ["8개 검사 항목", "결정론적 규칙 = 재현 가능", "통과 / 주의 / 누락 / 위험",
                     "가장 보수적인 값 기준"]),
        ("④ 근거 제시·기록", ["관련 법령 조문 자동 검색", "서류 원문 위치 하이라이트", "값의 출처(규칙/AI판독) 표시",
                          "검증 결과 JSON 내보내기"]),
    ]
    for i, (head, lines) in enumerate(steps):
        _card(s, Inches(0.62 + i * 3.08), Inches(1.65), Inches(2.86), Inches(2.75), head, lines)

    frame = _textbox(s, Inches(0.62), Inches(4.75), Inches(12.1), Inches(2.0))
    _para(frame, "누가 쓰는가", size=15, bold=True, color=KB_GRAY, first=True, space_after=8)
    _para(frame, "· 창구·PB 담당자 — 판매 직후 '이 건에 빠진 서류·어긋난 값'을 즉시 확인해 사후 분쟁을 예방",
          size=13, color=INK)
    _para(frame, "· 컴플라이언스 부서 — 판매 건을 모아 점검하고, 판정 근거와 조문이 담긴 기록을 남김",
          size=13, color=INK)
    _para(frame, "· 도입 형태 — 사내 시스템에 붙는 검증 모듈. 판정 규칙은 KB 내부 기준으로 교체 가능하게 분리",
          size=13, color=INK)

    # ── 5. AS-IS / TO-BE ────────────────────────────────────
    s = _slide(prs, "업무 흐름 변화", "기존 프로세스를 바꾸지 않고, 서류 작성과 마감 사이에 들어간다",
               tag="활용 가능성")
    frame = _textbox(s, Inches(0.62), Inches(1.5), Inches(12.1), Inches(0.4))
    _para(frame, "AS-IS   현재 창구 업무", size=15, bold=True, color=MUTED, first=True, space_after=0)
    asis = ["상담·권유", "서류 4종 작성", "직원이 육안 대조", "마감", "컴플라이언스 표본 점검"]
    for i, step in enumerate(asis):
        box = s.shapes.add_shape(5, Inches(0.62 + i * 2.46), Inches(1.95), Inches(2.24), Inches(0.62))
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor(0xF2, 0xF0, 0xEB)
        box.line.color.rgb = LINE
        _para(box.text_frame, step, size=12, color=INK, first=True, align=PP_ALIGN.CENTER, space_after=0)
    frame = _textbox(s, Inches(0.62), Inches(2.68), Inches(12.1), Inches(0.9))
    # 한 줄에 들어가야 한다 — 넘치면 마지막 글자만 다음 줄로 떨어져 지저분해진다.
    _para(frame, "· 건수가 쌓이면 육안 대조에서 누락이 생깁니다   "
                 "· 컴플라이언스는 표본만 봅니다   "
                 "· 발견해도 이미 계약이 끝난 뒤입니다",
          size=12, color=RISK, first=True, space_after=0)

    frame = _textbox(s, Inches(0.62), Inches(3.7), Inches(12.1), Inches(0.4))
    _para(frame, "TO-BE   제안 시스템 적용", size=15, bold=True, color=KB_GRAY, first=True, space_after=0)
    tobe = ["상담·권유", "서류 4종 작성", "자동 검증 (약 50초)", "이상 시 즉시 보완", "전건 기록 자동 축적"]
    for i, step in enumerate(tobe):
        emphasis = i in (2, 3, 4)
        box = s.shapes.add_shape(5, Inches(0.62 + i * 2.46), Inches(4.15), Inches(2.24), Inches(0.62))
        box.fill.solid()
        box.fill.fore_color.rgb = BG_SOFT if emphasis else RGBColor(0xF2, 0xF0, 0xEB)
        box.line.color.rgb = KB_YELLOW if emphasis else LINE
        _para(box.text_frame, step, size=12, bold=emphasis, color=KB_GRAY if emphasis else INK,
              first=True, align=PP_ALIGN.CENTER, space_after=0)

    _card(s, Inches(0.62), Inches(5.05), Inches(3.85), Inches(1.65),
          "표본 점검 → 전건 점검",
          ["사람이 놓치던 건까지 일관되게 확인",
           "누락 없이 모든 판매 건에 같은 기준 적용"], accent=OK)
    _card(s, Inches(4.72), Inches(5.05), Inches(3.85), Inches(1.65),
          "사후 적발 → 사전 예방",
          ["계약 완료 전에 보완 기회를 준다",
           "분쟁·배상으로 가기 전에 차단"], accent=OK)
    _card(s, Inches(8.82), Inches(5.05), Inches(3.9), Inches(1.65),
          "기록이 자동으로 남는다",
          ["판정 근거·조문·검증 시각을 함께 보관",
           "사후 입증 자료로 그대로 사용"], accent=OK)

    # ── 6. 아키텍처 ─────────────────────────────────────────
    s = _slide(prs, "아키텍처: LLM은 읽고, 규칙이 판정한다", "읽기와 판정 사이에 '검증 계층'을 둔다",
               tag="기술 적정성")
    _table(s, Inches(0.62), Inches(1.5), Inches(12.1), [
        ["계층", "역할", "LLM 관여", "구성"],
        ["① 판독", "비정형 서류 → 텍스트·좌표", "폴백 시에만", "PyMuPDF · Tesseract OCR · LLM 비전"],
        ["② 추출", "판정에 필요한 값만 구조화", "있음", "Claude Haiku"],
        ["③ 검증", "읽은 값이 원문에 실제로 있는지 대조", "없음", "원문 대조 · 문서유형 게이팅 · 고정 스키마"],
        ["④ 판정", "위반 여부 결정", "없음", "결정론적 규칙 8종 (Python)"],
        ["⑤ 근거", "조문·원문 위치 제시", "쟁점 설명만", "규칙별 확정 근거 조문 · 적용 조항 형광 표시"],
    ], col_widths=[1.3, 3.9, 1.6, 5.3], body_size=11)

    _card(s, Inches(0.62), Inches(4.25), Inches(5.9), Inches(2.45),
          "LLM은 판정을 바꿀 수 없다 — 구조적으로",
          ["판정 모듈에 LLM 관련 코드 0건 (순수 Python)",
           "판정 함수에 LLM 인자 자체가 없음",
           "LLM이 만드는 객체에 '판정' 필드가 없음",
           "→ 바꾸고 싶어도 손댈 지점이 없다"], accent=OK)

    box = s.shapes.add_shape(5, Inches(6.75), Inches(4.25), Inches(5.97), Inches(2.45))
    box.fill.solid()
    box.fill.fore_color.rgb = BG_SOFT
    box.line.color.rgb = KB_YELLOW
    frame = _textbox(s, Inches(7.0), Inches(4.42), Inches(5.5), Inches(2.1))
    _para(frame, "실측 · 같은 서류를 2회 검증 (캐시 off, LLM 실호출 8건×2)",
          size=13, bold=True, color=KB_GRAY, first=True, space_after=9)
    _para(frame, "LLM이 쓴 설명 문구", size=11, color=MUTED, space_after=2)
    _para(frame, "1회차 \"안정형(6등급만 가입 가능)이나 상품은 1등급…\"", size=10, color=INK, space_after=1)
    _para(frame, "2회차 \"'안정형'(위험 감내도 최저)이나 위험등급은…\"", size=10, color=INK, space_after=6)
    _para(frame, "→ 문구는 매번 달랐으나", size=11, color=MUTED, space_after=2)
    _para(frame, "판정 8개는 완전히 동일", size=15, bold=True, color=OK, space_after=0)

    # ── 7. 기술 선택 근거 ───────────────────────────────────
    s = _slide(prs, "왜 이 구조인가", "다른 방식을 검토하고 배제한 근거", tag="기술 적정성")
    _table(s, Inches(0.62), Inches(1.55), Inches(12.1), [
        ["대안", "배제 이유", "근거"],
        ["LLM이 판정까지 수행", "같은 입력에도 결과가 흔들려 감사·재현이 불가능",
         "실측 — 같은 판정에도 설명 문구가 매번 달랐음"],
        ["전용 모델 파인튜닝", "실제 판매서류는 개인정보라 학습 데이터 확보 불가. 규정 개정 시마다 재학습 필요",
         "규정은 조문 텍스트로 관리하는 편이 갱신에 유리"],
        ["RAG(법령 검색)만 구성", "조문은 찾아주지만 서류에서 값을 뽑아 문서 간 대조하지 못함",
         "적합성 위반은 두 문서를 맞대야 성립"],
        ["규칙만 사용 (LLM 없이)", "비정형 서류에서 문서유형·필드를 읽지 못함",
         "실측 — 실물 22건에서 규칙 분류가 전부 확신 실패"],
        ["채택: LLM 판독 + 규칙 판정 + 원문 대조",
         "읽기는 LLM이 잘하고, 판정은 재현 가능해야 한다 — 역할을 분리",
         "실물 25건 전수 감사 이상 0건"],
    ], col_widths=[2.9, 5.4, 3.8], body_size=10)

    _card(s, Inches(0.62), Inches(4.9), Inches(12.1), Inches(1.75),
          "핵심 판단",
          ["컴플라이언스 도구는 '정확도가 높은 모델'보다 '틀렸을 때 드러나는 구조'가 중요합니다.",
           "LLM이 값을 잘못 읽으면 판정도 틀립니다 — 그래서 읽기와 판정 사이에 원문 대조 계층을 두었습니다.",
           "실제로 LLM이 위험등급을 '5등급'으로 지어냈을 때(원문에는 6등급만 존재) 이 계층이 잡아냈습니다."],
          accent=KB_YELLOW)

    # ── 6. 환각 차단 ────────────────────────────────────────
    s = _slide(prs, "핵심 기술 ①  AI가 지어낸 값을 판정에 쓰지 않는다",
               "실물 서류에서 실제로 발생한 오류와, 이를 막은 방식", tag="기술 적정성")
    _table(s, Inches(0.62), Inches(1.55), Inches(12.1), [
        ["실제 발생한 오류", "원문", "방어 장치", "결과"],
        ["LLM이 위험등급 '5등급' 생성", "원문에는 6등급만 존재", "원문 대조 후 미존재 값 폐기", "6등급으로 교정"],
        ["상품명에 '(주가연계증권)' 삽입", "'제 23129호 파생결합증권(ELS)'", "원문 표기로 복원, 겹침 부족 시 폐기",
         "원문 표기 채택"],
        ["설명확인서를 상품설명서로 오분류", "제목이 '상품설명 확인서'", "규칙 분류가 확신하면 LLM보다 우선",
         "고객확인·설명일 복구"],
        ["약관의 '서명' 문구를 확인으로 오인", "서명란은 실제로 공란", "페이지 이미지에서 서명 유무 판독",
         "미서명 적발 가능"],
        ["진단표의 진단일을 설명일로 오인", "'투자성향 2026-07-24 기준'",
         "유형별 고정 스키마에서 설명일 제외", "회차 간 판정 흔들림 제거"],
        ["비전이 '등급 못 찾음'을 등급으로 읽음", "'표시 없음(1~6 중 판단 불가)'",
         "답변이 등급 하나일 때만 채택", "1등급 오판 차단"],
        ["두 자리 연도를 서기 26년으로 읽음", "'26.07.15'",
         "세기를 지어내지 않고 형식 경고로 남김", "계약 이후 설명 적발"],
    ], col_widths=[3.3, 3.1, 3.5, 2.2], body_size=10)

    _card(s, Inches(0.62), Inches(4.5), Inches(3.85), Inches(2.2),
          "1차 · 원문 대조",
          ["추출값이 원문에 실제로 있는지 확인",
           "없으면 채택하지 않고 '미확인'",
           "오판보다 미확인이 안전하다"], accent=KB_YELLOW)
    _card(s, Inches(4.72), Inches(4.5), Inches(3.85), Inches(2.2),
          "2차 · 문서유형 게이팅",
          ["상품설명서에 계약일은 없다",
           "→ 발행일을 계약일로 오인 차단",
           "유형별로 가능한 필드를 고정"], accent=KB_YELLOW)
    _card(s, Inches(8.82), Inches(4.5), Inches(3.9), Inches(2.2),
          "3차 · 고정 스키마",
          ["같은 양식이면 항상 같은 필드 목록",
           "판독 회차마다 값이 달라질 수 있는 필드는 서류 유형별 고정 스키마로 차단",
           "실측: 캐시 없이 3회 재판독 → 회차 간 불일치 0건"], accent=KB_YELLOW)

    # ── 7. 판독 폴백 ────────────────────────────────────────
    s = _slide(prs, "핵심 기술 ②  어떤 형태로 와도 읽는다",
               "텍스트 → OCR → AI 비전 단계 폴백 (필요할 때만 호출)", tag="기술 실현 가능성")
    _table(s, Inches(0.62), Inches(1.55), Inches(12.1), [
        ["서류 형태", "1차 판독", "폴백", "실측 결과"],
        ["전자 PDF", "텍스트 레이어", "—", "정상"],
        ["스캔 PDF · 종이 촬영", "Tesseract OCR(한국어)", "AI 비전", "분류·서명·날짜 모두 원본과 동일"],
        ["모바일 다크모드 스크린샷", "OCR 오독('투자성향'→'투자성양')", "AI 비전", "'적극투자형' 정확 판독"],
        ["위험등급이 표 체크(✓)로만 표시", "텍스트 공란", "AI 비전", "4등급·6등급 정확 판독"],
        ["계약일이 표에 분리 기재", "'년 월 일 24 07 2026'", "AI 비전 + 원문 대조", "2026-07-24 채택"],
    ], col_widths=[3.4, 3.4, 2.3, 3.0])

    _card(s, Inches(0.62), Inches(4.7), Inches(5.9), Inches(2.0),
          "비용을 쓰지 않는 설계",
          ["비전은 앞 단계가 실패했을 때만 호출",
           "한 페이지에 필요한 항목을 한 번에 질문",
           "결과 캐시로 같은 서류 재검증은 0원",
           "실측: 패키지 1건당 약 76원 (모델 승격 0회)"], accent=OK)
    _card(s, Inches(6.75), Inches(4.7), Inches(5.97), Inches(2.0),
          "개인정보 최소화",
          ["생년월일·계좌번호는 애초에 추출하지 않음",
           "비전 프롬프트에서 개인정보 출력을 금지",
           "원본 파일은 디스크에 저장하지 않음",
           "판독 캐시 즉시 삭제 기능 제공"], accent=OK)

    # ── 8. 검사 규칙 ────────────────────────────────────────
    s = _slide(prs, "검사 항목 8종과 근거 조문", "무엇을 검사하는지, 그리고 무엇을 검사하지 않는지 밝힌다",
               tag="기술 적정성")
    _table(s, Inches(0.62), Inches(1.5), Inches(12.1), [
        ["규칙", "검사 내용", "근거", "한 문서로 가능?"],
        ["FIT-001", "고객 투자성향 ↔ 상품 위험등급 적합성", "금소법 17조", "불가 (교차)"],
        ["EXP-001", "원금손실·위험등급·수수료 설명 존재", "금소법 19조", "가능"],
        ["DATE-001", "설명일이 계약일보다 선행하는지", "금소법 19조", "불가 (교차)"],
        ["ACK-001", "고객 설명 확인·서명 증빙", "금소법 19조", "가능"],
        ["PKG-001", "서류 간 상품명·상품코드 동일성", "금소법 19조", "불가 (교차)"],
        ["ADV-001", "원금보장·확정수익 등 부당권유 표현", "금소법 21조", "가능"],
        ["DOC-001", "판매서류 4종 구비 여부", "금소법 23조", "불가 (교차)"],
        ["REC-001", "녹취 의무 대상 여부(고위험·고령·부적합)", "금소법 28조", "가능"],
    ], col_widths=[1.4, 6.0, 2.3, 2.4], body_size=10)

    frame = _textbox(s, Inches(0.62), Inches(5.55), Inches(12.1), Inches(1.5))
    _para(frame, "판매서류로 검증할 수 있는 판매원칙은 3개이고, 그 3개를 모두 덮었습니다.",
          size=14, bold=True, color=KB_GRAY, first=True, space_after=6)
    _para(frame, "나머지 3개는 서류 밖의 정보를 요구합니다 — "
                 "적정성(18조)은 '권유 없이 청약했는지'(판매채널 기록), "
                 "불공정영업(20조)은 '다른 상품 끼워팔기'(거래 이력), "
                 "광고(22조)는 광고물 자체가 검사 대상입니다.",
          size=12, color=INK, space_after=5)
    _para(frame, "여기에 6대 원칙 밖의 의무인 계약서류 제공(23조)·자료 기록 유지(28조)까지 검사합니다. "
                 "녹취(REC-001)는 음성을 판독하지 않고 '녹취가 필요한 건인지'만 표시합니다.",
          size=12, color=INK)

    # ── 검사 범위 산정 ──────────────────────────────────────
    s = _slide(prs, "검사 범위를 어떻게 잡았나", "금소법 73개 조문 중 '판매서류로 검증할 수 있는 것'만 남긴다",
               tag="기술 적정성")
    _table(s, Inches(0.62), Inches(1.55), Inches(5.8), [
        ["금소법 조문 성격", "조문 수"],
        ["판매행위 규제", "10"],
        ["정의 · 총칙 · 부칙", "31"],
        ["감독 · 검사 · 제재 · 과징금", "15"],
        ["분쟁조정 · 소송 · 소비자 권리", "12"],
        ["판매업자 자격 · 등록 · 조직", "5"],
        ["합계", "73"],
    ], col_widths=[4.2, 1.6], body_size=11)

    _table(s, Inches(6.65), Inches(1.55), Inches(6.07), [
        ["판매행위 규제 10개 조문", "우리 검사"],
        ["17 적합성 · 19 설명의무 · 21 부당권유", "커버"],
        ["23 계약서류 제공 · 28 자료 기록 유지", "커버"],
        ["18 적정성 (권유 여부 정보 필요)", "서류 밖"],
        ["20 불공정영업 (타 상품 거래 이력 필요)", "서류 밖"],
        ["22 광고 (광고물이 검사 대상)", "서류 밖"],
        ["46 청약철회 · 47 위법계약해지", "판매 이후"],
    ], col_widths=[4.5, 1.57], body_size=11)

    _card(s, Inches(0.62), Inches(4.55), Inches(5.8), Inches(2.1),
          "분모는 73이 아니라 10입니다",
          ["63개 조문은 정의·감독절차·분쟁조정 등으로,",
           "판매 시점 서류로 검증할 대상이 아닙니다.",
           "판매행위 규제 10개 중 '판매 시점에 서류만으로",
           "확인 가능한 5개'를 모두 검사합니다."], accent=KB_YELLOW)
    _card(s, Inches(6.65), Inches(4.55), Inches(6.07), Inches(2.1),
          "넓게 얕게가 아니라, 좁게 깊게",
          ["설명의무(19조) 하나를 3개 규칙으로 나눠 봅니다",
           "  EXP-001 설명 항목이 실제로 있는가",
           "  DATE-001 설명이 계약보다 먼저였는가",
           "  ACK-001 고객 확인·서명이 실재하는가"], accent=OK)

    # ── 8-2. 판매 채널·고객 조건 ────────────────────────────
    s = _slide(prs, "서류에 없는 맥락 — 대면·비대면과 고령투자자",
               "같은 서류 구성이라도 판매 채널과 고객 조건에 따라 판정이 달라야 한다",
               tag="창의성·효과")
    _table(s, Inches(0.62), Inches(1.5), Inches(12.1), [
        ["구분", "대면 (영업점 창구)", "비대면 (모바일·인터넷)"],
        ["설명확인서", "별도 서류로 받아 보관한다",
         "계약서의 확인 문구 + 전자서명으로 통합된다"],
        ["그 서류가 없으면",
         "DOC-001 누락 — 영업점에서 실제로 빠뜨린 것",
         "DOC-001 주의 — 정상 형태. 전자문서함 보관 기록 확인 안내"],
        ["같은 근거 조문", "금소법 23조 계약서류 제공 의무", "금소법 23조 계약서류 제공 의무"],
    ], col_widths=[2.3, 4.6, 5.2], body_size=11)

    _card(s, Inches(0.62), Inches(3.4), Inches(3.85), Inches(2.05),
          "채널은 서류에 없다",
          ["'비대면'이라 적힌 서류는 없습니다.",
           "없는 것을 추측하면 곧 환각입니다.",
           "담당자가 건마다 체크로 표시하고,",
           "규칙은 그 조건으로 판정합니다."], accent=KB_YELLOW)
    _card(s, Inches(4.72), Inches(3.4), Inches(3.85), Inches(2.05),
          "체크만으로 통과시키지 않는다",
          ["표시해도 그냥 넘어가지 않습니다.",
           "다른 서류에 확인 문구와 고객",
           "확인값이 있을 때만 완화합니다.",
           "실측 — 데모 3종은 표시해도 '누락'"], accent=OK)
    _card(s, Inches(8.82), Inches(3.4), Inches(3.9), Inches(2.05),
          "고령투자자도 같은 구조",
          ["생년월일은 추출하지 않습니다.",
           "만 65세 이상도 담당자가 표시합니다.",
           "표시하면 REC-001 녹취 대상이 됩니다.",
           "사유 — 고위험(1·2등급)·고령·부적합"], accent=KB_YELLOW)

    frame = _textbox(s, Inches(0.62), Inches(5.68), Inches(12.1), Inches(0.7))
    _para(frame, "이 구분이 없으면 정상적인 비대면 판매가 전부 '서류 누락'으로 뜹니다. "
                 "컴플라이언스 도구에서 오탐은 곧 도입 실패입니다.",
          size=13, bold=True, color=KB_GRAY, first=True)

    # ── 9. 검증 결과 ────────────────────────────────────────
    s = _slide(prs, "검증 결과", "실제 금융사 서류로 검증했고, 회귀 테스트로 고정했다", tag="기술 실현 가능성")
    metrics = [
        ("25건", "실물 서류 전수 감사\n이상 0건"),
        ("86건", "자동화 회귀 테스트\n전부 통과"),
        ("1가지", "업로드 순서 24가지 →\n판정 조합 1가지"),
        ("0건", "교차 모델 판정 불일치\n(haiku vs sonnet)"),
    ]
    for i, (big, small) in enumerate(metrics):
        box = s.shapes.add_shape(5, Inches(0.62 + i * 3.08), Inches(1.6), Inches(2.86), Inches(1.6))
        box.fill.solid()
        box.fill.fore_color.rgb = BG_SOFT
        box.line.color.rgb = KB_YELLOW
        frame = box.text_frame
        frame.word_wrap = True
        _para(frame, big, size=30, bold=True, color=KB_GRAY, first=True,
              align=PP_ALIGN.CENTER, space_after=2)
        for line in small.split("\n"):
            _para(frame, line, size=11, color=INK, align=PP_ALIGN.CENTER, space_after=0)

    _table(s, Inches(0.62), Inches(3.5), Inches(12.1), [
        ["검증 항목", "방법", "결과"],
        ["실물 서류 커버리지",
         "5개 운용·증권사 / ETF·채권·MMF·ELS·DLS / 위험등급 1~6 / 전자·스캔·스크린샷",
         "25건 전수 감사, 이상 징후 0건"],
        ["판정 재현성", "동일 패키지를 업로드 순서 24가지로 반복", "판정 조합 1가지 (순서 무관)"],
        ["판독 재현성", "캐시를 끄고 실물 서류를 3회 재판독해 필드 비교", "회차 간 불일치 0건"],
        ["점검에 쓰지 않은 자료", "KB·삼성·미래에셋·신한·대우 실물 23건으로 재검증",
         "위험등급 10/10 일치 · 부당권유 오탐 0건"],
        ["근거 조문 안정성", "규칙마다 검색어를 바꿔 근거 조문 비교", "8종 전부 불변(확정 선언)"],
        ["모델 의존성", "haiku · sonnet 교차 실행 후 판정 임계 필드 비교", "불일치 0건 (저가 모델로 충분)"],
        ["오탐 통제", "정상 서류 17건에 부당권유 검사(ADV-001) 적용", "오탐 0건"],
    ], col_widths=[2.4, 6.3, 3.4], body_size=9)

    # ── 10. 정량 효과 ───────────────────────────────────────
    s = _slide(prs, "정량 효과와 운영 비용", "검증 시간과 API 비용을 실측 기준으로 제시", tag="활용 가능성")
    _table(s, Inches(0.62), Inches(1.6), Inches(12.1), [
        ["구분", "수작업 기준", "제안 시스템", "비고"],
        ["판매 건 1건(서류 4종) 검증", "약 60분 (문서당 15분 가정)", "약 50초",
         "첫 처리 실측 (LLM 판독 + 법령 API 포함)"],
        ["재검증(같은 서류)", "동일 시간 재소요", "즉시 · API 비용 0원", "결과 캐시"],
        ["API 비용", "—", "약 76원 / 건", "Claude Haiku 실측"],
        ["일 3,000건 환산(가정)", "약 3,000시간", "약 23만원 / 일", "규모 추정치"],
    ], col_widths=[3.2, 3.2, 3.0, 2.7])

    _card(s, Inches(0.62), Inches(4.1), Inches(5.9), Inches(2.55),
          "비용이 낮은 이유",
          ["판정은 코드가 하므로 LLM 호출이 적다",
           "비전은 앞 단계가 실패했을 때만 호출",
           "모델 승격(고가 모델) 발동 0회 — 저가 모델로 충분함을 교차 검증",
           "동일 서류 재검증은 캐시로 0원"], accent=OK)
    _card(s, Inches(6.75), Inches(4.1), Inches(5.97), Inches(2.55),
          "효과는 시간 단축만이 아니다",
          ["사람이 놓치기 쉬운 '서류 간 불일치'를 기계가 일관되게 확인",
           "판정 근거(조문·원문 위치)가 함께 남아 사후 입증에 사용",
           "검증 시각·모델·적용 정책을 기록으로 보관",
           "검사 범위를 명시해 과신을 방지"], accent=KB_YELLOW)

    # ── 11. 시연 시나리오 ───────────────────────────────────
    s = _slide(prs, "시연 시나리오", "위반을 심어둔 패키지는 전부 적발하고, 정상 건은 통과시킨다",
               tag="창의성·효과")
    _table(s, Inches(0.62), Inches(1.5), Inches(6.0), [
        ["검사", "위반 심은 패키지", "실제 정상 거래"],
        ["FIT-001 적합성", "위험 (안정형 · 1등급)", "통과"],
        ["DATE-001 선후", "위험 (설명 07-15 > 계약 07-12)", "통과"],
        ["ACK-001 확인", "위험 (미서명)", "주의"],
        ["ADV-001 부당권유", "통과", "통과"],
        ["DOC-001 서류구비", "통과", "주의(비대면 통합)"],
        ["REC-001 녹취", "주의 (고위험·부적합)", "통과"],
    ], col_widths=[1.9, 2.3, 1.8], body_size=10)

    _shot(s, Inches(6.95), Inches(1.5), Inches(5.77), Inches(2.6),
          "01_판정요약.png", "[스크린샷] 판정 요약 화면")
    _shot(s, Inches(6.95), Inches(4.15), Inches(5.77), Inches(2.6),
          "04_하이라이트.png", "[스크린샷] 서류 원문 하이라이트", focus=0.09)

    frame = _textbox(s, Inches(0.62), Inches(4.35), Inches(6.0), Inches(2.3))
    _para(frame, "오탐을 내지 않는 것이 핵심입니다", size=15, bold=True, color=KB_GRAY,
          first=True, space_after=8)
    _para(frame, "실제 KB 판매 건을 넣으면 위험 0건으로 통과합니다. "
                 "위반을 심은 건만 정확히 잡습니다.", size=12, color=INK, space_after=6)
    _para(frame, "컴플라이언스 도구는 '다 잡아내는 것'보다 "
                 "'정상을 정상이라 말하는 것'이 도입 가능성을 좌우합니다.", size=12, color=INK)

    # ── 11-2. 실제 화면: 근거 조문 ──────────────────────────
    s = _slide(prs, "실제 화면 — 조문에서 걸리는 부분까지 짚는다",
               "조문 전체를 던지면 '그래서 어디가 문제냐'에 답하지 못한다", tag="창의성·효과")
    _shot(s, Inches(0.62), Inches(1.5), Inches(8.3), Inches(4.4),
          "02_근거조문.png", "[스크린샷] 근거 조문 제시 화면", focus=0.05)
    _card(s, Inches(9.15), Inches(1.5), Inches(3.57), Inches(4.4),
          "이 화면이 말하는 것",
          ["규칙마다 근거 조문을 확정 선언 — 검색어가 바뀌어도 근거가 흔들리지 않는다",
           "조문에서 규칙이 걸리는 항만 뽑아 문구를 형광 표시 (조문 전체의 6~30%)",
           "법률 조문과 그 위임을 받은 감독규정을 함께 제시",
           "적용 조항이 내보내기 파일에도 같이 담긴다"], accent=KB_YELLOW)
    frame = _textbox(s, Inches(0.62), Inches(6.05), Inches(12.1), Inches(0.9))
    _para(frame, "녹취 의무(REC-001) 판정에 금소법 제28조와 감독규정 제25조가 함께 붙고, "
                 "각 조문에서 '자료를 기록'·'멸실 또는 위조'·'고시하는 기간(5년)'이 형광으로 "
                 "표시됩니다 — 담당자는 화면을 벗어나지 않고 무엇을 확인해야 하는지 압니다.",
          size=12, color=INK, first=True)

    # ── 한계와 대응 ─────────────────────────────────────────
    s = _slide(prs, "한계와 대응", "검사하지 못하는 것을 검사한 척하지 않는다", tag="기술 실현 가능성")
    _table(s, Inches(0.62), Inches(1.5), Inches(12.1), [
        ["한계", "왜 생기는가", "현재 대응", "향후"],
        ["녹취 음성을 판독하지 못함", "입력이 문서(PDF·이미지)로 한정",
         "REC-001로 '녹취 의무 대상 여부'만 표시하고, 음성 미판독을 화면에 명시",
         "본선: STT 연동해 낭독·동의 확인"],
        ["6대 원칙 중 3개(18·20·22조)는 미검사", "판매서류만으로는 판단에 필요한 정보가 없음",
         "검사 범위(17·19·21·23·28조)를 화면에 명시",
         "판매채널·거래이력 연계 시 확장"],
        ["판정 매트릭스가 MVP 예시값", "실제 기준은 회사 내부 규정",
         "정책을 코드에서 분리해 파라미터로 주입",
         "KB 내부 적합성 기준으로 교체"],
        ["문서유형 오분류 가능성", "실물 서류는 어휘가 섞여 규칙 분류가 확신하지 못함",
         "규칙이 확신할 때만 LLM보다 우선 + 검토자가 화면에서 수동 교정",
         "유형 판별 정확도 별도 측정"],
        ["외부 LLM API에 서류를 전송", "비정형 판독에 필요",
         "개인정보 미추출·마스킹 프롬프트·판독 캐시 즉시 삭제",
         "사내망 LLM으로 대체 가능한 구조"],
    ], col_widths=[2.5, 2.8, 4.2, 2.6], body_size=10)

    frame = _textbox(s, Inches(0.62), Inches(5.75), Inches(12.1), Inches(1.1))
    _para(frame, "한계를 감추면 도구를 신뢰할 수 없습니다.", size=15, bold=True, color=KB_GRAY,
          first=True, space_after=7)
    _para(frame, "이 시스템은 확인하지 못한 값을 '미확인'으로 드러내고, 검사하지 않는 조항을 화면에 밝힙니다. "
                 "판정을 내리지 못하는 상황에서 그럴듯한 답을 내놓는 것이 컴플라이언스에서는 가장 위험합니다.",
          size=13, color=INK)

    # ── 12. 개발 계획 ───────────────────────────────────────
    s = _slide(prs, "개발 계획", "예선 제출본은 여기서 고정하고, 본선 진출 시 단계별로 확장합니다",
               tag="개발 계획의 구체성")
    _table(s, Inches(0.62), Inches(1.5), Inches(12.1), [
        ["단계", "과제", "내용", "검증 방법"],
        ["예선 제출 (완료·고정)", "핵심 파이프라인",
         "판독 3단 폴백 · 규칙 8종 · 근거 조문 · 하이라이트 · 결과 내보내기",
         "실물 25건 + 미사용 23건 · 테스트 86건"],
        ["본선 1단계\n진출 후 ~2주", "녹취 검증",
         "음성 업로드 + STT로 '원금손실 낭독 → 고객 동의' 확인 (현재 미검사 영역)",
         "샘플 녹취로 문구 검출률 측정"],
        ["본선 2단계\n~4주", "검사 범위 확대",
         "적정성(18조)·불공정영업(20조)은 판매채널·거래이력 연계가 전제 — "
         "연계 가능 시 규칙 추가",
         "규칙별 회귀 테스트 · 정상 서류 오탐 0 유지"],
        ["본선 3단계\n~6주", "규모 대응",
         "판매 건 배치 검증 · 기간별 위반 통계 · 부서별 리포트",
         "수백 건 배치 처리 시간·정확도"],
        ["도입 준비\nPoC 협의 시", "은행 환경 정합",
         "판정 기준을 KB 내부 매트릭스로 교체 · 사내 LLM 대체 · 개인정보 마스킹",
         "내부 기준 대조 · 마스킹 후 재현성"],
    ], col_widths=[1.9, 2.1, 5.0, 3.1], body_size=10)

    frame = _textbox(s, Inches(0.62), Inches(5.5), Inches(12.1), Inches(1.5))
    _para(frame, "설계상 확장이 쉬운 이유", size=14, bold=True, color=KB_GRAY, first=True, space_after=6)
    _para(frame, "· 규칙 1개 = 함수 1개. 서류를 받아 판정 하나를 돌려주는 구조라, 규칙 추가가 다른 규칙에 영향을 주지 않습니다.",
          size=12, color=INK)
    _para(frame, "· 법령 조문은 국가법령정보 API로 이미 수집돼 있어(금소법 73개 조문 포함) 추가 수집 없이 규칙을 늘릴 수 있습니다.",
          size=12, color=INK)

    # ── 12-2. 개발 체계 ─────────────────────────────────────
    s = _slide(prs, "개발 체계 · 필요 자원 · 리스크",
               "누가 무엇을 맡고, 도입에 무엇이 필요하며, 무엇이 틀어질 수 있는지",
               tag="개발 계획의 구체성")
    _card(s, Inches(0.62), Inches(1.5), Inches(3.85), Inches(2.7),
          "역할 분담 (2인)",
          ["Track A — src/ingest · src/verify",
           "   법령 수집 · 판정 규칙 · 근거 조문",
           "Track B — src/parser · app",
           "   서류 판독 · 검증 화면",
           "공용 — schemas.py (변경 시 단독 PR)",
           "상대 모듈은 Issue로 요청"])
    _card(s, Inches(4.72), Inches(1.5), Inches(3.85), Inches(2.7),
          "품질을 고정하는 방식",
          ["인터페이스 계약 1곳 → 병렬 개발",
           "규칙 1개 = 함수 1개 · 작은 PR",
           "발견한 오류는 회귀 테스트로 고정",
           "   테스트 45건 → 86건",
           "실물 서류 전수 감사로 회귀 확인",
           "재현성: 순서·회차·모델 교차 측정"])
    _card(s, Inches(8.82), Inches(1.5), Inches(3.9), Inches(2.7),
          "도입에 필요한 것 (KB 협조)",
          ["판매채널·거래이력 연계",
           "   18조·20조 검사의 전제",
           "상품 등급 ↔ 고객 성향 내부 매트릭스",
           "사내 LLM 또는 폐쇄망 추론 환경",
           "은행 고유 판매서류 양식 샘플",
           "→ 없어도 현재 8개 항목은 그대로 동작"])

    _table(s, Inches(0.62), Inches(4.5), Inches(12.1), [
        ["리스크", "영향", "대응"],
        ["은행 고유 양식이 판독 학습 범위 밖",
         "필드 추출 실패",
         "문서유형 스키마 추가로 흡수 · 실패해도 '미확인'으로 드러나 오판이 되지 않음"],
        ["사내 LLM 으로 교체 시 판독 품질 차이",
         "추출 정확도 저하",
         "모델 교체 가능한 구조 · haiku↔sonnet 교차 검증에서 판정 불일치 0건 (모델 의존 낮음)"],
        ["법령·감독규정 개정",
         "근거 조문 무효화",
         "국가법령정보 API 재수집 · 규칙별 조문을 확정 선언해 두어 영향 범위를 즉시 특정"],
        ["개인정보 우려로 도입 지연",
         "현장 적용 차단",
         "생년월일·계좌번호 미추출 · 원본 미저장 · 캐시 삭제 제공 · 마스킹 후 재현성 검증"],
    ], col_widths=[3.3, 2.2, 6.6], body_size=10)

    # ── 13. 기술 스택 ───────────────────────────────────────
    s = _slide(prs, "기술 스택과 구현 현황", "프로토타입은 실행 가능한 상태로 제출합니다", tag="기술 실현 가능성")
    _card(s, Inches(0.62), Inches(1.6), Inches(3.85), Inches(2.6),
          "문서 판독",
          ["PyMuPDF — 텍스트·단어 좌표 추출",
           "Tesseract OCR (한국어) — 스캔·사진",
           "Claude Vision — OCR 실패 시 폴백",
           "PDF · JPG · PNG 지원"])
    _card(s, Inches(4.72), Inches(1.6), Inches(3.85), Inches(2.6),
          "이해·판정",
          ["Claude Haiku — 필드 추출 전담",
           "결정론적 규칙 8종 (Python)",
           "원문 대조 · 문서유형 스키마",
           "판정에 LLM 관여 없음"])
    _card(s, Inches(8.82), Inches(1.6), Inches(3.9), Inches(2.6),
          "근거·화면",
          ["국가법령정보 Open API",
           "규칙별 확정 근거 조문 + BM25 폴백",
           "Streamlit — 검증 화면",
           "결과 JSON 내보내기"])

    _table(s, Inches(0.62), Inches(4.5), Inches(12.1), [
        ["구현 현황", "상태"],
        ["판독 파이프라인 (텍스트 → OCR → 비전)", "완료 · 실물 25건 검증"],
        ["검사 규칙 8종 + 법령 조문 매핑", "완료 · 회귀 테스트 86건"],
        ["검증 화면 (업로드 · 판정 · 하이라이트 · 내보내기)", "완료 · 실행 가능"],
        ["데모 패키지 (위반 3건 포함)", "완료 · 위반 3건 전부 적발 확인"],
    ], col_widths=[8.0, 4.1])

    # ── 14. 마무리 ──────────────────────────────────────────
    s = _slide(prs, "정리", "위반을 잡는 것보다, 판정을 신뢰할 수 있게 만드는 데 집중했습니다")
    _card(s, Inches(0.62), Inches(1.7), Inches(3.85), Inches(2.7),
          "① 문제에 맞는 접근",
          ["적합성 위반은 서류 하나로 정의되지 않는다",
           "그래서 서류 간 교차 검증을 택했다",
           "역대 본선작이 다루지 않은 지점"], accent=KB_YELLOW)
    _card(s, Inches(4.72), Inches(1.7), Inches(3.85), Inches(2.7),
          "② 규제 도구에 맞는 설계",
          ["판정은 규칙이 한다 — 재현 가능",
           "AI가 지어낸 값은 판정에 쓰지 않는다",
           "검사하지 않는 범위를 밝힌다"], accent=OK)
    _card(s, Inches(8.82), Inches(1.7), Inches(3.9), Inches(2.7),
          "③ 실제 서류로 검증",
          ["5개사 25건 · 전자/스캔/스크린샷",
           "정상 건은 통과, 위반 건만 적발",
           "회귀 테스트로 고정"], accent=KB_YELLOW)

    frame = _textbox(s, Inches(0.62), Inches(4.8), Inches(12.1), Inches(1.8))
    _para(frame, "컴플라이언스 도구의 가치는 '많이 잡는 것'이 아니라 '틀리지 않는 것'입니다.",
          size=18, bold=True, color=KB_GRAY, first=True, space_after=10)
    _para(frame, "이 프로토타입은 AI가 읽되 판정하지 않게 하고, 읽은 값이 원문에 실제로 있는지 대조하며, "
                 "확인하지 못한 것은 '미확인'으로 드러냅니다. 그 위에서만 자동화가 현장에 들어갈 수 있다고 보았습니다.",
          size=13, color=INK)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        prs.save(OUT)
        return OUT
    except PermissionError:
        # 파워포인트로 열어둔 상태면 덮어쓸 수 없다. 옆에 새로 저장하고 알린다.
        alt = OUT.with_name(f"{OUT.stem}_new{OUT.suffix}")
        prs.save(alt)
        print(f"⚠ {OUT} 를 열어둔 상태라 덮어쓰지 못했습니다. 닫은 뒤 다시 실행하세요.")
        return alt


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    path = build()
    print(f"생성 완료: {path}  ({path.stat().st_size:,} bytes)")
