"""Optional live end-to-end scenario suite against /api/v1/generate.

This suite sends ONLY the public payload:
  user_query, enable_summary, and summary after the first turn.

Run:
  RUN_LIVE_LLM_TESTS=1
  TEST_API_URL=http://localhost:8000/api/v1/generate
  TEST_ACCESS_TOKEN=<gateway access token>
  poetry run pytest tests/test_nlp_scenarios.py -v -s
"""
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Set

import httpx
import pytest

RUN_LIVE = os.getenv("RUN_LIVE_LLM_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_LIVE,
    reason="Set RUN_LIVE_LLM_TESTS=1 to run live LLM integration scenarios.",
)


@dataclass
class Step:
    query: str
    tables: Dict[str, Set[str]]
    refs: List[str] = field(default_factory=list)
    absent_tables: Set[str] = field(default_factory=set)
    absent_columns: Dict[str, Set[str]] = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    steps: List[Step]


def C(text: str) -> Set[str]:
    return set(text.split()) if text else set()


def S(query, tables, refs=None, absent_tables=None, absent_columns=None):
    return Step(
        query=query,
        tables={name: C(cols) for name, cols in tables.items()},
        refs=refs or [],
        absent_tables=set(absent_tables or []),
        absent_columns={name: C(cols) for name, cols in (absent_columns or {}).items()},
    )


SCENARIOS = [
    Scenario("01_basic_create_alter", [
        S("Create an employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Add email column to it.", {"employee": "id name salary email"}),
        S("Add phone column to it.", {"employee": "id name salary email phone"}),
        S("Add department_id as a foreign key", {"employee": "id name salary email phone department_id", "department": "id"}, ["employee.department_id>department.id"]),
    ]),
    Scenario("02_multiple_create_alter_drop_retain", [
        S("Create an employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Add department_id as a foreign key", {"employee": "id name salary department_id", "department": "id"}, ["employee.department_id>department.id"]),
        S("Add sort order column in department table.", {"employee": "id name salary department_id", "department": "id sort_order"}, ["employee.department_id>department.id"]),
        S("drop employee table", {"department": "id sort_order"}, absent_tables={"employee"}),
        S("drop department table", {}, absent_tables={"employee", "department"}),
        S("create company table with id as primary_key, name as varchar", {"company": "id name"}, absent_tables={"employee", "department"}),
        S("retain that tables", {"company": "id name", "employee": "id name salary department_id", "department": "id sort_order"}, ["employee.department_id>department.id"]),
    ]),
    Scenario("03_multiple_independent_tables", [
        S("Create employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Create customer table with id, name and email.", {"employee": "id name salary", "customer": "id name email"}),
        S("Create product table with id, name and price.", {"employee": "id name salary", "customer": "id name email", "product": "id name price"}),
        S("Add phone column to customer table.", {"employee": "id name salary", "customer": "id name email phone", "product": "id name price"}),
        S("Add stock column to product table.", {"employee": "id name salary", "customer": "id name email phone", "product": "id name price stock"}),
        S("Add department column to employee table.", {"employee": "id name salary department", "customer": "id name email phone", "product": "id name price stock"}),
    ]),
    Scenario("04_drop_retain_specific", [
        S("Create employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Add email column to employee table.", {"employee": "id name salary email"}),
        S("Create customer table with id, name and email.", {"employee": "id name salary email", "customer": "id name email"}),
        S("Add phone column to customer table.", {"employee": "id name salary email", "customer": "id name email phone"}),
        S("drop employee table", {"customer": "id name email phone"}, absent_tables={"employee"}),
        S("retain the employee table", {"employee": "id name salary email", "customer": "id name email phone"}),
    ]),
    Scenario("05_alter_drop_create_retain", [
        S("Create employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Add email column to employee table.", {"employee": "id name salary email"}),
        S("Add phone column to employee table.", {"employee": "id name salary email phone"}),
        S("drop employee table", {}, absent_tables={"employee"}),
        S("create company table with id as primary key and name as varchar.", {"company": "id name"}, absent_tables={"employee"}),
        S("add address column to company table.", {"company": "id name address"}, absent_tables={"employee"}),
        S("retain the employee table", {"company": "id name address", "employee": "id name salary email phone"}),
    ]),
    Scenario("06_foreign_key_dependency", [
        S("Create employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Add department_id as a foreign key.", {"employee": "id name salary department_id", "department": "id"}, ["employee.department_id>department.id"]),
        S("Add manager_id as a foreign key to employee.", {"employee": "id name salary department_id manager_id", "department": "id", "manager": "id"}, ["employee.department_id>department.id", "employee.manager_id>manager.id"]),
        S("Add sort_order column to department table.", {"employee": "id name salary department_id manager_id", "department": "id sort_order", "manager": "id"}, ["employee.department_id>department.id", "employee.manager_id>manager.id"]),
        S("drop employee table", {"department": "id sort_order", "manager": "id"}, absent_tables={"employee"}),
    ]),
    Scenario("07_drop_parent_child", [
        S("Create department table with id and name.", {"department": "id name"}),
        S("Create employee table with id, name and salary.", {"department": "id name", "employee": "id name salary"}),
        S("Add department_id as a foreign key to employee.", {"department": "id name", "employee": "id name salary department_id"}, ["employee.department_id>department.id"]),
        S("drop employee table", {"department": "id name"}, absent_tables={"employee"}),
        S("drop department table", {}, absent_tables={"employee", "department"}),
    ]),
    Scenario("08_recreate_after_drop", [
        S("Create employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Add email column to employee table.", {"employee": "id name salary email"}),
        S("drop employee table", {}, absent_tables={"employee"}),
        S("create employee table with id and name.", {"employee": "id name"}, absent_columns={"employee": "salary email"}),
    ]),
    Scenario("09_repeated_alter", [
        S("Create employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Add email column to employee table.", {"employee": "id name salary email"}),
        S("Add phone column to employee table.", {"employee": "id name salary email phone"}),
        S("Add address column to employee table.", {"employee": "id name salary email phone address"}),
        S("Add joining_date column to employee table.", {"employee": "id name salary email phone address joining_date"}),
        S("Add status column to employee table.", {"employee": "id name salary email phone address joining_date status"}),
    ]),
    Scenario("10_multiple_drop_retain_specific", [
        S("Create employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Create department table with id and name.", {"employee": "id name salary", "department": "id name"}),
        S("Create company table with id and name.", {"employee": "id name salary", "department": "id name", "company": "id name"}),
        S("Add department_id as a foreign key to employee.", {"employee": "id name salary department_id", "department": "id name", "company": "id name"}, ["employee.department_id>department.id"]),
        S("drop employee table", {"department": "id name", "company": "id name"}, absent_tables={"employee"}),
        S("drop department table", {"company": "id name"}, absent_tables={"employee", "department"}),
        S("drop company table", {}, absent_tables={"employee", "department", "company"}),
        S("retain the department table", {"department": "id name"}, absent_tables={"employee", "company"}),
    ]),
    Scenario("11_create_alter_drop_multiple_create", [
        S("Create employee table with id, name and salary.", {"employee": "id name salary"}),
        S("Add email column to employee table.", {"employee": "id name salary email"}),
        S("drop employee table", {}, absent_tables={"employee"}),
        S("create customer table with id, name and email.", {"customer": "id name email"}, absent_tables={"employee"}),
        S("create product table with id, name and price.", {"customer": "id name email", "product": "id name price"}, absent_tables={"employee"}),
        S("add stock column to product table.", {"customer": "id name email", "product": "id name price stock"}, absent_tables={"employee"}),
    ]),
    Scenario("12_full_end_to_end", [
        S("Create department table with id and name.", {"department": "id name"}),
        S("Add sort_order column to department table.", {"department": "id name sort_order"}),
        S("Create employee table with id, name and salary.", {"department": "id name sort_order", "employee": "id name salary"}),
        S("Add department_id as a foreign key to employee.", {"department": "id name sort_order", "employee": "id name salary department_id"}, ["employee.department_id>department.id"]),
        S("Add email column to employee table.", {"department": "id name sort_order", "employee": "id name salary department_id email"}, ["employee.department_id>department.id"]),
        S("drop employee table", {"department": "id name sort_order"}, absent_tables={"employee"}),
        S("create company table with id as primary key and name as varchar.", {"department": "id name sort_order", "company": "id name"}, absent_tables={"employee"}),
        S("Add address column to company table.", {"department": "id name sort_order", "company": "id name address"}, absent_tables={"employee"}),
        S("drop department table", {"company": "id name address"}, absent_tables={"employee", "department"}),
        S("retain the employee table", {"company": "id name address", "department": "id name sort_order", "employee": "id name salary department_id email"}, ["employee.department_id>department.id"]),
    ]),
]


