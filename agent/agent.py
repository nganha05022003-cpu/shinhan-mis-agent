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
import re
import json
import time
import sqlite3
import statistics

import matplotlib
matplotlib.use("Agg")  # no display needed — we only save PNG files
import matplotlib.pyplot as plt

from openai import OpenAI

# ---------------------------------------------------------
# Config
# ---------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "shinhan_mis.db")
MODEL = "gpt-4o-mini"  # swap for whatever model/tier you have access to
ANOMALY_STDEV_THRESHOLD = 2  # flag npl_ratio values more than N stdevs above the mean
CHARTS_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs", "charts")

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
- For questions asking about "bất thường" / "anomaly" / "outlier" in NPL ratio,
  call detect_anomalies instead of writing your own SQL threshold — it uses a
  real statistical rule (mean + 2 standard deviations), not a guessed number.
  The tool returns the full list of outliers plus the mean/threshold used, so
  you can also confirm a branch is NOT anomalous if it's absent from that list.
- For questions that ask to "vẽ biểu đồ" / "so sánh ... bằng biểu đồ" / "chart" /
  "graph" / explicitly want a visual comparison or trend picture, call
  generate_chart instead of (or in addition to) query_database. Write a SQL
  query whose FIRST column is the label (e.g. branch_name or month) and SECOND
  column is the numeric value to plot. Do NOT call generate_chart for a plain
  question that just wants one number or a short text answer — only call it
  when the user explicitly wants a chart/visual.
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
    },
    {
        "type": "function",
        "function": {
            "name": "detect_anomalies",
            "description": (
                "Detect branch/month combinations where npl_ratio is a statistical "
                "outlier — more than 2 standard deviations above the mean across all "
                "branches and months in npl_records. Uses a fixed statistical rule "
                "(z-score style threshold), not a model-guessed cutoff. Returns the "
                "mean, standard deviation, threshold used, and the full list of "
                "outlier rows (branch_name, month, npl_ratio). No arguments needed — "
                "it always scans the full npl_records table."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_chart",
            "description": (
                "Run a SQL SELECT query and render the results as a chart (PNG file). "
                "The query's first column must be the label (x-axis), second column "
                "the numeric value (y-axis). Saves the chart to outputs/charts/ and "
                "returns the file path. Use only when the user explicitly wants a "
                "chart/graph/visual comparison, not for plain text answers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": (
                            "SQLite SELECT statement. First column = label, "
                            "second column = numeric value to plot."
                        ),
                    },
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line"],
                        "description": (
                            "'line' for trends over time (e.g. revenue by month), "
                            "'bar' for comparisons across categories (e.g. branches)."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Chart title, in the same language as the question.",
                    },
                },
                "required": ["sql", "chart_type", "title"],
            },
        },
    },
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
# detect_anomalies tool: fixed statistical rule (mean + N*stdev),
# not an LLM-guessed threshold. Reads npl_records directly — this is a
# known, safe, hardcoded query (not user-supplied SQL), so it bypasses
# is_safe_query() the same way query_database's internals never let
# arbitrary strings through unchecked.
# ---------------------------------------------------------
def detect_anomalies() -> str:
    """Flag branch/month npl_ratio values more than ANOMALY_STDEV_THRESHOLD
    standard deviations above the mean. Returns JSON with mean, stdev,
    threshold, and the list of outlier rows."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            """
            SELECT b.branch_name, n.month, n.npl_ratio
            FROM npl_records n
            JOIN branches b ON b.branch_id = n.branch_id
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        con.close()
    except sqlite3.Error as e:
        return json.dumps({"error": f"SQL error: {e}"})

    if not rows:
        return json.dumps({"error": "No npl_records data found."})

    values = [r["npl_ratio"] for r in rows]
    mean = statistics.mean(values)
    stdev = statistics.pstdev(values)
    threshold = mean + ANOMALY_STDEV_THRESHOLD * stdev

    outliers = [r for r in rows if r["npl_ratio"] > threshold]

    return json.dumps(
        {
            "mean": round(mean, 2),
            "stdev": round(stdev, 2),
            "threshold": round(threshold, 2),
            "outlier_count": len(outliers),
            "outliers": outliers,
        },
        default=str,
    )


# ---------------------------------------------------------
# generate_chart tool: runs a query (through the same guardrail as
# query_database — user-supplied SQL is never trusted just because it's
# going into a chart instead of a text answer), then renders + saves a PNG.
# agent.py only CREATES the file. Displaying it is Output 4's job (Streamlit
# reads the returned path with st.image()) — see masterplan.md architecture note.
# ---------------------------------------------------------
def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", text)[:50] or "chart"


def generate_chart(sql: str, chart_type: str, title: str) -> str:
    """Run sql, plot first column (labels) vs second column (values) as a
    bar or line chart, save PNG to outputs/charts/, return path + metadata."""
    safe, reason = is_safe_query(sql)
    if not safe:
        return json.dumps({"error": reason})

    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        con.close()
    except sqlite3.Error as e:
        return json.dumps({"error": f"SQL error: {e}"})

    if not rows:
        return json.dumps({"error": "Query returned no rows to chart."})

    columns = list(rows[0].keys())
    if len(columns) < 2:
        return json.dumps({"error": "Query must return at least 2 columns (label, value)."})

    label_col, value_col = columns[0], columns[1]
    labels = [str(r[label_col]) for r in rows]
    values = [r[value_col] for r in rows]

    try:
        values = [float(v) for v in values]
    except (TypeError, ValueError):
        return json.dumps({"error": f"Second column '{value_col}' is not numeric."})

    # Auto-scale large VND amounts so the y-axis shows readable units
    # (tỷ/triệu) instead of matplotlib's ambiguous "1e9" scientific offset.
    max_abs = max(abs(v) for v in values) if values else 0
    if max_abs >= 1_000_000_000:
        scale, unit = 1_000_000_000, "tỷ"
    elif max_abs >= 1_000_000:
        scale, unit = 1_000_000, "triệu"
    else:
        scale, unit = 1, ""
    scaled_values = [v / scale for v in values]
    ylabel = f"{value_col} ({unit} VND)" if unit else value_col

    fig, ax = plt.subplots(figsize=(9, 5))
    if chart_type == "line":
        ax.plot(labels, scaled_values, marker="o")
    else:
        ax.bar(labels, scaled_values)
    ax.set_title(title)
    ax.set_xlabel(label_col)
    ax.set_ylabel(ylabel)
    ax.ticklabel_format(style="plain", axis="y")  # never show ambiguous 1eN offset
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()

    os.makedirs(CHARTS_DIR, exist_ok=True)
    filename = f"{_slugify(title)}_{int(time.time())}.png"
    filepath = os.path.join(CHARTS_DIR, filename)
    fig.savefig(filepath)
    plt.close(fig)

    return json.dumps(
        {
            "file_path": os.path.abspath(filepath),
            "chart_type": chart_type,
            "title": title,
            "data_points": len(rows),
        }
    )


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

        # Model wants to call one or more tools
        messages.append(msg)
        for tool_call in msg.tool_calls:
            tool_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments or "{}")

            if verbose:
                print(f"[tool call] {tool_name}({args})")

            if tool_name == "query_database":
                result = query_database(args.get("sql", ""))
            elif tool_name == "detect_anomalies":
                result = detect_anomalies()
            elif tool_name == "generate_chart":
                result = generate_chart(
                    args.get("sql", ""),
                    args.get("chart_type", "bar"),
                    args.get("title", "Chart"),
                )
            else:
                result = json.dumps({"error": f"Unknown tool: {tool_name}"})

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
