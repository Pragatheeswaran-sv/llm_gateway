import json

from src.utils.helper import parse_dbml_response


def test_summary_and_explanation_are_single_line_but_dbml_keeps_newlines():
    raw = json.dumps(
        {
            "summary": "Request Summary: Created employee.\nCurrent Structure: employee(id int PK).",
            "dbml": "Table employee {\n  id int [pk]\n}",
            "explanation": "Created employee.\nAdded primary key.",
        }
    )
    result = parse_dbml_response(raw)
    assert "\n" in result["dbml_query"]
    assert "\n" not in result["updated_summary"]
    assert "\n" not in result["explanation"]
