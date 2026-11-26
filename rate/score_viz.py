"""将单局表现评分渲染为可保存的 PNG。"""

from io import BytesIO

from PIL import Image, ImageColor, ImageDraw

from bot_viz import F, _fit
from .rate_core import ReplayRating, SCALE_VERSION


INK = "#1E252D"
MUTED = "#55616E"
LEFT, RIGHT = 40, 1160
FIRST_ROW, ROW_HEIGHT = 424, 59
PERCENTILE_TIERS = (
    (0, "#BF2C32"),
    (20, "#B5530A"),
    (40, "#A47800"),
    (50, "#4B7917"),
    (60, "#2D681C"),
    (70, "#0A718A"),
    (80, "#7742A4"),
    (90, "#50267D"),
    (95, None),  # 彩色渐变
)
GRADIENT_STOPS = tuple(
    ImageColor.getrgb(color)
    for color in ("#B886A4", "#CBA8C6", "#B3AACF", "#A5D8D7", "#81E3E2", "#7FDAAC", "#ADD78F")
)
GRADIENT_TEXT_OUTLINE = "#526071"


def _percentile_color(value):
    return next(color for minimum, color in reversed(PERCENTILE_TIERS) if value >= minimum)


def _horizontal_gradient(size):
    width, height = size
    gradient = Image.new("RGB", size)
    for x in range(width):
        position = x * (len(GRADIENT_STOPS) - 1) / max(1, width - 1)
        index = min(int(position), len(GRADIENT_STOPS) - 2)
        amount = position - index
        color = tuple(
            round(start + (end - start) * amount)
            for start, end in zip(GRADIENT_STOPS[index], GRADIENT_STOPS[index + 1])
        )
        gradient.paste(color, (x, 0, x + 1, height))
    return gradient


def render_score_png(report: ReplayRating, *, preview=False) -> bytes:
    height = FIRST_ROW + len(report.players) * ROW_HEIGHT + 40
    image = Image.new("RGB", (1200, height), "#F2F3F5")
    draw = ImageDraw.Draw(image)

    def label(x, y, value, size, color=INK, bold=False, anchor="lm", max_width=None):
        if max_width is None:
            font = F(size, bold)
        else:
            value, font = _fit(draw, value, max_width, size, bold)
        if color is not None:
            draw.text((x, y), value, font=font, fill=color, anchor=anchor)
            return

        left, top, right, bottom = draw.textbbox((0, 0), value, font=font, anchor=anchor)
        mask = Image.new("L", (right - left, bottom - top))
        ImageDraw.Draw(mask).text((-left, -top), value, font=font, fill=255, anchor=anchor)
        draw.text(
            (x, y), value, font=font, fill=GRADIENT_TEXT_OUTLINE,
            anchor=anchor, stroke_width=1,
        )
        image.paste(_horizontal_gradient(mask.size), (x + left, y + top), mask)

    def band(x, y, width, color):
        radius = min(4, (width - 1) // 2)
        if color is not None:
            draw.rounded_rectangle((x, y, x + width - 1, y + 8), radius=radius, fill=color)
            return

        mask = Image.new("L", (width, 9))
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, 8), radius=radius, fill=255)
        image.paste(_horizontal_gradient(mask.size), (x, y), mask)

    draw.rectangle((24, 24, 1175, height - 25), fill="white")
    label(LEFT, 51, "ASTRAL PARTY", 20, MUTED, bold=True)
    label(LEFT, 101, "单局表现评分", 49, bold=True)
    if preview:
        draw.rounded_rectangle((1031, 80, RIGHT, 119), radius=6, fill="#F1F3F5")
        label(1096, 100, "模拟数据", 21, MUTED, bold=True, anchor="mm")

    draw.rectangle((LEFT, 143, RIGHT, 198), fill="#2D791F")
    label(LEFT + 16, 171, "PVE · 森秋幻境", 28, "white", bold=True)
    label(RIGHT - 16, 171, "难度  疯狂", 26, "white", bold=True, anchor="rm")

    scored = sum(player.score is not None for player in report.players)
    for x1, x2, heading, value in (
        (LEFT, 464, "回放号", report.replay_id),
        (476, 749, "评分人数", f"{scored} / {len(report.players)}"),
        (761, RIGHT, "评分量表", f"36 角色 {SCALE_VERSION}"),
    ):
        draw.rectangle((x1, 214, x2, 308), fill="#F1F3F5")
        draw.line((x1, 308, x2, 308), fill="#92B5C8", width=2)
        label(x1 + 16, 237, heading, 21, MUTED, bold=True)
        label(x1 + 16, 276, value, 31, bold=True)

    draw.rectangle((LEFT, 332, LEFT + 7, 359), fill=INK)
    label(LEFT + 20, 345, "玩家评分", 28, bold=True)
    draw.line((LEFT, 370, RIGHT, 370), fill="#D5DADE", width=2)
    draw.rectangle((LEFT, 377, RIGHT, FIRST_ROW - 1), fill="#E7EBEE")
    label(LEFT + 16, 400, "玩家", 22, MUTED, bold=True)
    label(316, 400, "角色", 22, MUTED, bold=True)
    label(566, 400, "本局评分", 22, MUTED, bold=True, anchor="mm")
    label(675, 400, "同条件百分位", 22, MUTED, bold=True)
    label(1065, 400, "量表提示", 22, MUTED, bold=True, anchor="mm")

    for index, player in enumerate(report.players):
        top = FIRST_ROW + index * ROW_HEIGHT
        center = top + ROW_HEIGHT // 2
        background = "#F1F3F5" if index % 2 == 0 else "#FAFBFC"
        draw.rectangle((LEFT, top, RIGHT, top + ROW_HEIGHT - 2), fill=background)
        label(LEFT + 16, center, " ".join(player.nick.splitlines()), 27, bold=True, max_width=247)
        label(316, center, player.hero, 24, max_width=198)
        if player.score is None or player.percentile is None:
            label(566, center, "—", 28, MUTED, anchor="mm")
            label(804, center, "—", 28, MUTED, anchor="rm")
            label(1065, center, "无法评分", 22, MUTED, anchor="mm")
            continue

        displayed_percentile = f"{player.percentile:.2f}"
        color = _percentile_color(float(displayed_percentile))
        label(566, center, f"{player.score:.2f}", 34, color, bold=True, anchor="mm")
        label(804, center, f"{displayed_percentile}%", 32, color, bold=True, anchor="rm")
        band(824, center - 4, 120, "#D8DEE3")
        length = round(120 * player.percentile / 100)
        if length:
            band(824, center - 4, length, color)
        label(
            1065, center, player.usage or "—", 22, "#8B4A16" if player.usage else MUTED,
            bold=bool(player.usage), anchor="mm", max_width=170,
        )

    with BytesIO() as output:
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()
