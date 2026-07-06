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
import pandas as pd

from openai import OpenAI

# ---------------------------------------------------------
# Config
# ---------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "shinhan_mis.db")
MODEL = "gpt-4o-mini"  # swap for whatever model/tier you have access to
ANOMALY_STDEV_THRESHOLD = 2  # flag npl_ratio values more than N stdevs above the mean
CHARTS_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs", "charts")
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs", "reports")

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
- For questions that ask to "xuất file" / "tạo báo cáo" / "tải xuống" / "report"
  / explicitly want a downloadable file (Excel/CSV) rather than a chat answer,
  call generate_report. Pick a short, descriptive filename (no spaces needed —
  underscores are fine) that reflects what the report contains. Do NOT call
  generate_report for a plain question that just wants a quick answer in chat.
- For "nếu ... thì" / "giả sử" / "what if" / "scenario" questions about NPL
  ratio increasing and its effect on revenue, call whatif_npl_scenario instead
  of writing your own SQL or estimating a number yourself. It returns a
  transparent projection (current numbers, the yield-rate assumption used,
  and the projected outcome) computed from that branch's real data — present
  those numbers and briefly explain the assumption stated in the result,
  do not invent your own formula or additional assumptions.
- IMPORTANT for generate_chart and generate_report: after the tool succeeds,
  do NOT include the file path, a markdown image link (![...](...)), or any
  local filesystem path in your answer text. The calling application already
  displays the chart image / download button separately using the returned
  file path — repeating it as text or a broken markdown link only leaks local
  paths and confuses the user. Just briefly confirm in words what was created
  (e.g. "Đã tạo biểu đồ so sánh doanh thu 6 chi nhánh." / "Đã tạo file báo cáo
  top 10 khách hàng rủi ro cao."), nothing about file paths.
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
    {
        "type": "function",
        "function": {
            "name": "whatif_npl_scenario",
            "description": (
                "Project the revenue impact of a hypothetical NPL ratio increase "
                "for a branch, or system-wide across all branches. Uses a "
                "transparent formula grounded in that branch's own real data "
                "(its own current yield rate on performing loans), not a "
                "made-up constant. Returns current numbers, the assumption "
                "used, and the projected outcome. Use this instead of writing "
                "your own SQL or guessing a number for 'what if NPL increases "
                "by X%' style questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "branch_query": {
                        "type": "string",
                        "description": (
                            "An unaccented keyword fragment matching part of the "
                            "branch_name (e.g. 'Binh Duong', 'Quan 1'), same rule "
                            "as query_database. Use 'system' for a system-wide "
                            "scenario across all branches if the question doesn't "
                            "name a specific branch."
                        ),
                    },
                    "npl_increase_pct": {
                        "type": "number",
                        "description": (
                            "How many percentage points the NPL ratio increases "
                            "by in this scenario, e.g. 2 for '+2%'."
                        ),
                    },
                },
                "required": ["branch_query", "npl_increase_pct"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": (
                "Run a SQL SELECT query and export the results as a downloadable "
                "Excel (.xlsx) or CSV file, saved to outputs/reports/. Returns the "
                "file path, row count, and column names. Use only when the user "
                "explicitly wants a file/report to download, not for plain "
                "chat answers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQLite SELECT statement whose results become the report rows.",
                    },
                    "filename": {
                        "type": "string",
                        "description": (
                            "Short descriptive filename WITHOUT extension, "
                            "e.g. 'top_10_khach_hang_rui_ro_cao'."
                        ),
                    },
                    "format": {
                        "type": "string",
                        "enum": ["xlsx", "csv"],
                        "description": "File format. Default to 'xlsx' unless the user asks for CSV.",
                    },
                },
                "required": ["sql", "filename", "format"],
            },
        },
    },
]