def _tables(dbml: str) -> Dict[str, str]:
    pattern = re.compile(r"\bTable\s+([A-Za-z_]\w*)\s*\{(.*?)\}", re.I | re.S)
    return {m.group(1).lower(): m.group(2) for m in pattern.finditer(dbml or "")}


def _has(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text or "", re.I))


def _refs(dbml: str) -> Set[str]:
    pattern = re.compile(r"\bRef\s*:\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*>\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)", re.I)
    return {f"{a.lower()}.{b.lower()}>{c.lower()}.{d.lower()}" for a, b, c, d in pattern.findall(dbml or "")}


def _assert_state(step: Step, dbml: str):
    tables = _tables(dbml)
    assert set(tables) == {x.lower() for x in step.tables}, dbml
    for table, columns in step.tables.items():
        for column in columns:
            assert _has(tables[table.lower()], column), f"missing {table}.{column}: {dbml}"
    for table in step.absent_tables:
        assert table.lower() not in tables
    for table, columns in step.absent_columns.items():
        for column in columns:
            assert not _has(tables.get(table.lower(), ""), column)
    assert _refs(dbml) == {ref.lower().replace(" ", "") for ref in step.refs}


def _call_api(query: str, summary: str | None) -> dict:
    url = os.getenv("TEST_API_URL", "http://localhost:8000/api/v1/generate")
    token = os.getenv("TEST_ACCESS_TOKEN")
    if not token:
        pytest.fail("TEST_ACCESS_TOKEN is required for live tests")

    payload = {"user_query": query, "enable_summary": True}
    if summary:
        payload["summary"] = summary

    response = httpx.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=90,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_all_nlp_scenarios(scenario: Scenario):
    summary = None
    for number, step in enumerate(scenario.steps, 1):
        result = _call_api(step.query, summary)
        _assert_state(step, result["dbml_query"])
        assert isinstance(result["used_tokens"], int) and result["used_tokens"] >= 0
        assert "\n" not in result["updated_summary"]
        assert "\t" not in result["updated_summary"]
        assert "\n" not in result["explanation"]
        assert "request summary:" in result["updated_summary"].lower()
        assert "current structure:" in result["updated_summary"].lower()
        summary = result["updated_summary"]
        print(f"[PASS] {scenario.name} step {number}/{len(scenario.steps)} tokens={result['used_tokens']}")


def test_scenario_count():
    assert len(SCENARIOS) == 12


def test_total_query_count():
    assert sum(len(s.steps) for s in SCENARIOS) == 74
