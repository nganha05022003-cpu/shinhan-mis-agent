"""
app.py
Shinhan MIS Agent — Streamlit demo interface (Output 4). Bilingual (VI/EN).

Architecture note: this file is ONLY responsible for the interface.
All logic (SQL generation, guardrail, chart/report creation) lives in
agent/agent.py. This app just calls ask_agent() and renders whatever it
gets back — text answer, plus st.image()/st.download_button() if a chart
or report file was created during that turn.

Run: streamlit run app/app.py
Requires: OPENAI_API_KEY set as an environment variable.
"""

import os
import sys
import base64

import streamlit as st
from openai import OpenAI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
from agent import ask_agent, generate_daily_digest

LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "logo.png")
BRAND_BLUE = "#0046FF"
BRAND_GRAY = "#4A4A4A"

# ---------------------------------------------------------
# Capability categories — mirrors a "quick action" bar (like Claude's
# Strategize/Write/Learn buttons) instead of one long flat list of sample
# questions. The point: a judge should see the agent's 5 distinct
# capabilities at a glance (query, chart, report, morning brief, what-if),
# then drill into example questions for ONE capability at a time — not
# scroll a wall of 10+ unsorted questions and have to infer the big picture
# themselves. Each category's questions are (vi, en) pairs, same pattern as
# before, just grouped by which tool they demonstrate.
# ---------------------------------------------------------
CATEGORIES = [
    {"id": "query", "icon": "🔍", "label_vi": "Tra cứu dữ liệu", "label_en": "Query Data"},
    {"id": "chart", "icon": "📊", "label_vi": "Biểu đồ", "label_en": "Chart"},
    {"id": "report", "icon": "📄", "label_vi": "Báo cáo", "label_en": "Report"},
    {"id": "digest", "icon": "🌅", "label_vi": "Tóm tắt sáng nay", "label_en": "Morning Brief"},
    {"id": "whatif", "icon": "🔮", "label_vi": "Phân tích kịch bản", "label_en": "What-if Scenario"},
]

CATEGORY_QUESTIONS = {
    "query": [
        ("Chi nhánh nào có NPL ratio cao nhất trong tháng 6/2026?",
         "Which branch has the highest NPL ratio in June 2026?"),
        ("Chi nhánh Bình Dương thuộc miền nào, loại chi nhánh gì?",
         "Which region is Binh Duong branch in, and what size category is it?"),
        ("Trong 4 loại vay, loại nào có giá trị trung bình cao nhất?",
         "Among the 4 loan types, which has the highest average value?"),
        ("Chi nhánh/tháng nào có NPL ratio bất thường so với trung bình toàn hệ thống?",
         "Which branch/month has an NPL ratio that's anomalous vs. the system-wide average?"),
    ],
    "chart": [
        ("Xếp hạng chi nhánh theo tổng doanh thu toàn bộ 12 tháng, giảm dần, vẽ biểu đồ giúp tôi",
         "Rank branches by total 12-month revenue, descending, and draw me a chart"),
        ("So sánh doanh thu 6 chi nhánh bằng biểu đồ",
         "Compare revenue across all 6 branches with a chart"),
        ("Vẽ biểu đồ doanh thu 12 tháng của Chi nhánh Quận 1",
         "Plot District 1 branch's 12-month revenue trend"),
    ],
    "report": [
        ("Tạo file báo cáo top 10 khách hàng có rủi ro cao nhất",
         "Generate a report file of the top 10 highest-risk customers"),
        ("Tổng hợp doanh thu 6 tháng gần nhất của tất cả chi nhánh thành file Excel",
         "Export the last 6 months of revenue for all branches to an Excel file"),
    ],
    "whatif": [
        ("Nếu NPL ratio chi nhánh Bình Dương tăng thêm 2% thì doanh thu ảnh hưởng thế nào?",
         "If Binh Duong branch's NPL ratio increases by 2%, how would revenue be affected?"),
        ("Nếu NPL ratio toàn hệ thống tăng thêm 3% thì ảnh hưởng thế nào?",
         "If system-wide NPL ratio increases by 3%, what's the impact?"),
    ],
    # "digest" intentionally has no question list — it's not a Q&A tool,
    # it's the auto-generated summary already shown at the top of the page.
    # Clicking that pill just points the user back to it (see render logic).
}

