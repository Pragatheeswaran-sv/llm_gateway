# NLP-to-DBML POC — Scenario Inputs and Expected Results

The live integration suite in `test_nlp_scenarios.py` executes all 12 conversations sequentially. The first call sends no `summary`; every later call sends the prior `updated_summary`. Exact varchar lengths, decimal precision and constraint names may vary. The required behavior is the resulting active schema state, DROP/RETAIN memory, and valid foreign-key relationships.

## Public payload contract

First turn:

```json
{
  "user_query": "Create an employee table with id, name and salary.",
  "enable_summary": true
}
```

Follow-up turn:

```json
{
  "user_query": "Add email column to employee table.",
  "enable_summary": true,
  "summary": "Request Summary: Create an employee table with id, name and salary. Current Structure: employee table with columns id int primary key, name varchar, salary decimal."
}
```

Expected response shape:

```json
{
  "message": "DBML generated successfully",
  "status_code": 200,
  "data": {
    "dbml_query": "Table employee {\n  id int [pk]\n  name varchar\n  salary decimal\n}",
    "updated_summary": "Request Summary: Created employee table with id, name and salary. Current Structure: employee(id int PK, name varchar, salary decimal).",
    "explanation": "Created employee table with id as primary key, name and salary.",
    "used_tokens": 1200
  }
}
```

`updated_summary` and `explanation` must be single-line strings. `dbml_query` may contain normal line breaks.

---

## Scenario 1 — Basic CREATE + ALTER

1. `Create an employee table with id, name and salary.` → `employee(id, name, salary)`
2. `Add email column to it.` → `employee(id, name, salary, email)`
3. `Add phone column to it.` → `employee(id, name, salary, email, phone)`
4. `Add department_id as a foreign key` → `employee(id, name, salary, email, phone, department_id)`, `department(id)`, and `Ref: employee.department_id > department.id`

## Scenario 2 — Multiple CREATE + ALTER + DROP + RETAIN

1. Create `employee(id, name, salary)`.
2. Add `department_id`; create `department(id)`; add employee → department Ref.
3. Add `department.sort_order`.
4. Drop employee; department remains.
5. Drop department; no active employee/department tables remain.
6. Create `company(id, name)`.
7. `retain that tables` restores the last complete employee and department definitions while company remains active. The employee → department Ref is restored.

## Scenario 3 — Multiple Independent Tables

Final active state after all six requests:

- `employee(id, name, salary, department)`
- `customer(id, name, email, phone)`
- `product(id, name, price, stock)`

No unrelated table may disappear while another table is altered.

## Scenario 4 — Drop and Retain One Specific Table

Before DROP:

- `employee(id, name, salary, email)`
- `customer(id, name, email, phone)`

After `drop employee table`, only customer is active. After `retain the employee table`, employee returns with `id, name, salary, email`; customer remains unchanged.

## Scenario 5 — ALTER → DROP → CREATE Another Table → RETAIN

Final active state:

- `company(id, name, address)`
- restored `employee(id, name, salary, email, phone)`

The employee definition must be the last definition before its DROP.

## Scenario 6 — Foreign-Key Dependency

Before the final DROP:

- `employee(id, name, salary, department_id, manager_id)`
- `department(id, sort_order)`
- `manager(id)`
- `Ref: employee.department_id > department.id`
- `Ref: employee.manager_id > manager.id`

After dropping employee, department and manager remain active and employee-related Refs disappear.

## Scenario 7 — Drop Parent and Child Tables

1. `department(id, name)`
2. add `employee(id, name, salary)`
3. add employee.department_id Ref to department.id
4. drop employee → department remains
5. drop department → no active tables remain

## Scenario 8 — Recreate After Drop

Old employee before DROP: `employee(id, name, salary, email)`.

After explicit `create employee table with id and name`, the new active schema is only `employee(id, name)`. `salary` and `email` must **not** be restored because this is a new CREATE, not RETAIN.

## Scenario 9 — Repeated ALTER Operations

Final employee columns:

`id, name, salary, email, phone, address, joining_date, status`

Every ALTER must preserve all prior columns.

## Scenario 10 — Multiple DROP + Retain Specific Table

Create employee, department and company; add employee → department FK; then drop employee, department and company. `retain the department table` restores only `department(id, name)`. Employee and company remain dropped.

## Scenario 11 — CREATE → ALTER → DROP → Multiple CREATE

Final active state:

- `customer(id, name, email)`
- `product(id, name, price, stock)`

Employee remains dropped, but its last definition `employee(id, name, salary, email)` must remain recoverable through summary memory for a future RETAIN.

## Scenario 12 — Full End-to-End Dependency-Aware RETAIN

Before the final retain, active schema is only `company(id, name, address)` and dropped memory contains:

- `department(id, name, sort_order)`
- `employee(id, name, salary, department_id, email)` with employee.department_id → department.id

`retain the employee table` uses dependency-aware behavior: restore department first, then employee, and restore the FK relationship.

Final active state:

```dbml
Table department {
  id int [pk]
  name varchar
  sort_order int
}

Table employee {
  id int [pk]
  name varchar
  salary decimal
  department_id int
  email varchar
}

Table company {
  id int [pk]
  name varchar
  address varchar
}

Ref: employee.department_id > department.id
```

---

# Fallback Expectations

The gateway owns provider/model/API-key selection. The public request never sends model, API key or base URL.

Generation fallback order used by the POC:

1. `openai/gpt-oss-120b`
2. `qwen/qwen3.8-27b`
3. `openai/gpt-oss-20b`
4. `allam-2-7b`

Prompt Guard and Safeguard rows may exist in `llm_fallback_models`, but are not DBML generation fallbacks.

Before each model call the gateway checks:

- `minute_requests < rpm_limit`
- projected `minute_tokens <= tpm_limit`
- `used_requests < daily_request_limit`
- projected `used_tokens <= daily_token_limit`
- `status == ACTIVE`
- no active cooldown

Expected fallback cases:

- RPM exhausted → skip model, choose next eligible generation model.
- TPM insufficient for estimated request → skip model, choose next eligible generation model.
- RPD exhausted → skip model until daily counters reset.
- TPD insufficient → skip model until daily counters reset.
- Provider HTTP 429 → set `is_rate_limited`, set `cooldown_until`, then try next model.
- 401/403/404 → mark model `UNAVAILABLE`, then try next model.
- timeout/5xx/498 → try next model after the controlled provider failure.
- invalid generated response → token usage is still counted; try the next generation model.
- all candidates unavailable → endpoint returns HTTP 503.

Successful calls increment `used_requests`, `used_tokens`, `minute_requests`, `minute_tokens`, update `last_used_at`, and return actual `used_tokens` from provider usage metadata.
