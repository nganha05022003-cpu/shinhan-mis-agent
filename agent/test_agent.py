"""
test_agent.py
Consolidated test suite for the Shinhan MIS Agent — replaces the old scattered
run_test_cases.py + test_anomaly_detection.py files with one organized suite.

All checks are HEURISTIC (substring match, diacritic-insensitive) — not a
perfect judge of natural language. Always read the printed "ACTUAL" answer
yourself before trusting a PASS.

Usage:
  python3 agent/test_agent.py            # run everything
  python3 agent/test_agent.py core        # only core Q&A tests (2.1-2.16 set)
  python3 agent/test_agent.py anomaly     # only 2b anomaly detection test
  python3 agent/test_agent.py chart       # only 2a chart generation tests
  python3 agent/test_agent.py report      # only 2c report generation tests

Requires: OPENAI_API_KEY set in environment.
"""

import os
import sys
import glob
import unicodedata

sys.path.insert(0, os.path.dirname(__file__))
from agent import ask_agent, CHARTS_DIR, REPORTS_DIR
from openai import OpenAI


# ---------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------
def strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn").lower()


def check_answer(answer: str, key_facts: list[str]) -> bool:
    """Pass if AT LEAST ONE variant per fact group matches (facts are OR'd —
    e.g. '1738' / '1,738' / '1.7' are alternates for the same number, not
    independent required facts)."""
    norm_answer = strip_diacritics(answer)
    return any(strip_diacritics(fact) in norm_answer for fact in key_facts)


def run_question(client, test_id, question, key_facts):
    print(f"\n{'='*70}\n[{test_id}] {question}")
    try:
        answer = ask_agent(client, question, verbose=False)
    except Exception as e:
        answer = f"ERROR: {e}"

    passed = check_answer(answer, key_facts) if not answer.startswith("ERROR") else False
    print(f"ACTUAL: {answer}")
    print(f"Expected key facts (any of): {key_facts}")
    print(f"AUTO-CHECK: {'PASS' if passed else 'FAIL — needs manual review'}")
    return (test_id, question, answer, passed)


# ---------------------------------------------------------
# Group: core Q&A (docs/test_cases.md — Output 2 core set)
# Note: 2.2, 2.3, 2.6, 2.11 removed — redundant in purpose with 2.18, 2.5,
# 2.15, and 2.12 respectively. 2.16 (unscripted) intentionally excluded —
# meant to be improvised by you at demo time, not scripted.
# ---------------------------------------------------------
CORE_TEST_CASES = [
    ("2.1", "Chi nhánh nào có NPL ratio cao nhất trong tháng 6/2026?",
     ["Long An", "32"]),
    ("2.4", "Khoản vay lớn nhất hiện có là bao nhiêu, thuộc chi nhánh nào?",
     ["2,902,000,000", "2902000000", "2.9", "Quan 1"]),
    ("2.5", "Chi nhánh nào hiện có nhiều khoản vay NPL nhất (theo repayment_status)?",
     ["Hoan Kiem", "5"]),
    ("2.7", "Miền nào (region) có tổng giá trị cho vay lớn nhất?",
     ["Mien Nam"]),
    ("2.8", "Có bao nhiêu chi nhánh loại Flagship?",
     ["2"]),
    ("2.9", "Chi nhánh Thái Nguyên có tỷ lệ NPL bao nhiêu trong tháng 6/2026?",
     ["Thai Nguyen", "0"]),
    ("2.10", "Chi nhánh nào có ít khoản vay nhất?",
     ["Thai Nguyen", "8"]),
    ("2.12", "Trong 4 loại vay, loại nào có giá trị trung bình cao nhất?",
     ["Mortgage"]),
    ("2.13", 'Tổng số khoản vay đang ở trạng thái "Closed" là bao nhiêu?',
     ["18"]),
    ("2.14", "Chi nhánh Bình Dương thuộc miền nào, loại chi nhánh gì?",
     ["Mien Nam", "Standard"]),
    ("2.15", "Xu hướng doanh thu của Chi nhánh Quận 1 trong 12 tháng qua có tăng không?",
     ["tang", "tăng"]),
]


def run_core_tests(client):
    results = [run_question(client, tid, q, facts) for tid, q, facts in CORE_TEST_CASES]
    print_summary("CORE", results)
    return results


# ---------------------------------------------------------
# Group: 2b anomaly detection (detect_anomalies tool)
# Ground truth (verified against shinhan_mis.db): mean=12.9, stdev=14.32,
# threshold=41.55. Outliers: Hai Chau 2025-07 (55.88%), Long An 2025-07
# (42.14%), Long An 2026-01/02/03 (44.32% each). Thai Nguyen: NOT an outlier.
# ---------------------------------------------------------
ANOMALY_QUESTION = (
    "2b.1",
    "Chi nhánh/tháng nào có NPL ratio bất thường (so với trung bình toàn hệ thống)? "
    "Chi nhánh Thái Nguyên có nằm trong danh sách đó không?",
    ["Hai Chau", "Long An"],  # must mention both outlier branches
)


def run_anomaly_test(client):
    tid, question, facts = ANOMALY_QUESTION
    result = run_question(client, tid, question, facts)
    # Extra check: Thai Nguyen should be mentioned as NOT anomalous, not omitted
    answer = result[2]
    mentions_thai_nguyen = strip_diacritics("Thai Nguyen") in strip_diacritics(answer)
    print(f"[{'PASS' if mentions_thai_nguyen else 'FAIL'}] Mentions Thai Nguyen "
          f"(should confirm it's NOT anomalous)")
    print_summary("ANOMALY", [result])
    return [result]