TEXT = {
    "vi": {
        "title": "Shinhan MIS Agent — trợ lý đắc lực của các nhà quản lý",
        "subtitle": (
            "Hỏi bất kỳ câu hỏi nào về chi nhánh, khoản vay, doanh thu, "
            "hoặc tỷ lệ nợ xấu (NPL)<br>bằng tiếng Việt tự nhiên — không cần biết SQL."
        ),
        "chat_placeholder": "Nhập câu hỏi của bạn...",
        "spinner": "Đang xử lý...",
        "download_label": "Tải xuống",
        "api_key_error": (
            "OPENAI_API_KEY chưa được set trong biến môi trường. "
            "Set biến này rồi khởi động lại app."
        ),
        "lang_switch_label": "🌐 English",
        "digest_header": "📋 Tóm tắt sáng nay",
        "capability_questions_caption": "Câu hỏi gợi ý. Bấm để hỏi",
        "digest_pointer": "Tính năng này tự động hiển thị ở đầu trang mỗi khi bạn mở app — không cần hỏi gì cả. Xem lại phần \"📋 Tóm tắt sáng nay\" phía trên.",
    },
    "en": {
        "title": "Shinhan MIS Agent — Every Manager's Essential Assistant",
        "subtitle": (
            "Ask anything about branches, loans, revenue, or non-performing "
            "loan (NPL) ratios<br>in plain English — no SQL required."
        ),
        "chat_placeholder": "Type your question...",
        "spinner": "Processing...",
        "download_label": "Download",
        "api_key_error": (
            "OPENAI_API_KEY is not set as an environment variable. "
            "Set it and restart the app."
        ),
        "lang_switch_label": "🌐 Tiếng Việt",
        "digest_header": "📋 Morning Brief",
        "capability_questions_caption": "Suggested questions. Click to ask",
        "digest_pointer": "This runs automatically at the top of the page every time you open the app — no need to ask. See the \"📋 Morning Brief\" section above.",
    },
}

# ---------------------------------------------------------
# Language state (default Vietnamese, judges can switch to English)
# ---------------------------------------------------------
if "lang" not in st.session_state:
    st.session_state["lang"] = "vi"

lang = st.session_state["lang"]
t = TEXT[lang]
lang_idx = 0 if lang == "vi" else 1

st.set_page_config(page_title="Shinhan MIS Agent", page_icon="🏦", layout="centered")

