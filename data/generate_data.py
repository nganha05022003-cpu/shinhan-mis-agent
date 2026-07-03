"""
generate_data.py
Generates simulated data for shinhan_mis.db (4 tables):
branches, loans, monthly_revenue, npl_records

Run: python3 generate_data.py
Output: shinhan_mis.db (SQLite file) created in the same folder.
"""

import sqlite3
import random
from datetime import date, timedelta

random.seed(42)  # reproducible results every run

DB_PATH = "shinhan_mis.db"
NUM_MONTHS = 12

# ---------------------------------------------------------
# 1. BRANCHES (fixed, not randomized — real identity matters)
# ---------------------------------------------------------
BRANCHES = [
    {"branch_id": 1, "branch_name": "Chi nhanh Quan 1",       "city": "Ho Chi Minh", "region": "Mien Nam",  "branch_size": "Flagship"},
    {"branch_id": 2, "branch_name": "Chi nhanh Hoan Kiem",    "city": "Ha Noi",      "region": "Mien Bac",  "branch_size": "Flagship"},
    {"branch_id": 3, "branch_name": "Chi nhanh Hai Chau",     "city": "Da Nang",     "region": "Mien Trung","branch_size": "Standard"},
    {"branch_id": 4, "branch_name": "Chi nhanh Binh Duong",   "city": "Binh Duong",  "region": "Mien Nam",  "branch_size": "Standard"},
    {"branch_id": 5, "branch_name": "Chi nhanh Long An",      "city": "Long An",     "region": "Mien Nam",  "branch_size": "Small"},
    {"branch_id": 6, "branch_name": "Chi nhanh Thai Nguyen",  "city": "Thai Nguyen", "region": "Mien Bac",  "branch_size": "Small"},
]

# Loan volume range per branch, scaled by size (grounded in earlier reasoning:
# Flagship = big city, high traffic; Small = countryside, low traffic)
LOAN_COUNT_RANGE = {
    "Flagship": (40, 60),
    "Standard": (20, 35),
    "Small":    (8, 15),
}

LOAN_TYPES = ["Mortgage", "Personal", "Business", "Auto"]

# Monthly interest rate range per Shinhan's real published annual rates
# (7.95%-16%/year observed -> ~0.66%-1.33%/month on outstanding balance)
MONTHLY_RATE_RANGE = (0.007, 0.013)


def month_labels(n=NUM_MONTHS):
    """Return n month labels like '2025-07' ... '2026-06', most recent = current month."""
    labels = []
    y, m = 2026, 6  # anchor: last month in the series is 2026-06
    for _ in range(n):
        labels.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(labels))


def random_date_in_window(months_ago_max=NUM_MONTHS):
    """Random issue_date within the last NUM_MONTHS months, anchored to 2026-06-30."""
    end = date(2026, 6, 30)
    start = end - timedelta(days=months_ago_max * 30)
    delta_days = (end - start).days
    return start + timedelta(days=random.randint(0, delta_days))


def generate_loans():
    loans = []
    loan_id = 1
    earliest_month = month_labels()[0]  # e.g. "2025-07"
    for b in BRANCHES:
        lo, hi = LOAN_COUNT_RANGE[b["branch_size"]]
        num_loans = random.randint(lo, hi)
        # Ensure at least 3 loans are backdated to the earliest month, so
        # month-1 NPL ratios aren't computed off a 1-loan sample (avoids 0%/100% noise).
        min_early_loans = min(3, num_loans)
        for i in range(num_loans):
            if i < min_early_loans:
                y, m = map(int, earliest_month.split("-"))
                issue_dt = date(y, m, random.randint(1, 28))
            else:
                issue_dt = random_date_in_window()
            due_dt = issue_dt + timedelta(days=random.choice([365, 730, 1095, 1825]))  # 1-5 yrs
            loan_type = random.choice(LOAN_TYPES)
            # amount ranges roughly reflect real product sizes (VND)
            amount_ranges = {
                "Mortgage": (500_000_000, 3_000_000_000),
                "Personal": (20_000_000, 300_000_000),
                "Business": (200_000_000, 2_000_000_000),
                "Auto":     (100_000_000, 800_000_000),
            }
            amt_lo, amt_hi = amount_ranges[loan_type]
            amount = round(random.uniform(amt_lo, amt_hi), -6)  # round to nearest million

            # weighted repayment status: most loans healthy
            repayment_status = random.choices(
                ["Performing", "NPL", "Closed"],
                weights=[78, 12, 10],
                k=1
            )[0]

            loans.append({
                "loan_id": loan_id,
                "branch_id": b["branch_id"],
                "customer_id": f"CUST{loan_id:05d}",
                "customer_name": f"Khach hang {loan_id:03d}",
                "loan_type": loan_type,
                "amount": amount,
                "issue_date": issue_dt.isoformat(),
                "due_date": due_dt.isoformat(),
                "repayment_status": repayment_status,
            })
            loan_id += 1
    return loans


