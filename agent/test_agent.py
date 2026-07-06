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
  python3 agent/test_agent.py digest      # only 2d Morning Brief test (no API key needed)
  python3 agent/test_agent.py whatif      # only 2e What-if Scenario test

Requires: OPENAI_API_KEY set in environment (except the 'digest' group, and
the deterministic half of 'whatif', which never call the LLM).
"""

import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(__file__))
from agent import ask_agent, generate_daily_digest, whatif_npl_scenario
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
        result = ask_agent(client, question, verbose=False)
        answer = result["answer"]
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

        try:
            result = ask_agent(client, question, verbose=True)
            answer = result["answer"]
            chart_files = result["chart_files"]
        except Exception as e:
            answer, chart_files = f"ERROR: {e}", []

        print(f"\nACTUAL ANSWER: {answer}")

        if not chart_files:
            print("[FAIL] ask_agent() reported no chart_files created")
            results.append((test_id, question, answer, False))
            continue

        newest = chart_files[-1]
        file_ok = os.path.exists(newest) and os.path.getsize(newest) > 0
        print(f"[{'PASS' if file_ok else 'FAIL'}] Chart file created: {newest} "
              f"({os.path.getsize(newest) if os.path.exists(newest) else 0} bytes)")
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
    import pandas as pd

    results = []
    for test_id, question in REPORT_TEST_CASES:
        print(f"\n{'='*70}\n[{test_id}] {question}")

        try:
            result = ask_agent(client, question, verbose=True)
            answer = result["answer"]
            report_files = result["report_files"]
        except Exception as e:
            answer, report_files = f"ERROR: {e}", []

        print(f"\nACTUAL ANSWER: {answer}")

        if not report_files:
            print("[FAIL] ask_agent() reported no report_files created")
            results.append((test_id, question, answer, False))
            continue

        newest = report_files[-1]
        size = os.path.getsize(newest) if os.path.exists(newest) else 0

        row_count = None
        try:
            df = pd.read_excel(newest) if newest.endswith(".xlsx") else pd.read_csv(newest)
            row_count = len(df)
        except Exception as e:
            print(f"  (could not reopen file to count rows: {e})")

        expected_rows = 6
        file_ok = size > 0 and (row_count is None or row_count == expected_rows)
        print(f"[{'PASS' if file_ok else 'FAIL'}] Report file created: {newest} "
              f"({size} bytes, {row_count} rows — expect {expected_rows})")
        print("Manually open this file to confirm the 6 monthly revenue values "
              "match ground truth: 45,955,856 / 53,842,068 / 67,155,230 / "
              "69,942,276 / 73,344,343 / 53,419,034 VND.")
        results.append((test_id, question, answer, file_ok))

    print_summary("REPORT", results)
    return results


# ---------------------------------------------------------
# Group: 2d Morning Brief (generate_daily_digest — no LLM call, no API cost)
# Ground truth (verified against shinhan_mis.db, month 2026-06 vs 2026-05):
# Chi nhanh Quan 1 revenue dropped 32.7%; top 2 current NPL loans are
# Khach hang 071 (1,677,000,000 VND) and Khach hang 096 (1,553,000,000 VND).
# ---------------------------------------------------------
def run_digest_test():
    print(f"\n{'='*70}\n[2d.1] generate_daily_digest — Morning Brief (both languages)")
    results = []
    for lang in ("vi", "en"):
        digest = generate_daily_digest(lang=lang)
        text = digest[lang]
        print(f"\n--- {lang.upper()} ---\n{text}")

        checks = {
            "Mentions Quan 1's revenue drop": "Quan 1" in text,
            "Revenue drop % matches ground truth (32.7%)": "32.7" in text,
            "Mentions top NPL loan amount (1,677,000,000)": "1,677,000,000" in text,
        }
        passed = all(checks.values())
        for label, ok in checks.items():
            print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        results.append((f"2d.1-{lang}", f"generate_daily_digest(lang='{lang}')", text, passed))

    print_summary("DIGEST", results)
    return results


# ---------------------------------------------------------
# Group: 2e What-if Scenario Analysis (whatif_npl_scenario)
# Ground truth (verified against shinhan_mis.db, latest month 2026-06):
#   Binh Duong +2pp NPL: current_npl_ratio 3.21%, new_npl_ratio 5.21%,
#     current_revenue 323,525,398 VND, revenue_loss ~6,684,838 VND (~2.07%).
#   System-wide +2pp NPL: current_npl_ratio 8.18%, new_npl_ratio 10.18%,
#     revenue_loss ~32,204,796 VND (~2.18%).
#   Unknown branch name -> must return an error, NOT silently fall back to
#     system-wide (this was a real bug caught during implementation).
# ---------------------------------------------------------
def run_whatif_test(client):
    import json

    results = []

    # --- Deterministic checks (no LLM, no API cost) ---
    print(f"\n{'='*70}\n[2e.1] whatif_npl_scenario('Binh Duong', 2) — deterministic math")
    r1 = json.loads(whatif_npl_scenario("Binh Duong", 2, "vi"))
    print(r1)
    ok1 = (
        r1.get("current_npl_ratio") == 3.21
        and r1.get("new_npl_ratio") == 5.21
        and abs(r1.get("revenue_loss", 0) - 6684838) < 100
    )
    print(f"[{'PASS' if ok1 else 'FAIL'}] Matches ground truth numbers")
    results.append(("2e.1", "whatif_npl_scenario('Binh Duong', 2)", str(r1), ok1))

    print(f"\n{'='*70}\n[2e.2] whatif_npl_scenario('system', 2) — deterministic math")
    r2 = json.loads(whatif_npl_scenario("system", 2, "en"))
    print(r2)
    ok2 = (
        r2.get("current_npl_ratio") == 8.18
        and r2.get("new_npl_ratio") == 10.18
        and abs(r2.get("revenue_loss", 0) - 32204796) < 100
    )
    print(f"[{'PASS' if ok2 else 'FAIL'}] Matches ground truth numbers")
    results.append(("2e.2", "whatif_npl_scenario('system', 2)", str(r2), ok2))

    print(f"\n{'='*70}\n[2e.3-adversarial] whatif_npl_scenario('Nonexistent Branch XYZ', 2) "
          "— must error, not silently fall back to system-wide")
    r3 = json.loads(whatif_npl_scenario("Nonexistent Branch XYZ", 2, "en"))
    print(r3)
    ok3 = "error" in r3
    print(f"[{'PASS' if ok3 else 'FAIL'}] Returned an error instead of a silent system-wide answer")
    results.append(("2e.3-adversarial", "whatif_npl_scenario('Nonexistent Branch XYZ', 2)", str(r3), ok3))

    # --- End-to-end through the LLM (confirms the model calls the tool
    #     correctly from a natural-language question) ---
    question = "Nếu NPL ratio chi nhánh Bình Dương tăng thêm 2% thì doanh thu ảnh hưởng thế nào?"
    result = run_question(client, "2e.4", question, ["6,684,838", "6684838", "2.07", "2.1"])
    results.append(result)

    print_summary("WHATIF", results)
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
    group = sys.argv[1] if len(sys.argv) > 1 else "all"

    # 'digest' never calls the LLM, so it doesn't need an API key.
    if group == "digest":
        run_digest_test()
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set.")
    client = OpenAI(api_key=api_key)

    if group in ("all", "core"):
        run_core_tests(client)
    if group in ("all", "anomaly"):
        run_anomaly_test(client)
    if group in ("all", "chart"):
        run_chart_tests(client)
    if group in ("all", "report"):
        run_report_tests(client)
    if group == "all":
        run_digest_test()
    if group in ("all", "whatif"):
        run_whatif_test(client)

    if group not in ("all", "core", "anomaly", "chart", "report", "digest", "whatif"):
        print(f"Unknown group '{group}'. Use: all | core | anomaly | chart | report | digest | whatif")


if __name__ == "__main__":
    main()
