from api.routers.cron import _build_schedule


def test_build_schedule_defaults_to_all_stars():
    assert _build_schedule("", "", "", "", "", "") == "* * * * *"


def test_build_schedule_from_fields():
    assert _build_schedule("*/5", "2", "*", "*", "1-5", "") == "*/5 2 * * 1-5"


def test_build_schedule_raw_override_takes_precedence():
    assert _build_schedule("*/5", "2", "*", "*", "1-5", "0 0 1 1 *") == "0 0 1 1 *"


def test_build_schedule_blank_field_becomes_star():
    assert _build_schedule("30", "  ", "*", "*", "*", "") == "30 * * * *"