def generate_monthly_revenue_and_npl(loans):
    """
    Derive monthly_revenue and npl_records FROM loans, per branch per month,
    so all 3 tables stay internally consistent (no contradicting numbers).
    """
    months = month_labels()
    revenue_rows = []
    npl_rows = []
    revenue_id = 1
    npl_id = 1

    for b in BRANCHES:
        branch_loans = [l for l in loans if l["branch_id"] == b["branch_id"]]

        for month in months:
            # Loans considered "active/outstanding" in this month:
            # issued on or before this month, and not yet past due before this month.
            active_loans = [
                l for l in branch_loans
                if l["issue_date"][:7] <= month
            ]
            total_outstanding = sum(l["amount"] for l in active_loans)

            # --- monthly_revenue: interest-based proxy + small fee buffer ---
            monthly_rate = random.uniform(*MONTHLY_RATE_RANGE)
            interest_income = total_outstanding * monthly_rate
            fee_buffer = random.uniform(5_000_000, 20_000_000)
            total_rev = round(interest_income + fee_buffer, 0)

            revenue_rows.append({
                "revenue_id": revenue_id,
                "branch_id": b["branch_id"],
                "month": month,
                "total_rev": total_rev,
            })
            revenue_id += 1

            # --- npl_records: based on loans whose issue_date falls in/before this month ---
            npl_loans = [l for l in active_loans if l["repayment_status"] == "NPL"]
            total_loans_amount = total_outstanding
            npl_amount = sum(l["amount"] for l in npl_loans)
            npl_ratio = round((npl_amount / total_loans_amount * 100), 2) if total_loans_amount > 0 else 0.0

            npl_rows.append({
                "npl_id": npl_id,
                "branch_id": b["branch_id"],
                "month": month,
                "total_loans_amount": total_loans_amount,
                "npl_amount": npl_amount,
                "npl_ratio": npl_ratio,
            })
            npl_id += 1

    return revenue_rows, npl_rows


def create_and_populate_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.executescript("""
    DROP TABLE IF EXISTS branches;
    DROP TABLE IF EXISTS loans;
    DROP TABLE IF EXISTS monthly_revenue;
    DROP TABLE IF EXISTS npl_records;

    CREATE TABLE branches (
        branch_id INTEGER PRIMARY KEY,
        branch_name TEXT NOT NULL,
        city TEXT NOT NULL,
        region TEXT NOT NULL,
        branch_size TEXT NOT NULL
    );

    CREATE TABLE loans (
        loan_id INTEGER PRIMARY KEY,
        branch_id INTEGER NOT NULL,
        customer_id TEXT NOT NULL,
        customer_name TEXT NOT NULL,
        loan_type TEXT NOT NULL,
        amount REAL NOT NULL,
        issue_date TEXT NOT NULL,
        due_date TEXT NOT NULL,
        repayment_status TEXT NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
    );

    CREATE TABLE monthly_revenue (
        revenue_id INTEGER PRIMARY KEY,
        branch_id INTEGER NOT NULL,
        month TEXT NOT NULL,
        total_rev REAL NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
    );

    CREATE TABLE npl_records (
        npl_id INTEGER PRIMARY KEY,
        branch_id INTEGER NOT NULL,
        month TEXT NOT NULL,
        total_loans_amount REAL NOT NULL,
        npl_amount REAL NOT NULL,
        npl_ratio REAL NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
    );
    """)

    cur.executemany(
        "INSERT INTO branches VALUES (:branch_id, :branch_name, :city, :region, :branch_size)",
        BRANCHES
    )

    loans = generate_loans()
    cur.executemany(
        """INSERT INTO loans VALUES (:loan_id, :branch_id, :customer_id, :customer_name,
           :loan_type, :amount, :issue_date, :due_date, :repayment_status)""",
        loans
    )

    revenue_rows, npl_rows = generate_monthly_revenue_and_npl(loans)
    cur.executemany(
        "INSERT INTO monthly_revenue VALUES (:revenue_id, :branch_id, :month, :total_rev)",
        revenue_rows
    )
    cur.executemany(
        """INSERT INTO npl_records VALUES (:npl_id, :branch_id, :month,
           :total_loans_amount, :npl_amount, :npl_ratio)""",
        npl_rows
    )

    conn.commit()

    # Quick sanity checks (Definition of Done: no negative NPL, revenue logical)
    cur.execute("SELECT COUNT(*) FROM branches")
    print(f"branches: {cur.fetchone()[0]} rows")
    cur.execute("SELECT COUNT(*) FROM loans")
    print(f"loans: {cur.fetchone()[0]} rows")
    cur.execute("SELECT COUNT(*) FROM monthly_revenue")
    print(f"monthly_revenue: {cur.fetchone()[0]} rows")
    cur.execute("SELECT COUNT(*) FROM npl_records")
    print(f"npl_records: {cur.fetchone()[0]} rows")
    cur.execute("SELECT MIN(npl_ratio), MAX(npl_ratio) FROM npl_records")
    print(f"npl_ratio range: {cur.fetchone()}")
    cur.execute("SELECT MIN(total_rev), MAX(total_rev) FROM monthly_revenue")
    print(f"total_rev range: {cur.fetchone()}")

    conn.close()
    print(f"\nDone. Database written to {DB_PATH}")


if __name__ == "__main__":
    create_and_populate_db()
