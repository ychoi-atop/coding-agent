from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from library.core import ValidationError, normalize_identifier, split_csv_items


def test_normalize_identifier_basic():
    assert normalize_identifier("  My Value ") == "my_value"


def test_normalize_identifier_rejects_bad_input():
    try:
        normalize_identifier("a.b")
    except ValidationError as exc:
        assert "dots" in str(exc)
    else:
        assert False, "Expected ValidationError"


def test_split_csv_items_handles_noise():
    assert split_csv_items("a, b,, c ") == ["a", "b", "c"]
    assert split_csv_items("") == []


def test_split_csv_items_rejects_non_string():
    try:
        split_csv_items(10)
    except ValidationError:
        pass
    else:
        assert False, "Expected ValidationError"