# ---------------------------------------------------------
# Guardrail (Output 3) — ALLOWLIST over blocklist.
# Instead of trying to enumerate every dangerous keyword (a blocklist, which
# can never be complete — an attacker only needs one word we forgot), this
# defines exactly what IS allowed and rejects everything else by default:
#   1. Exactly ONE SQL statement (no stacked queries via ';').
#   2. That statement must start with SELECT.
#   3. Every table referenced must be in ALLOWED_TABLES.
# A blocklist of obviously dangerous keywords is kept only as a secondary,
# defense-in-depth layer — it is NOT the primary protection.
# ---------------------------------------------------------
def is_safe_query(sql: str) -> tuple[bool, str]:
    """Validate a SQL string before it's ever executed. Returns (True, "")
    if safe, or (False, reason) if rejected."""
    stripped = sql.strip()
    normalized = stripped.lower()

    # Rule 1 (allowlist): exactly one statement, no stacked queries.
    # Reject any ';' that isn't just a single trailing terminator — this is
    # what actually stops "SELECT ...; DROP TABLE ...;" style attacks,
    # regardless of what keyword the second statement uses.
    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        return False, "Only a single SQL statement is allowed (no ';' stacked queries)."

    # Rule 2 (allowlist): must be a SELECT statement.
    if not normalized.startswith("select"):
        return False, "Only SELECT statements are allowed."

    # Rule 3 (allowlist): every referenced table must be whitelisted.
    mentioned_tables = re.findall(r"(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", normalized)
    for table in mentioned_tables:
        if table not in ALLOWED_TABLES:
            return False, f"Table '{table}' is not in the allowed table list."

    # Secondary blocklist layer (defense in depth, not primary defense).
    forbidden = ["insert", "update", "delete", "drop", "alter", "attach", "pragma", "create", "replace"]
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
# generate_daily_digest: Output 2d — "Morning Brief". Deterministic Python
# math (no LLM call), so it's always exactly right and costs zero API usage
# even though it runs every time the app opens. Compares the latest month vs
# the previous month per branch (NPL ratio delta, revenue delta), plus the
# largest currently-NPL loans. Called directly by app.py on page load — NOT
# part of the chat/tool-calling loop, so it isn't in TOOLS.
# ---------------------------------------------------------
def generate_daily_digest(lang: str = "vi") -> dict:
    """Return a bilingual dict {'vi': ..., 'en': ...} summarizing month-over-month
    NPL/revenue changes plus the largest NPL loans, computed directly from SQL —
    no model involved in the numbers, only used for display (Output 4)."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT DISTINCT month FROM npl_records ORDER BY month DESC LIMIT 2")
    months = [r["month"] for r in cur.fetchall()]
    if len(months) < 2:
        con.close()
        return {
            "vi": "Chưa đủ dữ liệu 2 tháng để so sánh.",
            "en": "Not enough monthly data yet to compare.",
        }
    latest_month, prev_month = months[0], months[1]

    cur.execute(
        """
        SELECT b.branch_name,
               n1.npl_ratio AS latest_npl, n0.npl_ratio AS prev_npl,
               r1.total_rev AS latest_rev, r0.total_rev AS prev_rev
        FROM branches b
        JOIN npl_records n1 ON n1.branch_id = b.branch_id AND n1.month = ?
        JOIN npl_records n0 ON n0.branch_id = b.branch_id AND n0.month = ?
        JOIN monthly_revenue r1 ON r1.branch_id = b.branch_id AND r1.month = ?
        JOIN monthly_revenue r0 ON r0.branch_id = b.branch_id AND r0.month = ?
        """,
        (latest_month, prev_month, latest_month, prev_month),
    )
    rows = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """
        SELECT customer_name, amount, loan_type FROM loans
        WHERE repayment_status = 'NPL'
        ORDER BY amount DESC LIMIT 2
        """
    )
    top_npl_loans = [dict(r) for r in cur.fetchall()]
    con.close()

    for r in rows:
        r["npl_delta"] = round(r["latest_npl"] - r["prev_npl"], 2)
        r["rev_delta_pct"] = (
            round((r["latest_rev"] - r["prev_rev"]) / r["prev_rev"] * 100, 1)
            if r["prev_rev"]
            else 0.0
        )

    worst_npl = max(rows, key=lambda r: r["npl_delta"]) if rows else None
    worst_rev = min(rows, key=lambda r: r["rev_delta_pct"]) if rows else None

    lines_vi = [f"Tóm tắt tháng {latest_month} so với {prev_month}:"]
    lines_en = [f"Summary for {latest_month} vs {prev_month}:"]

    flagged = False
    if worst_npl and worst_npl["npl_delta"] > 0:
        flagged = True
        lines_vi.append(
            f"- NPL ratio chi nhánh {worst_npl['branch_name']} tăng "
            f"{worst_npl['npl_delta']:+.2f} điểm % (từ {worst_npl['prev_npl']:.2f}% "
            f"lên {worst_npl['latest_npl']:.2f}%)."
        )
        lines_en.append(
            f"- {worst_npl['branch_name']}'s NPL ratio rose "
            f"{worst_npl['npl_delta']:+.2f} pp (from {worst_npl['prev_npl']:.2f}% "
            f"to {worst_npl['latest_npl']:.2f}%)."
        )
    if worst_rev and worst_rev["rev_delta_pct"] < 0:
        flagged = True
        lines_vi.append(
            f"- Doanh thu chi nhánh {worst_rev['branch_name']} giảm "
            f"{abs(worst_rev['rev_delta_pct']):.1f}% so với tháng trước."
        )
        lines_en.append(
            f"- {worst_rev['branch_name']}'s revenue dropped "
            f"{abs(worst_rev['rev_delta_pct']):.1f}% vs last month."
        )
    if not flagged:
        lines_vi.append("- Không có biến động bất thường đáng chú ý trong tháng này.")
        lines_en.append("- No notable adverse changes this month.")

    if top_npl_loans:
        lines_vi.append(f"- {len(top_npl_loans)} khoản vay NPL lớn nhất hiện tại:")
        lines_en.append(f"- Top {len(top_npl_loans)} largest current NPL loans:")
        for loan in top_npl_loans:
            line = f"   • {loan['customer_name']} — {loan['loan_type']} — {loan['amount']:,.0f} VND"
            lines_vi.append(line)
            lines_en.append(line)

    return {
        "vi": "\n".join(lines_vi),
        "en": "\n".join(lines_en),
        "latest_month": latest_month,
        "prev_month": prev_month,
        "branches": rows,
        "top_npl_loans": top_npl_loans,
    }


# ---------------------------------------------------------
# whatif_npl_scenario: Output 2e — "What-if Scenario Analysis". Turns the
# agent from a lookup tool into an advisor. The projection is a transparent,
# explainable formula grounded in the branch's OWN real data (not a made-up
# constant):
#   1. yield_rate = current_revenue / currently_performing_loan_balance
#      (the branch's own historical rate of interest income per VND of
#      performing loans this month).
#   2. Apply the hypothetical NPL ratio increase to get how much MORE loan
#      balance would flip to NPL.
#   3. Assume that newly-NPL balance stops generating interest at yield_rate
#      — the lost revenue is that balance times yield_rate.
# This keeps the assumption simple and auditable: every number returned can
# be traced back to real branches/npl_records/monthly_revenue rows, and the
# assumption itself ("newly-NPL loans stop generating interest") is stated
# explicitly in the result so the model (and the user) can see the reasoning,
# not just a number that appeared from nowhere.
# ---------------------------------------------------------
def _resolve_branch_id(branch_query: str):
    """Fuzzy-match a branch_query (expected to be an unaccented keyword
    fragment, e.g. 'Binh Duong', or 'system'/'toan he thong' for system-wide)
    against branch_name. Returns one of three distinct states:
      - (None, None)  -> explicit system-wide request (empty query or a
        recognized system keyword like 'system'/'toàn hệ thống').
      - (branch_id, branch_name) -> a real branch was matched.
      - (False, None) -> branch_query was NOT empty/system AND matched no
        branch. This must be distinguished from system-wide (None, None) so
        a typo/unknown branch name doesn't silently get answered as a
        system-wide scenario instead of surfacing an error."""
    if not branch_query:
        return None, None
    normalized = branch_query.strip().lower()
    system_keywords = {"system", "system-wide", "all", "toan he thong", "toàn hệ thống", "toàn bộ", "all branches"}
    if normalized in system_keywords:
        return None, None

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT branch_id, branch_name FROM branches WHERE branch_name LIKE ?", (f"%{branch_query}%",))
    row = cur.fetchone()
    con.close()
    if row:
        return row["branch_id"], row["branch_name"]
    return False, None


def whatif_npl_scenario(branch_query: str, npl_increase_pct: float, lang: str = "vi") -> str:
    """Project the revenue impact of a hypothetical NPL ratio increase for a
    branch (or system-wide if branch_query is empty/'system'). Returns JSON
    with all intermediate numbers and the assumption used, for the model to
    explain transparently rather than just stating a conclusion."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    branch_id, branch_name = _resolve_branch_id(branch_query)
    if branch_id is False:
        con.close()
        return json.dumps({"error": f"No branch matching '{branch_query}' was found."})

    cur.execute("SELECT MAX(month) AS m FROM npl_records")
    latest_month = cur.fetchone()["m"]

    if branch_id is None:
        cur.execute(
            "SELECT SUM(total_loans_amount) AS total_loans_amount, SUM(npl_amount) AS npl_amount "
            "FROM npl_records WHERE month = ?",
            (latest_month,),
        )
        npl_row = cur.fetchone()
        cur.execute("SELECT SUM(total_rev) AS total_rev FROM monthly_revenue WHERE month = ?", (latest_month,))
        rev_row = cur.fetchone()
        branch_label = "toàn hệ thống" if lang == "vi" else "system-wide"
    else:
        cur.execute(
            "SELECT total_loans_amount, npl_amount FROM npl_records WHERE branch_id = ? AND month = ?",
            (branch_id, latest_month),
        )
        npl_row = cur.fetchone()
        cur.execute(
            "SELECT total_rev FROM monthly_revenue WHERE branch_id = ? AND month = ?",
            (branch_id, latest_month),
        )
        rev_row = cur.fetchone()
        branch_label = branch_name

    con.close()

    if not npl_row or not npl_row["total_loans_amount"] or not rev_row or rev_row["total_rev"] is None:
        return json.dumps({"error": f"No data found for '{branch_query}' at month {latest_month}."})

    total_loans_amount = npl_row["total_loans_amount"]
    npl_amount = npl_row["npl_amount"] or 0
    total_rev = rev_row["total_rev"]
    current_npl_ratio = (npl_amount / total_loans_amount * 100) if total_loans_amount else 0
    performing_amount = total_loans_amount - npl_amount

    if performing_amount <= 0:
        return json.dumps({"error": "No performing loan balance to derive a yield rate from."})

    yield_rate = total_rev / performing_amount

    new_npl_ratio = min(current_npl_ratio + npl_increase_pct, 100)
    new_npl_amount = new_npl_ratio / 100 * total_loans_amount
    additional_npl_amount = max(new_npl_amount - npl_amount, 0)
    revenue_loss = additional_npl_amount * yield_rate
    projected_revenue = total_rev - revenue_loss
    revenue_loss_pct = (revenue_loss / total_rev * 100) if total_rev else 0

    return json.dumps(
        {
            "branch": branch_label,
            "month_used": latest_month,
            "current_npl_ratio": round(current_npl_ratio, 2),
            "scenario_npl_increase_pct": npl_increase_pct,
            "new_npl_ratio": round(new_npl_ratio, 2),
            "current_revenue": round(total_rev, 0),
            "projected_revenue": round(projected_revenue, 0),
            "revenue_loss": round(revenue_loss, 0),
            "revenue_loss_pct": round(revenue_loss_pct, 2),
            "assumption": (
                "yield_rate = current_revenue / currently_performing_loan_balance "
                "(this branch's own rate). Assumes loan balance that newly becomes "
                "NPL under the scenario stops generating interest income at that rate."
            ),
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


def generate_chart(sql: str, chart_type: str, title: str, lang: str = "vi") -> str:
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
    unit_labels = {
        "vi": {"billion": "tỷ", "million": "triệu"},
        "en": {"billion": "billion", "million": "million"},
    }
    labels_for_lang = unit_labels.get(lang, unit_labels["vi"])
    max_abs = max(abs(v) for v in values) if values else 0
    if max_abs >= 1_000_000_000:
        scale, unit = 1_000_000_000, labels_for_lang["billion"]
    elif max_abs >= 1_000_000:
        scale, unit = 1_000_000, labels_for_lang["million"]
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
# generate_report tool: runs a query (through the same guardrail), writes the
# result to a real Excel/CSV file with pandas. agent.py only CREATES the file
# — "download" is Output 4's job (Streamlit st.download_button() reads this
# path). See masterplan.md 2c architecture note: there is NO separate
# "export_report" tool — creating and downloading are different concerns.
# ---------------------------------------------------------
def generate_report(sql: str, filename: str, format: str = "xlsx") -> str:
    """Run sql, write results to outputs/reports/<filename>.<format>,
    return path + row/column metadata."""
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
        return json.dumps({"error": "Query returned no rows to export."})

    df = pd.DataFrame(rows)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    safe_name = _slugify(filename)
    format = format if format in ("xlsx", "csv") else "xlsx"
    filepath = os.path.join(REPORTS_DIR, f"{safe_name}.{format}")

    if format == "xlsx":
        df.to_excel(filepath, index=False)
    else:
        df.to_csv(filepath, index=False)

    return json.dumps(
        {
            "file_path": os.path.abspath(filepath),
            "format": format,
            "row_count": len(df),
            "columns": list(df.columns),
        },
        default=str,
    )


# ---------------------------------------------------------
# Defense in depth: even though the system prompt tells the model not to
# print file paths/markdown image links, LLMs don't always follow
# instructions perfectly. Strip them out programmatically too, rather than
# relying on the prompt alone — same allowlist-not-just-asking-nicely
# principle as the SQL guardrail.
# ---------------------------------------------------------
def _strip_file_paths(text: str) -> str:
    if not text:
        return text
    # Markdown image/link syntax: ![alt](path) or [alt](path)
    text = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", text)
    # Raw Windows paths (C:\...) or Unix-style paths containing outputs/charts|reports
    text = re.sub(r"[A-Za-z]:\\[^\s]+", "", text)
    text = re.sub(r"(?:/|\\)?outputs[/\\](?:charts|reports)[/\\][^\s]+", "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ---------------------------------------------------------
# Function-calling loop
#
# Returns a dict: {"answer": str, "chart_files": [...], "report_files": [...]}
# instead of a plain string, so callers (like the Streamlit app in Output 4)
# know whether a chart PNG or report Excel/CSV was actually created during
# this turn — st.image()/st.download_button() need the file path, which
# isn't otherwise recoverable from just the model's text answer.
# ---------------------------------------------------------
def ask_agent(client: OpenAI, question: str, verbose: bool = False, lang: str = "vi") -> dict:
    # Language is decided by the UI toggle (Output 4), not guessed from the
    # question's wording — guessing is unreliable because branch/customer
    # names in the DB are Vietnamese regardless of what language the question
    # is typed in, which was previously causing English questions to still
    # get Vietnamese answers. This is an explicit, enforced directive rather
    # than a soft "match the question's language" hint.
    lang_directive = {
        "vi": "IMPORTANT: Write your entire final answer in Vietnamese, and any chart title you choose must also be in Vietnamese.",
        "en": "IMPORTANT: Write your entire final answer in English, and any chart title you choose must also be in English.",
    }.get(lang, "")

    messages = [
        {"role": "system", "content": SCHEMA_DESCRIPTION + "\n\n" + lang_directive},
        {"role": "user", "content": question},
    ]

    chart_files = []
    report_files = []

    # Loop in case the model wants to call the tool more than once.
    for _ in range(5):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            return {
                "answer": _strip_file_paths(msg.content),
                "chart_files": chart_files,
                "report_files": report_files,
            }

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
                    lang,
                )
                try:
                    parsed = json.loads(result)
                    if "file_path" in parsed:
                        chart_files.append(parsed["file_path"])
                except (json.JSONDecodeError, TypeError):
                    pass
            elif tool_name == "whatif_npl_scenario":
                result = whatif_npl_scenario(
                    args.get("branch_query", "system"),
                    args.get("npl_increase_pct", 0),
                    lang,
                )
            elif tool_name == "generate_report":
                result = generate_report(
                    args.get("sql", ""),
                    args.get("filename", "report"),
                    args.get("format", "xlsx"),
                )
                try:
                    parsed = json.loads(result)
                    if "file_path" in parsed:
                        report_files.append(parsed["file_path"])
                except (json.JSONDecodeError, TypeError):
                    pass
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

    return {
        "answer": "Sorry, I couldn't produce an answer within the allowed steps.",
        "chart_files": chart_files,
        "report_files": report_files,
    }


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
        result = ask_agent(client, question, verbose=True)
        print(f"A: {result['answer']}\n")
        if result["chart_files"]:
            print(f"   [chart file(s)]: {result['chart_files']}")
        if result["report_files"]:
            print(f"   [report file(s)]: {result['report_files']}")


if __name__ == "__main__":
    main()
