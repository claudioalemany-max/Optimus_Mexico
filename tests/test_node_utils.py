from core.node_utils import normalize_node_code, extract_voltage, extract_codes_from_text


def test_normalize_node_code():
    assert normalize_node_code("01AAN-85") == "01AAN-85"
    assert normalize_node_code("01ACO230") == "01ACO-230"
    assert normalize_node_code("03MRA1115") == "03MRA1-115"


def test_extract_voltage():
    assert extract_voltage("01ACO-230") == "230"


def test_extract_codes_from_text():
    text = "1 01AAN-85 501.9 455.23 46.96 -0.3\n1 01ACO-230 466.24"
    assert "01AAN-85" in extract_codes_from_text(text)
    assert "01ACO-230" in extract_codes_from_text(text)
