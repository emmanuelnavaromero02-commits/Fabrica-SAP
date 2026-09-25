from fabrica.estimation.calculator import compute


def test_hours_follow_sizes_fixed_tasks_and_contingency() -> None:
    result = compute([{"object": "ZA", "size": "S"}, {"object": "ZB", "size": "L"}])
    assert result.breakdown["backend"] == 68
    assert result.breakdown["frontend"] == 24
    assert result.breakdown["pruebas"] == 20
    assert result.hours_base == 68 + 24 + 20 + 24
    assert result.hours_total == round(136 * 1.15, 2)
    assert result.days == round(result.hours_total / 8, 1)
    assert result.complexity == "L"
    assert [i["hours"] for i in result.items] == [16, 96]


def test_complexity_bands() -> None:
    assert compute([{"object": "ZA", "size": "XS"}]).complexity == "S"
    assert compute([{"object": "ZA", "size": "M"}]).complexity == "M"
    assert compute([{"object": "ZA", "size": "XL"}]).complexity == "L"
    assert compute([{"object": "Z", "size": "XL"}] * 2).complexity == "XL"
