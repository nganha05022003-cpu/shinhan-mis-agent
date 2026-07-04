"""
agent.py
Shinhan MIS Agent — function-calling loop.

Flow:
  1. User asks a question in plain text.
  2. Question + tool schema sent to OpenAI.
  3. If the model calls query_database, we run the SQL against shinhan_mis.db
     (read-only, SELECT-only — enforced by is_safe_query) and return the rows.
  4. Result sent back to OpenAI, which produces a natural-language answer.
  5. Loop continues until the model returns a plain text answer (no more tool calls).

Run:
  python3 agent/agent.py
Requires:
  OPENAI_API_KEY set as an environment variable.
"""

import os
import json
import sqlite3

from openai import OpenAI

# ---------------------------------------------------------
# Config
# ---------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "shinhan_mis.db")
MODEL = "gpt-4o-mini"  # swap for whatever model/tier you have access to

ALLOWED_TABLES = {"branches", "loans", "monthly_revenue", "npl_records"}

SCHEMA_DESCRIPTION = """
You are a banking BI assistant for Shinhan Vietnam branch managers.
You answer questions by writing a single SQLite SELECT statement and calling
the query_database tool. You only have access to these 4 tables:

branches(branch_id, branch_name, city, region, branch_size)
  - branch_size is one of: Flagship, Standard, Small
  - IMPORTANT: branch_name values in the database do NOT have Vietnamese diacritics,
    even though the user's question usually will. The 6 exact branch_name values are:
    "Chi nhanh Quan 1", "Chi nhanh Hoan Kiem", "Chi nhanh Hai Chau",
    "Chi nhanh Binh Duong", "Chi nhanh Long An", "Chi nhanh Thai Nguyen"
  - Never do an exact match against an accented name the user typed (e.g. "Chi nhánh
    Bình Dương"). Instead, match using LIKE with an unaccented keyword fragment from
    the list above, e.g. WHERE branch_name LIKE '%Binh Duong%'. If you are unsure how
    to strip accents yourself, just use the closest unaccented keyword from the list
    (city name or branch identifier) in a LIKE pattern rather than an exact equals.

loans(loan_id, branch_id, customer_id, customer_name, loan_type, amount,
      issue_date, due_date, repayment_status)
  - loan_type is one of: Mortgage, Personal, Business, Auto
  - repayment_status is CURRENT snapshot only: Performing, NPL, Closed
  - amount is in VND

monthly_revenue(revenue_id, branch_id, month, total_rev)
  - month format: "YYYY-MM"
  - total_rev is simulated interest income in VND for that branch/month

npl_records(npl_id, branch_id, month, total_loans_amount, npl_amount, npl_ratio)
  - month format: "YYYY-MM"
  - npl_ratio is already calculated as a percentage (npl_amount / total_loans_amount * 100)
    — use this column directly, do not recompute NPL ratio yourself

Rules:
- Only write SELECT statements. Never write INSERT/UPDATE/DELETE/DROP/ALTER.
- Join on branch_id when a question spans branches + loans/revenue/npl.
- For "current" NPL or portfolio questions, prefer loans.repayment_status.
- For "trend" or "over time" or "last N months" questions, prefer
  monthly_revenue / npl_records (already aggregated by month).
- Vietnamese phrasing "tổng số" / "số lượng" / "bao nhiêu khoản/cái" means COUNT
  the number of rows (COUNT(*)) — it does NOT mean sum of money. Only use
  SUM(amount) when the question asks for "tổng giá trị" / "tổng tiền" / a monetary
  total. If unsure which one is meant, prefer COUNT for "how many loans/branches"
  style questions and state your interpretation in the answer.
- If a question is ambiguous, make a reasonable assumption and state it in your answer.
- After you get query results, answer in natural language (Vietnamese or English,
  matching the user's question language). Do not just dump raw rows.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": (
                "Run a read-only SQL SELECT query against the shinhan_mis.db "
                "SQLite database (tables: branches, loans, monthly_revenue, "
                "npl_records) and return the resulting rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A single SQLite SELECT statement.",
                    }
                },
                "required": ["sql"],
            },
        },
    }
]


# ---------------------------------------------------------
# Guardrail placeholder (Output 3 will replace/extend this
# with the full is_safe_query() in agent/guardrail.py)
# ---------------------------------------------------------
def is_safe_query(sql: str) -> tuple[bool, str]:
    """Minimal safety check. Full version lives in Output 3."""
    normalized = sql.strip().lower()

    if not normalized.startswith("select"):
        return False, "Only SELECT statements are allowed."

    forbidden = ["insert", "update", "delete", "drop", "alter", "attach", "pragma", ";--"]
    if any(word in normalized for word in forbidden):
        return False, "Query contains a forbidden keyword."

    return True, ""


# ---------------------------------------------------------
# The tool itself
# ---------------------------------------------------------
def query_database(sql: str) -> str:
    """Execute a SELECT query and return results as a JSON string."""
    safe, reason = is_safe_query(sql)
    if not safe:
        return json.dumps({"error": reason})

    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(sql)
        rows = [dict(row) for row in cur.fetchall()]
        con.close()
        return json.dumps({"rows": rows, "row_count": len(rows)}, default=str)
    except sqlite3.Error as e:
        return json.dumps({"error": f"SQL error: {e}"})


# ---------------------------------------------------------
# Function-calling loop
# ---------------------------------------------------------
def ask_agent(client: OpenAI, question: str, verbose: bool = False) -> str:
    messages = [
        {"role": "system", "content": SCHEMA_DESCRIPTION},
        {"role": "user", "content": question},
    ]

    # Loop in case the model wants to call the tool more than once.
    for _ in range(5):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            return msg.content

        # Model wants to call query_database (possibly more than once)
        messages.append(msg)
        for tool_call in msg.tool_calls:
            args = json.loads(tool_call.function.arguments)
            sql = args.get("sql", "")

            if verbose:
                print(f"[tool call] SQL: {sql}")

            result = query_database(sql)

            if verbose:
                print(f"[tool result] {result[:300]}")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

    return "Sorry, I couldn't produce an answer within the allowed steps."


# ---------------------------------------------------------
# CLI entry point for manual testing
# ---------------------------------------------------------
def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY environment variable is not set.")

    client = OpenAI(api_key=api_key)

    print("Shinhan MIS Agent — type a question (or 'quit' to exit)\n")
    while True:
        question = input("Q: ").strip()
        if question.lower() in {"quit", "exit"}:
            break
        if not question:
            continue
        answer = ask_agent(client, question, verbose=True)
        print(f"A: {answer}\n")


if __name__ == "__main__":
    main()