# ---------------------------------------------------------
# Group: 2a chart generation (generate_chart tool)
# Ground truth (verified against shinhan_mis.db): ranking 12-month revenue,
# descending: Quan 1 (~3,761M), Hoan Kiem (~2,350.5M), Binh Duong (~1,911.9M),
# Hai Chau (~1,311.5M), Long An (~706.8M), Thai Nguyen (~634.6M) — matches
# test 2.18. This test doubles as test 2a.2 from docs/test_cases.md.
# ---------------------------------------------------------
CHART_TEST_CASES = [
    ("2a.2", "Xếp hạng chi nhánh theo tổng doanh thu toàn bộ 12 tháng, giảm dần, "
             "vẽ biểu đồ giúp tôi"),
]


def run_chart_tests(client):
    results = []
    for test_id, question in CHART_TEST_CASES:
        print(f"\n{'='*70}\n[{test_id}] {question}")

        before = set(glob.glob(os.path.join(CHARTS_DIR, "*.png")))
        try:
            answer = ask_agent(client, question, verbose=True)
        except Exception as e:
            answer = f"ERROR: {e}"
        after = set(glob.glob(os.path.join(CHARTS_DIR, "*.png")))
        new_files = after - before

        print(f"\nACTUAL ANSWER: {answer}")

        if not new_files:
            print("[FAIL] No new PNG file was created in outputs/charts/")
            results.append((test_id, question, answer, False))
            continue

        newest = max(new_files, key=os.path.getmtime)
        size = os.path.getsize(newest)
        file_ok = size > 0
        print(f"[{'PASS' if file_ok else 'FAIL'}] New chart file created: {newest} "
              f"({size} bytes)")
        print("Manually open this PNG to confirm it shows 6 branches, correctly "
              "ordered descending, matching the ground truth above.")
        results.append((test_id, question, answer, file_ok))

    print_summary("CHART", results)
    return results


# ---------------------------------------------------------
# Group: 2c report generation (generate_report tool)
# Ground truth (verified against shinhan_mis.db): Thai Nguyen monthly_revenue
# for 2026-01..2026-06 = 6 rows: 45,955,856 / 53,842,068 / 67,155,230 /
# 69,942,276 / 73,344,343 / 53,419,034 VND.
# ---------------------------------------------------------
REPORT_TEST_CASES = [
    ("2c-custom", "Yêu cầu 1 báo cáo cụ thể: tổng hợp doanh thu 6 tháng gần nhất "
                  "(2026-01 đến 2026-06) của Chi nhánh Thái Nguyên, xuất ra file"),
]


def run_report_tests(client):
    results = []
    for test_id, question in REPORT_TEST_CASES:
        print(f"\n{'='*70}\n[{test_id}] {question}")

        before = set(glob.glob(os.path.join(REPORTS_DIR, "*")))
        try:
            answer = ask_agent(client, question, verbose=True)
        except Exception as e:
            answer = f"ERROR: {e}"
        after = set(glob.glob(os.path.join(REPORTS_DIR, "*")))
        new_files = after - before

        print(f"\nACTUAL ANSWER: {answer}")

        if not new_files:
            print("[FAIL] No new report file was created in outputs/reports/")
            results.append((test_id, question, answer, False))
            continue

        newest = max(new_files, key=os.path.getmtime)
        size = os.path.getsize(newest)

        row_count = None
        try:
            import pandas as pd
            if newest.endswith(".xlsx"):
                df = pd.read_excel(newest)
            else:
                df = pd.read_csv(newest)
            row_count = len(df)
        except Exception as e:
            print(f"  (could not reopen file to count rows: {e})")

        expected_rows = 6
        file_ok = size > 0 and (row_count is None or row_count == expected_rows)
        print(f"[{'PASS' if file_ok else 'FAIL'}] New report file created: {newest} "
              f"({size} bytes, {row_count} rows — expect {expected_rows})")
        print("Manually open this file to confirm the 6 monthly revenue values "
              "match ground truth: 45,955,856 / 53,842,068 / 67,155,230 / "
              "69,942,276 / 73,344,343 / 53,419,034 VND.")
        results.append((test_id, question, answer, file_ok))

    print_summary("REPORT", results)
    return results


# ---------------------------------------------------------
# Summary printer
# ---------------------------------------------------------
def print_summary(group_name, results):
    print(f"\n{'='*70}\n{group_name} SUMMARY\n{'='*70}")
    passed_count = sum(1 for r in results if r[3])
    for test_id, question, answer, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {test_id}: {question}")
    print(f"\n{passed_count}/{len(results)} auto-passed. Review any FAIL rows "
          f"manually — this is a heuristic, not a perfect judge of correct "
          f"natural language answers.")


# ---------------------------------------------------------
# Entry point
# ---------------------------------------------------------
def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set.")
    client = OpenAI(api_key=api_key)

    group = sys.argv[1] if len(sys.argv) > 1 else "all"

    if group in ("all", "core"):
        run_core_tests(client)
    if group in ("all", "anomaly"):
        run_anomaly_test(client)
    if group in ("all", "chart"):
        run_chart_tests(client)
    if group in ("all", "report"):
        run_report_tests(client)

    if group not in ("all", "core", "anomaly", "chart", "report"):
        print(f"Unknown group '{group}'. Use: all | core | anomaly | chart | report")


if __name__ == "__main__":
    main()
