"""선택한 식단 세트를 화이트 & 그린 테마 상세 PDF로 생성하고 다운로드 URL을 제공합니다."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.pdfgen.canvas import Canvas

from app.core.config import Settings, get_settings
from app.schemas.chat import RecommendationData, Recipe, RecipeSet, get_recipe_set_key


_PAGE_WIDTH, _PAGE_HEIGHT = A4
_MARGIN = 16 * mm
# 기존 카드·표·페이지 구조를 유지하면서, 인쇄와 화면 모두에서 읽기 쉬운
# 백색 바탕과 그린 계열 역할 색으로만 테마를 전환한다.
_BACKGROUND = colors.HexColor("#F7FBF8")
_PANEL = colors.HexColor("#FFFFFF")
_PANEL_DARK = colors.HexColor("#EEF7F0")
_BORDER = colors.HexColor("#B8DCC2")
_TEXT = colors.HexColor("#183B28")
_MUTED = colors.HexColor("#6B8272")
_ACCENT = colors.HexColor("#257A4D")
_PLACEHOLDER = colors.HexColor("#DCEFE1")
_RECIPE_IMAGE_HOSTS = frozenset({"foodsafetykorea.go.kr", "www.foodsafetykorea.go.kr"})
_MAX_RECIPE_IMAGE_BYTES = 8 * 1024 * 1024


class RecipePdfService:
    """검증된 추천 세트만 PDF로 저장하는 파일 서비스입니다."""

    def __init__(self, settings: Settings) -> None:
        self._storage_directory = Path(settings.pdf_storage_directory)
        self._public_base_url = (settings.pdf_public_base_url or "").rstrip("/")

    async def generate(self, *, session_id: str, recommendation: RecommendationData, selected_set_id: str) -> str:
        """선택 세트의 5끼니 상세 PDF를 생성하고 공개 URL을 반환합니다."""

        selected_set = _find_recipe_set(recommendation, selected_set_id)
        if selected_set is None:
            raise ValueError("선택한 식단 세트를 찾을 수 없습니다.")
        filename = _build_pdf_filename(selected_set_id)
        render_set = await _embed_recipe_images(selected_set)
        await asyncio.to_thread(self._write_pdf, self._storage_directory / filename, session_id, render_set)
        return self.public_url(filename)

    def public_url(self, filename: str) -> str:
        """다운로드 route 또는 배포 도메인을 포함한 URL을 반환합니다."""

        path = f"/pdfs/{filename}"
        return f"{self._public_base_url}{path}" if self._public_base_url else path

    def resolve_download_path(self, filename: str) -> Path | None:
        """파일명에 대한 안전한 다운로드 경로를 반환합니다."""

        if not re.fullmatch(
            r"(?:[0-9a-f]{32}|\d{8}T\d{6}Z-[0-9a-f]{8})-[A-Za-z0-9_-]+\.pdf",
            filename,
        ):
            return None
        root = self._storage_directory.resolve()
        candidate = (root / filename).resolve()
        if candidate.parent != root or candidate.suffix.lower() != ".pdf" or not candidate.is_file():
            return None
        return candidate

    def _write_pdf(self, output_path: Path, session_id: str, selected_set: RecipeSet) -> None:
        """기준 시안과 같은 커버 1쪽 + 레시피 5쪽 PDF를 동기로 작성합니다."""

        # 세션 ID는 민감한 대화 식별자이므로 파일명·본문 어디에도 노출하지 않는다.
        _ = session_id
        output_path.parent.mkdir(parents=True, exist_ok=True)
        font_name = _register_korean_font()
        canvas = Canvas(str(output_path), pagesize=A4, pageCompression=1)
        canvas.setTitle("PlanEat 오늘의 추천 레시피 5")
        canvas.setAuthor("PlanEat")
        _draw_cover(canvas, selected_set, font_name)
        canvas.showPage()
        for index, recipe in enumerate(selected_set.recipes, start=1):
            _draw_recipe_page(canvas, index, recipe, font_name)
            if index != len(selected_set.recipes):
                canvas.showPage()
        canvas.save()


def _find_recipe_set(recommendation: RecommendationData, set_id: str) -> RecipeSet | None:
    """검증된 추천 데이터에서 내부 키 또는 표시 문구로 세트를 찾습니다."""

    return next(
        (
            item
            for index, item in enumerate(recommendation.recipe_sets)
            if get_recipe_set_key(item, index) == set_id or item.set_id == set_id
        ),
        None,
    )


def _safe_filename_part(value: str) -> str:
    """파일명에 사용할 세트 ID를 제한된 문자로 정규화합니다."""

    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "recipe-set"


def _build_pdf_filename(selected_set_id: str) -> str:
    """시간 정렬 가능한 타임스탬프와 충돌 방지 난수로 PDF 파일명을 만듭니다."""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    unique_suffix = uuid4().hex[:8]
    return f"{timestamp}-{unique_suffix}-{_safe_filename_part(selected_set_id)}.pdf"


def _register_korean_font() -> str:
    """배포에 포함한 한글 글꼴을 우선 등록하고 운영체제 글꼴로 보완합니다.

    Render 같은 Linux 서버에는 Windows의 ``malgun.ttf``가 없을 수 있으므로,
    저장소의 Noto Sans KR을 먼저 사용해야 PDF에 한글 글리프가 임베드된다.
    모든 후보가 없을 때만 Helvetica를 반환하며, 이 경우 영문만 안전하게 표시된다.
    """

    font_name = "PlanEatKorean"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    bundled_font = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansKR-VF.ttf"
    candidates = [
        bundled_font,
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf",
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            try:
                pdfmetrics.registerFont(TTFont(font_name, str(path)))
                return font_name
            except (OSError, TypeError, TTFError):
                continue
    return "Helvetica"


def _draw_cover(canvas: Canvas, selected_set: RecipeSet, font_name: str) -> None:
    """첫 페이지에 선택 레시피 목록과 다음 페이지 안내를 표시합니다."""

    _background(canvas)
    _pill(canvas, _MARGIN, 760, 45 * mm, 11 * mm, "PLAN EAT", font_name, _ACCENT, colors.white)
    _text(canvas, "TODAY'S FIVE", _MARGIN, 716, 12, _ACCENT, font_name)
    _text(canvas, "오늘의 추천 레시피 5", _MARGIN, 670, 28, _TEXT, font_name)
    _text(canvas, "확정한 재료를 먼저 활용해 고른 다섯 가지 레시피입니다.", _MARGIN, 644, 11, _MUTED, font_name)
    _text(canvas, "다음 페이지부터 레시피별 재료와 조리 방법을 한 장씩 확인할 수 있어요.", _MARGIN, 626, 11, _MUTED, font_name)
    _text(canvas, "선택한 레시피 한눈에 보기", _MARGIN, 520, 17, _TEXT, font_name)
    table_y, table_h = 225, 260
    _round_panel(canvas, _MARGIN, table_y, _PAGE_WIDTH - 2 * _MARGIN, table_h, _PANEL_DARK, _BORDER)
    for label, offset in (("#", 8), ("레시피", 42), ("분류", 190), ("시간", 310), ("열량", 380), ("구성", 456)):
        _text(canvas, label, _MARGIN + offset, table_y + table_h - 24, 8, _MUTED, font_name)
    row_y = table_y + table_h - 47
    for index, recipe in enumerate(selected_set.recipes, start=1):
        if index > 1:
            canvas.setStrokeColor(_BORDER)
            canvas.line(_MARGIN + 5 * mm, row_y + 15, _PAGE_WIDTH - _MARGIN - 5 * mm, row_y + 15)
        category = " · ".join(recipe.owned_ingredients[:2]) or "추천 레시피"
        _text(canvas, f"{index:02d}", _MARGIN + 8, row_y, 12, _ACCENT, font_name)
        _text(canvas, _ellipsis(recipe.title, font_name, 10, 145), _MARGIN + 42, row_y + 1, 10, _TEXT, font_name)
        _text(canvas, _ellipsis(category, font_name, 8, 105), _MARGIN + 190, row_y + 2, 8, _MUTED, font_name)
        _text(canvas, _cook_time_label(recipe), _MARGIN + 310, row_y + 2, 8, _MUTED, font_name)
        _text(canvas, f"{recipe.nutrition.calories:g} kcal", _MARGIN + 380, row_y + 2, 8, _TEXT, font_name)
        _text(canvas, f"{len(recipe.owned_ingredients)}/{len(recipe.shopping_list)}", _MARGIN + 456, row_y + 2, 7, _MUTED, font_name)
        row_y -= 49
    _round_panel(canvas, _MARGIN, 75, _PAGE_WIDTH - 2 * _MARGIN, 25 * mm, _PANEL)
    _text(canvas, "NEXT", _MARGIN + 8 * mm, 103, 9, _ACCENT, font_name)
    _text(canvas, "다음 페이지부터 레시피 01부터 05까지 상세 정보가 한 장씩 이어집니다.", _MARGIN + 32 * mm, 103, 10, _TEXT, font_name)
    _footer(canvas, 1, font_name)


def _draw_recipe_page(canvas: Canvas, index: int, recipe: Recipe, font_name: str) -> None:
    """이미지·재료·조리 단계가 중심이 되도록 레시피 한 개를 A4에 렌더링합니다."""

    _background(canvas)
    _text(canvas, "PlanEat", _MARGIN, 804, 16, _TEXT, font_name)
    _text(canvas, "내 냉장고에서 시작하는 식단", _MARGIN, 785, 8, _MUTED, font_name)
    _pill(canvas, _PAGE_WIDTH - _MARGIN - 58 * mm, 788, 58 * mm, 12 * mm, "TODAY'S MENU", font_name, _ACCENT, colors.white)
    _text(canvas, _ellipsis(recipe.title, font_name, 25, _PAGE_WIDTH - 2 * _MARGIN), _MARGIN, 728, 25, _TEXT, font_name)
    subtitle = " · ".join(recipe.owned_ingredients[:2]) or "추천 레시피"
    _text(canvas, f"{subtitle} · 조리 시간 {_cook_time_label(recipe)}", _MARGIN, 700, 10, _MUTED, font_name)

    content_width = _PAGE_WIDTH - 2 * _MARGIN
    _round_panel(canvas, _MARGIN, 510, content_width, 170, _PANEL)
    _text(canvas, "TODAY'S PLATE", _MARGIN + 8 * mm, 650, 8, _ACCENT, font_name)
    image_x = _MARGIN + 8 * mm
    image_width = content_width * 0.50 - 8 * mm
    _draw_recipe_image(
        canvas,
        recipe.image,
        image_x,
        525,
        image_width,
        112,
        font_name,
    )

    info_x = image_x + image_width + 8 * mm
    info_width = content_width - image_width - 24 * mm
    info_cards = (
        ("조리 시간", _cook_time_label(recipe)),
        ("출처 RCP_SEQ", _recipe_source_label(recipe)),
        ("보유 재료", f"{len(recipe.owned_ingredients)}개 활용"),
    )
    for position, (label, value) in enumerate(info_cards):
        card_y = 625 - position * 47
        _round_panel(canvas, info_x, card_y, info_width, 40, _PANEL_DARK, _BORDER)
        _text(canvas, label, info_x + 12, card_y + 24, 8, _MUTED, font_name)
        _text(canvas, _ellipsis(value, font_name, 11, info_width - 24), info_x + 12, card_y + 8, 11, _TEXT, font_name)

    panel_width = (content_width - 14) / 2
    _ingredient_panel(canvas, _MARGIN, 330, panel_width, 160, "보유 재료", [f"✓ {item}" for item in recipe.owned_ingredients] or ["✓ 활용 가능한 재료 없음"], font_name)
    _ingredient_panel(canvas, _MARGIN + panel_width + 14, 330, panel_width, 160, "장보기 목록", [f"□ {item.ingredient} {item.amount}" for item in recipe.shopping_list] or ["□ 추가 장보기 없음"], font_name)
    _nutrition_bar(canvas, recipe, font_name)
    _draw_cooking_steps(canvas, recipe, font_name)
    _round_panel(canvas, _MARGIN, 100, content_width, 28, _PANEL)
    _text(canvas, "TIP", _MARGIN + 8 * mm, 110, 8, _ACCENT, font_name)
    _text(canvas, _ellipsis(_tip_text(recipe), font_name, 8.5, content_width - 48 * mm), _MARGIN + 28 * mm, 110, 8.5, _TEXT, font_name)
    _footer(canvas, index + 1, font_name)


def _draw_cooking_steps(canvas: Canvas, recipe: Recipe, font_name: str) -> None:
    """레시피 원본의 조리 단계를 번호 목록으로 표시합니다."""

    content_width = _PAGE_WIDTH - 2 * _MARGIN
    _text(canvas, "조리 단계", _MARGIN, 250, 15, _TEXT, font_name)
    _round_panel(canvas, _MARGIN, 139, content_width, 100, _PANEL_DARK)
    steps = recipe.cooking_steps[:7] or ["제공된 레시피 데이터에 조리 단계가 없습니다."]
    row_y = 215
    for index, step in enumerate(steps):
        _text(canvas, f"{index + 1:02d}", _MARGIN + 10, row_y, 8, _ACCENT, font_name)
        _text(
            canvas,
            _ellipsis(step, font_name, 7.5, content_width - 43),
            _MARGIN + 35,
            row_y + 1,
            7.5,
            _TEXT,
            font_name,
        )
        row_y -= 12


def _background(canvas: Canvas) -> None:
    canvas.setFillColor(_BACKGROUND)
    canvas.rect(0, 0, _PAGE_WIDTH, _PAGE_HEIGHT, fill=1, stroke=0)


def _round_panel(canvas: Canvas, x: float, y: float, width: float, height: float, fill: colors.Color, stroke: colors.Color | None = None) -> None:
    canvas.setFillColor(fill)
    canvas.setStrokeColor(stroke or fill)
    canvas.setLineWidth(1)
    canvas.roundRect(x, y, width, height, 12, fill=1, stroke=int(stroke is not None))


def _pill(canvas: Canvas, x: float, y: float, width: float, height: float, label: str, font_name: str, fill: colors.Color, text_color: colors.Color) -> None:
    _round_panel(canvas, x, y, width, height, fill)
    _text(canvas, label, x + (width - pdfmetrics.stringWidth(label, font_name, 8)) / 2, y + height / 2 - 3, 8, text_color, font_name)


def _text(canvas: Canvas, value: str, x: float, y: float, size: float, color: colors.Color, font_name: str) -> None:
    canvas.setFont(font_name, size)
    canvas.setFillColor(color)
    canvas.drawString(x, y, value)


def _ellipsis(value: str, font_name: str, size: float, max_width: float) -> str:
    if pdfmetrics.stringWidth(value, font_name, size) <= max_width:
        return value
    result = ""
    for character in value:
        if pdfmetrics.stringWidth(result + character + "...", font_name, size) > max_width:
            return result + "..."
        result += character
    return result


def _draw_recipe_image(canvas: Canvas, image_data: str | None, x: float, y: float, width: float, height: float, font_name: str) -> None:
    """원격 URL은 요청하지 않고 제한된 크기의 data URI만 렌더링합니다."""

    image = _data_uri_image(image_data)
    if image is not None:
        try:
            image_width, image_height = image.getSize()
            scale = max(width / image_width, height / image_height)
            draw_width = image_width * scale
            draw_height = image_height * scale
            clip = canvas.beginPath()
            clip.rect(x, y, width, height)
            canvas.saveState()
            canvas.clipPath(clip, stroke=0, fill=0)
            canvas.drawImage(
                image,
                x + (width - draw_width) / 2,
                y + (height - draw_height) / 2,
                width=draw_width,
                height=draw_height,
                mask="auto",
            )
            canvas.restoreState()
        except (AttributeError, TypeError, ZeroDivisionError):
            canvas.drawImage(image, x, y, width=width, height=height, mask="auto")
        return
    canvas.setFillColor(_PLACEHOLDER)
    canvas.rect(x, y, width, height, fill=1, stroke=0)
    _text(canvas, "RECIPE IMAGE", x + 12, y + height / 2 + 3, 8, _MUTED, font_name)
    _text(canvas, "이미지 준비 중", x + 12, y + height / 2 - 14, 10, _TEXT, font_name)


def _data_uri_image(image_data: str | None) -> ImageReader | None:
    if not image_data or not image_data.startswith("data:image/") or ";base64," not in image_data:
        return None
    try:
        decoded = base64.b64decode(image_data.split(";base64,", 1)[1], validate=True)
        return ImageReader(io.BytesIO(decoded)) if len(decoded) <= _MAX_RECIPE_IMAGE_BYTES else None
    except (ValueError, binascii.Error, OSError):
        return None


async def _embed_recipe_images(selected_set: RecipeSet) -> RecipeSet:
    """허용된 레시피 이미지 URL을 PDF 내부 data URI로 바꿉니다.

    외부 URL은 신뢰할 수 없는 입력이므로 내부 카탈로그의 공식 이미지 호스트와 이미지
    응답만 허용한다. 실패한 이미지 하나가 PDF 전체 생성을 막지 않도록 해당 카드만
    플레이스홀더를 유지한다.
    """

    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        recipes = await asyncio.gather(
            *(_embed_recipe_image(client, recipe) for recipe in selected_set.recipes)
        )
    return selected_set.model_copy(update={"recipes": recipes})


async def _embed_recipe_image(client: httpx.AsyncClient, recipe: Recipe) -> Recipe:
    """한 레시피의 안전한 이미지 URL을 내장 data URI로 변환합니다."""

    image_url = recipe.image
    if not _is_allowed_recipe_image_url(image_url):
        return recipe
    try:
        response = await client.get(image_url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if (
            not _is_allowed_recipe_image_url(str(response.url))
            or not content_type.startswith("image/")
            or len(response.content) > _MAX_RECIPE_IMAGE_BYTES
        ):
            return recipe
    except httpx.HTTPError:
        return recipe
    data_uri = f"data:{content_type};base64,{base64.b64encode(response.content).decode('ascii')}"
    return recipe.model_copy(update={"image": data_uri})


def _is_allowed_recipe_image_url(value: str | None) -> bool:
    """공식 레시피 카탈로그의 HTTPS/HTTP 이미지 URL만 허용합니다."""

    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname in _RECIPE_IMAGE_HOSTS


def _ingredient_panel(canvas: Canvas, x: float, y: float, width: float, height: float, title: str, entries: list[str], font_name: str) -> None:
    _round_panel(canvas, x, y, width, height, _PANEL)
    _text(canvas, title, x + 8 * mm, y + height - 28, 15, _TEXT, font_name)
    for index, entry in enumerate(entries[:6]):
        _text(canvas, _ellipsis(entry, font_name, 9, width - 16 * mm), x + 8 * mm, y + height - 55 - index * 18, 9, _TEXT, font_name)


def _nutrition_bar(canvas: Canvas, recipe: Recipe, font_name: str) -> None:
    x, y, width, height = _MARGIN, 274, _PAGE_WIDTH - 2 * _MARGIN, 42
    _round_panel(canvas, x, y, width, height, _PANEL_DARK, _BORDER)
    values = (("열량", f"{recipe.nutrition.calories:g} kcal"), ("단백질", f"{recipe.nutrition.protein:g} g"), ("탄수화물", f"{recipe.nutrition.carbohydrate:g} g"), ("지방", f"{recipe.nutrition.fat:g} g"))
    section = width / len(values)
    for index, (label, value) in enumerate(values):
        item_x = x + section * index
        if index:
            canvas.setStrokeColor(_BORDER)
            canvas.line(item_x, y + 7, item_x, y + height - 7)
        _text(canvas, label, item_x + 12, y + 25, 8, _MUTED, font_name)
        _text(canvas, value, item_x + 12, y + 10, 11, _TEXT, font_name)


def _cook_time_label(recipe: Recipe) -> str:
    return f"{recipe.cook_time}분" if recipe.cook_time else "미입력"


def _recipe_source_label(recipe: Recipe) -> str:
    """내부 CSV 레시피의 원본 번호를 PDF 정보 카드에 표시합니다."""

    return recipe.recipe_id or "미입력"


def _unique_tip_values(values: list[str]) -> list[str]:
    """팁에 표시할 재료명을 중복 없이 원래 순서대로 정리합니다."""

    unique_values: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in unique_values:
            unique_values.append(normalized)
    return unique_values


def _tip_values_text(values: list[str]) -> str:
    """한 줄 팁 영역에 들어갈 수 있도록 재료 목록을 간결하게 줄입니다."""

    visible_values = values[:3]
    summary = ", ".join(visible_values)
    remaining_count = len(values) - len(visible_values)
    return f"{summary} 외 {remaining_count}개" if remaining_count else summary


def _tip_text(recipe: Recipe) -> str:
    # 원본 CSV의 OPTIONAL_INGREDIENTS와
    # SUBSTITUTABLE_INGREDIENTS/SUBSTITUTABLE_SEASONINGS는 API 레시피 카드에
    # 별도 목록으로 보존한다. ``missing_ingredients``는 보유 재료를 제외한 목록이므로
    # 이 값만 사용하면 사용자가 이미 가진 선택·대체 재료가 팁에서 사라진다.
    optional_names = _unique_tip_values(
        [
            *recipe.optional_ingredients,
            *[
                item.name
                for item in recipe.missing_ingredients
                if item.importance in {"권장", "생략 가능"}
            ],
        ]
    )
    substitutable_names = _unique_tip_values(
        [
            *recipe.substitutable_ingredients,
            *(
                item.name
                for item in recipe.missing_ingredients
                if item.importance == "대체 가능"
            ),
        ]
    )
    alternative_pairs = _unique_tip_values(
        [
            f"{item.name} → {item.alternative}"
            for item in recipe.missing_ingredients
            if item.alternative
        ]
    )

    tip_parts: list[str] = []
    if optional_names:
        tip_parts.append(f"선택 재료: {_tip_values_text(optional_names)}")
    if substitutable_names:
        tip_parts.append(f"대체 가능: {_tip_values_text(substitutable_names)}")
    if alternative_pairs:
        tip_parts.append(f"대체안: {_tip_values_text(alternative_pairs)}")
    if tip_parts:
        return " · ".join(tip_parts)

    if not recipe.missing_ingredients:
        return "추가 장보기 없이 보유 재료로 조리할 수 있습니다. 기호에 따라 재료를 가감해 보세요."

    # CSV에 선택·대체 그룹이 없는 레시피는 필수 재료를 다시 보여주지 않는다.
    return "선택 재료나 대체 가능한 재료 정보가 없습니다. 장보기 목록을 확인해 주세요."


def _footer(canvas: Canvas, page: int, font_name: str) -> None:
    canvas.setStrokeColor(_BORDER)
    canvas.line(_MARGIN, 34, _PAGE_WIDTH - _MARGIN, 34)
    _text(canvas, "PlanEat  /  오늘의 식단", _MARGIN, 17, 8, _MUTED, font_name)
    _text(canvas, f"{page:02d}", _PAGE_WIDTH - _MARGIN - 10, 17, 8, _MUTED, font_name)


recipe_pdf_service = RecipePdfService(get_settings())
