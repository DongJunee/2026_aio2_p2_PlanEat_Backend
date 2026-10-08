"""선택한 식단 세트의 상세 PDF를 생성하고 안전한 다운로드 URL을 제공합니다."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from uuid import uuid4
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import Settings, get_settings
from app.schemas.chat import RecommendationData, Recipe, RecipeSet


class RecipePdfService:
    """검증된 추천 세트만 PDF로 저장하는 파일 서비스입니다.

    파일명은 외부 입력을 그대로 사용하지 않고 UUID로 생성한다. 저장된 파일은 별도
    다운로드 route에서 같은 루트 디렉터리 안에 있는지 다시 확인해 경로 탈출을 막는다.
    """

    def __init__(self, settings: Settings) -> None:
        self._storage_directory = Path(settings.pdf_storage_directory)
        self._public_base_url = (settings.pdf_public_base_url or "").rstrip("/")

    async def generate(
        self,
        *,
        session_id: str,
        recommendation: RecommendationData,
        selected_set_id: str,
    ) -> str:
        """선택 세트의 5끼니 상세 PDF를 생성하고 공개 URL을 반환합니다."""

        selected_set = _find_recipe_set(recommendation, selected_set_id)
        if selected_set is None:
            raise ValueError("선택한 식단 세트를 찾을 수 없습니다.")
        filename = f"{uuid4().hex}-{_safe_filename_part(selected_set_id)}.pdf"
        output_path = self._storage_directory / filename
        await asyncio.to_thread(
            self._write_pdf,
            output_path,
            session_id,
            selected_set,
        )
        return self.public_url(filename)

    def public_url(self, filename: str) -> str:
        """다운로드 route 또는 배포 도메인을 포함한 URL을 반환합니다."""

        path = f"/pdfs/{filename}"
        return f"{self._public_base_url}{path}" if self._public_base_url else path

    def resolve_download_path(self, filename: str) -> Path | None:
        """파일명에 대한 안전한 다운로드 경로를 반환합니다."""

        if not re.fullmatch(r"[0-9a-f]{32}-[A-Za-z0-9_-]+\.pdf", filename):
            return None
        root = self._storage_directory.resolve()
        candidate = (root / filename).resolve()
        if candidate.parent != root or candidate.suffix.lower() != ".pdf" or not candidate.is_file():
            return None
        return candidate

    def _write_pdf(self, output_path: Path, session_id: str, selected_set: RecipeSet) -> None:
        """동기 reportlab 작업을 수행합니다. 호출자는 이를 worker thread에서 실행합니다."""

        output_path.parent.mkdir(parents=True, exist_ok=True)
        font_name = _register_korean_font()
        styles = _build_styles(font_name)
        document = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=16 * mm,
            leftMargin=16 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
            title="PlanEat 식단 상세",
            author="PlanEat",
        )
        story = [
            Paragraph("PlanEat 식단 상세", styles["title"]),
            Paragraph(
                f"선택 세트: {escape(selected_set.set_id)} · 세션: {escape(session_id)}",
                styles["subtitle"],
            ),
            Spacer(1, 7 * mm),
        ]
        for index, recipe in enumerate(selected_set.recipes, start=1):
            story.append(_recipe_block(index, recipe, styles, document.width))
            if index != len(selected_set.recipes):
                story.append(PageBreak())
        document.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)


def _find_recipe_set(recommendation: RecommendationData, set_id: str) -> RecipeSet | None:
    """검증된 추천 데이터에서 요청한 세트를 찾습니다."""

    return next((recipe_set for recipe_set in recommendation.recipe_sets if recipe_set.set_id == set_id), None)


def _safe_filename_part(value: str) -> str:
    """파일명에 사용할 세트 ID를 제한된 문자로 정규화합니다."""

    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return normalized or "recipe-set"


def _register_korean_font() -> str:
    """운영체제에 있는 한국어 폰트를 찾아 등록하고 없으면 Helvetica로 fallback합니다."""

    font_name = "PlanEatKorean"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf",
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for font_path in candidates:
        if not font_path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
            return font_name
        except (OSError, TypeError):
            continue
    return "Helvetica"


def _build_styles(font_name: str) -> dict[str, ParagraphStyle]:
    """PDF의 제목·본문·표에 공통으로 사용할 스타일을 만듭니다."""

    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PlanEatTitle",
            parent=base["Title"],
            fontName=font_name,
            fontSize=22,
            leading=28,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#173B32"),
        ),
        "subtitle": ParagraphStyle(
            "PlanEatSubtitle",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=9,
            leading=13,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#64748B"),
        ),
        "recipe_title": ParagraphStyle(
            "PlanEatRecipeTitle",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=15,
            leading=20,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#173B32"),
            spaceAfter=3 * mm,
        ),
        "body": ParagraphStyle(
            "PlanEatBody",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor("#24303D"),
        ),
        "small": ParagraphStyle(
            "PlanEatSmall",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#475569"),
        ),
    }


def _recipe_block(
    index: int,
    recipe: Recipe,
    styles: dict[str, ParagraphStyle],
    table_width: float,
) -> KeepTogether:
    """한 끼니 레시피의 기본 정보·재료·영양을 한 블록으로 만듭니다."""

    owned = ", ".join(escape(value) for value in recipe.owned_ingredients) or "없음"
    missing = ", ".join(
        escape(item.name) for item in recipe.missing_ingredients
    ) or "없음"
    shopping = ", ".join(
        f"{escape(item.ingredient)} {escape(item.amount)}" for item in recipe.shopping_list
    ) or "없음"
    nutrition = recipe.nutrition
    nutrition_text = (
        f"열량 {nutrition.calories:g} kcal · 단백질 {nutrition.protein:g} g · "
        f"탄수화물 {nutrition.carbohydrate:g} g · 지방 {nutrition.fat:g} g"
    )
    rows = [
        [Paragraph("조리 시간", styles["small"]), Paragraph(f"{recipe.cook_time}분", styles["body"])],
        [Paragraph("보유 재료", styles["small"]), Paragraph(owned, styles["body"])],
        [Paragraph("부족 재료", styles["small"]), Paragraph(missing, styles["body"])],
        [Paragraph("장보기", styles["small"]), Paragraph(shopping, styles["body"])],
        [Paragraph("영양 정보", styles["small"]), Paragraph(escape(nutrition_text), styles["body"])],
    ]
    table = Table(rows, colWidths=[30 * mm, table_width - 30 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#ECFDF5")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return KeepTogether(
        [
            Paragraph(f"{index}. {escape(recipe.title)}", styles["recipe_title"]),
            table,
        ]
    )


def _draw_footer(canvas, document) -> None:
    """모든 페이지 하단에 문서명과 페이지 번호를 표시합니다."""

    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#94A3B8"))
    canvas.drawString(16 * mm, 9 * mm, "PlanEat")
    canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"{document.page}")
    canvas.restoreState()


recipe_pdf_service = RecipePdfService(get_settings())