# ---------------------------------------------------------
# Brand styling: Poppins font (closest open-source match to the bold
# rounded sans-serif in Shinhan's branding). Hero section uses blue
# heading text on a plain background (not a solid blue block) + a
# bold italic gray subtitle, per the requested layout.
# Widget colors (buttons, inputs) come from .streamlit/config.toml.
# ---------------------------------------------------------
st.markdown(
    f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700;800&display=swap');

html, body, [class*="css"] {{
    font-family: 'Poppins', sans-serif;
}}

.shinhan-hero {{
    text-align: center;
    padding: 8px 8px 20px 8px;
}}
.shinhan-hero img {{
    height: 56px;
    margin-bottom: 12px;
}}
.shinhan-hero h1 {{
    color: {BRAND_BLUE};
    font-weight: 800;
    font-size: 1.7rem;
    margin: 0 0 10px 0;
    line-height: 1.3;
}}
.shinhan-hero p {{
    color: {BRAND_GRAY};
    font-weight: 700;
    font-style: italic;
    font-size: 0.95rem;
    margin: 0;
    line-height: 1.5;
}}

/* Sidebar sample-question header in brand blue */
[data-testid="stSidebar"] h2 {{
    color: {BRAND_BLUE} !important;
    font-weight: 800 !important;
}}
</style>""",
    unsafe_allow_html=True,
)


def _logo_data_uri() -> str:
    if not os.path.exists(LOGO_PATH):
        return ""
    with open(LOGO_PATH, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()
    return f"data:image/png;base64,{encoded}"


_logo_uri = _logo_data_uri()
logo_html = f'<img src="{_logo_uri}" alt="Shinhan logo">' if _logo_uri else ""

# ---------------------------------------------------------
# Language switch button, top-right-ish (Streamlit puts it inline with
# the rest of the layout since true absolute positioning isn't supported
# without more CSS hacking — a top button above the hero is clear enough).
# ---------------------------------------------------------
_, lang_col = st.columns([4, 1])
with lang_col:
    if st.button(t["lang_switch_label"], use_container_width=True):
        st.session_state["lang"] = "en" if lang == "vi" else "vi"
        st.rerun()

st.markdown(
    f"""<div class="shinhan-hero">
{logo_html}
<h1>{t['title']}</h1>
<p>{t['subtitle']}</p>
</div>""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# Morning Brief (Output 2d) — computed with plain Python/SQL in agent.py,
# not the LLM, so it's free and instant on every page load. Cached in
# session_state per language so switching the toggle re-renders the correct
# language without re-querying the DB every single Streamlit rerun.
# ---------------------------------------------------------
if "digest" not in st.session_state or st.session_state.get("digest_lang") != lang:
    st.session_state["digest"] = generate_daily_digest(lang=lang)
    st.session_state["digest_lang"] = lang

digest = st.session_state["digest"]
with st.container():
    st.markdown(f"**{t['digest_header']}**")
    st.info(digest[lang])

# ---------------------------------------------------------
# Capability bar (Output 4) — 5 pill buttons, one per agent capability
# (query_database, generate_chart, generate_report, Morning Brief,
# whatif_npl_scenario), mirroring a "quick action" row like Claude's
# Strategize/Write/Learn buttons. Clicking a pill shows example questions
# for JUST that capability below it — lets a judge see the full breadth of
# what the agent can do at a glance, then drill into one thing at a time,
# instead of scanning one long undifferentiated list of 10+ questions.
# The clicked pill is rendered with type="primary" (filled, brand color) so
# it's visually obvious which capability is currently expanded.
# ---------------------------------------------------------
if "active_category" not in st.session_state:
    st.session_state["active_category"] = None

cat_cols = st.columns(len(CATEGORIES))
for col, cat in zip(cat_cols, CATEGORIES):
    label = f"{cat['icon']} {cat['label_vi'] if lang == 'vi' else cat['label_en']}"
    is_active = st.session_state["active_category"] == cat["id"]
    with col:
        if st.button(label, key=f"cat_{cat['id']}", use_container_width=True,
                     type="primary" if is_active else "secondary"):
            # Clicking the already-open pill collapses it; clicking a
            # different one switches which capability is expanded.
            st.session_state["active_category"] = None if is_active else cat["id"]
            st.rerun()

active = st.session_state["active_category"]
if active == "digest":
    st.info(t["digest_pointer"])
elif active is not None:
    st.caption(t["capability_questions_caption"])
    for q_pair in CATEGORY_QUESTIONS.get(active, []):
        q = q_pair[lang_idx]
        if st.button(q, key=f"q_{active}_{q}", use_container_width=True):
            st.session_state["pending_question"] = q

# ---------------------------------------------------------
# Sidebar — branding only (capability bar above replaces the old flat
# sample-question list; keeping both would just duplicate the same buttons
# in two places).
# ---------------------------------------------------------
with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=80)

# ---------------------------------------------------------
# Session state: conversation history + OpenAI client
# ---------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state["messages"] = []  # list of dicts: role, content, chart_files, report_files

if "client" not in st.session_state:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        st.error(t["api_key_error"])
        st.stop()
    st.session_state["client"] = OpenAI(api_key=api_key)

client = st.session_state["client"]

# ---------------------------------------------------------
# Render existing conversation history
# ---------------------------------------------------------
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])
        for chart_path in msg.get("chart_files", []):
            if os.path.exists(chart_path):
                st.image(chart_path)
        for report_path in msg.get("report_files", []):
            if os.path.exists(report_path):
                with open(report_path, "rb") as f:
                    st.download_button(
                        label=f"{t['download_label']} {os.path.basename(report_path)}",
                        data=f.read(),
                        file_name=os.path.basename(report_path),
                        key=report_path,
                    )

# ---------------------------------------------------------
# Chat input — either typed by user, or pre-filled by a sidebar button
# ---------------------------------------------------------
prefilled = st.session_state.pop("pending_question", None)
question = st.chat_input(t["chat_placeholder"]) or prefilled

if question:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner(t["spinner"]):
            result = ask_agent(client, question, verbose=False, lang=lang)

        st.write(result["answer"])

        for chart_path in result["chart_files"]:
            if os.path.exists(chart_path):
                st.image(chart_path)

        for report_path in result["report_files"]:
            if os.path.exists(report_path):
                with open(report_path, "rb") as f:
                    st.download_button(
                        label=f"{t['download_label']} {os.path.basename(report_path)}",
                        data=f.read(),
                        file_name=os.path.basename(report_path),
                        key=report_path + "_new",
                    )

    st.session_state["messages"].append(
        {
            "role": "assistant",
            "content": result["answer"],
            "chart_files": result["chart_files"],
            "report_files": result["report_files"],
        }
    )
