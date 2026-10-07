import pytest

from brand_api.agent.confidence import ConfidenceFilter, combine, strip_confidence


def stream(chunks: list[str]) -> tuple[str, float | None]:
    marker = ConfidenceFilter()
    visible = "".join(marker.feed(c) for c in chunks) + marker.flush()
    return visible, marker.value


@pytest.mark.parametrize(
    "chunks",
    [
        ["Ответ [1].", "<confidence>0.8</confidence>"],
        ["Ответ [1].<conf", "idence>0.", "8</confi", "dence>"],
        ["Ответ [1]. <CONFIDENCE> 0,8 </CONFIDENCE>"],
    ],
)
def test_marker_is_hidden_however_it_is_split(chunks: list[str]) -> None:
    visible, value = stream(chunks)
    assert visible.strip() == "Ответ [1]."
    assert value == 0.8


def test_text_with_angle_brackets_is_not_swallowed() -> None:
    visible, value = stream(["Цвета: <b>", "терракота</b> и 2 < 3"])
    assert visible == "Цвета: <b>терракота</b> и 2 < 3"
    assert value is None


def test_unterminated_marker_at_the_end_is_still_parsed() -> None:
    visible, value = stream(["Готово.", "<confidence>0.65"])
    assert visible == "Готово."
    assert value == 0.65


def test_strip_and_clamp() -> None:
    assert strip_confidence("Да. <confidence>1.7</confidence>") == ("Да.", 1.0)
    assert strip_confidence("Без метки") == ("Без метки", None)


def test_combine_weights_and_threshold() -> None:
    result = combine(0.9, 0.4, weight=0.6, threshold=0.5)
    assert result.combined == pytest.approx(0.7)
    assert not result.low
    assert combine(0.3, None, weight=0.6, threshold=0.5).low
