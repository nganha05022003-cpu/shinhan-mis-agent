# Masterplan — Shinhan MIS Agent

## Deliverables

**Output 1: Database MIS**

- [x] 1 file SQLite (`shinhan_mis.db`) chứa 4 bảng (`branches`, `loans`, `monthly_revenue`, `npl_records`), mỗi bảng có 6-12 tháng dữ liệu giả lập hợp lý, cộng 1 file Python script tạo lại được data này (để chứng minh khả năng tái tạo, không phải hardcode 1 lần).

> Definition of Done: Chạy được câu SQL SELECT bất kỳ trên 4 bảng và ra kết quả không lỗi, số liệu logic (NPL không âm, revenue có xu hướng theo tháng). ✅ Đã verify — xem `docs/test_cases.md` mục Output 1.

**Output 2: Agent (trái tim dự án)**

- [ ] 1 file Python (`agent.py`) chứa vòng lặp function calling hoàn chỉnh — nhận câu hỏi text, gọi OpenAI API với tool `query_database`, thực thi SQL, trả kết quả về OpenAI, nhận câu trả lời tự nhiên.
- [ ] **Mở rộng (mới thêm — 3/7):** khả năng tạo biểu đồ (chart) từ kết quả truy vấn.
- [ ] **Mở rộng (mới thêm — 3/7):** anomaly detection có thuật toán rõ ràng (không chỉ dựa vào LLM đoán).
- [ ] **Mở rộng (mới thêm — 3/7):** khả năng tổng hợp thành "report" nhiều dữ liệu (đã có sẵn qua SQL aggregation, chính thức hóa thành 1 loại câu hỏi được test).

> Definition of Done (gốc): Trả lời đúng ít nhất 10/10 câu hỏi demo mẫu bạn tự soạn, và xử lý được ít nhất 1 câu hỏi ngoài kịch bản mà vẫn ra kết quả hợp lý.
>
> Definition of Done (mở rộng): Xem chi tiết 3 mục dưới.

### 2a. Chart generation (mới)

**Thiết kế:** thêm 1 tool thứ 2 tên `generate_chart`, song song với `query_database` — model có thể gọi tool này khi câu hỏi ngụ ý so sánh/xu hướng (VD "vẽ biểu đồ doanh thu 12 tháng của Quận 1"). Tool nhận `sql` (để lấy data) + `chart_type` (bar/line) + `title`, dùng matplotlib vẽ, lưu file PNG vào `outputs/charts/`, trả về đường dẫn file cho model xác nhận đã tạo xong.

**Kiến trúc lưu ý:** bản thân agent.py (Output 2) chỉ chịu trách nhiệm *tạo* file chart. Việc *hiển thị* chart cho người dùng xem là việc của Streamlit (Output 4) — Streamlit đọc file PNG này và render bằng `st.image()`. Tách 2 việc để mỗi Output vẫn độc lập, đúng tinh thần "agent = logic, Streamlit = giao diện."

> Definition of Done: Hỏi 1 câu ngụ ý biểu đồ (VD "so sánh doanh thu 6 chi nhánh") → agent gọi đúng tool, sinh ra 1 file PNG hợp lệ, không lỗi, dữ liệu trong chart khớp với SQL.

### 2b. Anomaly detection (mới)

**Thiết kế:** thêm 1 tool thứ 3 tên `detect_anomalies` — thay vì để LLM tự đoán ngưỡng "bất thường," tool này chạy 1 rule cố định: với npl_ratio hoặc total_rev theo tháng/chi nhánh, tính mean + standard deviation, flag bất kỳ điểm nào lệch quá 2 độ lệch chuẩn (2×stdev) so với trung bình. Đây là thuật toán thống kê đơn giản (z-score threshold), không phải model tự bịa ngưỡng.

> Definition of Done: Hỏi "chi nhánh/tháng nào có NPL bất thường?" → agent gọi `detect_anomalies`, trả về đúng (các) chi nhánh/tháng thực sự lệch >2 stdev (verify bằng cách tự tính tay/Python trước), không bỏ sót, không báo sai chi nhánh bình thường thành bất thường.

### 2c. Report generation — file Excel/CSV (làm rõ lại 3/7, sau thảo luận thiết kế với user)

**Khác với trả lời text thường (đã có sẵn nhờ SQL GROUP BY/ORDER BY/SUM/AVG — xem test 2.17-2.19), đây là 1 tính năng riêng: tạo ra 1 file thật**, không chỉ hiển thị trong khung chat.

**Thiết kế (đã chốt sau thảo luận 3/7):** chỉ có **1 tool** tên `generate_report` — nhận `sql` + `filename`, chạy query, dùng `pandas` ghi kết quả ra file `.xlsx` (hoặc `.csv`) vào `outputs/reports/`, trả về đường dẫn file cho model xác nhận. Đây là tool thứ 4 và cũng là tool cuối cùng của agent (cùng với `query_database`, `generate_chart`, `detect_anomalies`).

**Quan trọng — không có tool `export_report` riêng:** "tải xuống file" không phải là hành động model cần quyết định (giống việc user bấm xem chart PNG) — model chỉ *tạo* file, còn *tải xuống* là hành động của user trên giao diện, không phải của model. Vì vậy việc "export/tải xuống" thuộc về Output 4 (Streamlit dùng `st.download_button()` đọc file mà `generate_report` đã tạo sẵn), không phải 1 tool trong `TOOLS` list của agent.py.

**Phạm vi đã chốt (theo lựa chọn của user 3/7):** file Excel/CSV đơn giản — 1 sheet, có header, không cần formatting màu sắc hay nhiều sheet. Không làm dashboard tương tác kiểu Power BI (quá lớn so với 9 ngày còn lại) — nếu muốn dashboard thật sự, đây nên là 1 project/giai đoạn riêng sau buildathon.

**Ví dụ use case:** "Xuất file danh sách 10 khách hàng có rủi ro cao nhất" → agent viết SQL lọc `loans` theo `repayment_status='NPL'` sắp xếp theo `amount` giảm dần LIMIT 10, gọi `generate_report`, trả lời kèm đường dẫn file → Streamlit hiển thị nút tải xuống.

> Definition of Done: Yêu cầu 1 báo cáo cụ thể (VD "tổng hợp doanh thu 6 tháng gần nhất," "top 10 khách hàng rủi ro cao") → agent tạo đúng 1 file Excel/CSV mở được, dữ liệu khớp với SQL, không lỗi, không thiếu dòng; Streamlit hiển thị được nút tải file đó.

**⚠️ Rủi ro thời gian:** 3 mục mở rộng trên (đặc biệt 2a và 2b) là phạm vi thêm ngoài kế hoạch gốc, trong khi deadline chỉ còn 9 ngày (đến 12/7). Nếu đến ngày 8/7 mà Output 2 gốc (function calling cơ bản) chưa chạy ổn định qua 10/10 câu hỏi demo, nên **tạm dừng 2a/2b**, ưu tiên hoàn thiện Output 3 và 4 trước — vì Definition of Done gốc của Output 2 vẫn là điều kiện bắt buộc, còn chart/anomaly là điểm cộng, không phải bắt buộc để nộp bài.

**Output 3: Guardrail**

- [ ] 1 hàm validate (`is_safe_query()`) chặn mọi câu không phải SELECT, cộng danh sách bảng/cột được phép truy vấn.

> Definition of Done: Thử với 3 câu SQL độc hại (DROP TABLE, DELETE FROM, UPDATE...) và cả 3 đều bị chặn với thông báo rõ ràng, không crash hệ thống. Xem `docs/test_cases.md` mục Output 3 để có bộ test đầy đủ (unit + adversarial).

**Output 4: Giao diện demo**

- [ ] 1 app Streamlit (`app.py`) — ô chat, hiển thị câu trả lời, sidebar gợi ý câu hỏi mẫu.
- [ ] Nếu 2a (chart) hoàn thành: hiển thị được file PNG chart trả về từ agent bằng `st.image()`.
- [ ] Nếu 2c (report) hoàn thành: hiển thị nút tải xuống bằng `st.download_button()` đọc file mà `generate_report` đã tạo.

> Definition of Done: Người lạ (không phải bạn) có thể mở app, gõ câu hỏi, nhận câu trả lời đúng trong dưới 30 giây — không cần bạn hướng dẫn gì thêm. Xem `docs/test_cases.md` mục Output 4.

**Deliverable phụ nhưng bắt buộc (theo yêu cầu Devpost):**

- [ ] Video demo 2-3 phút
- [ ] Bài viết mô tả dự án (problem/solution/user) cho Devpost
- [ ] Tài liệu AI documentation — giải thích model dùng, cơ chế function calling
- [ ] Repo GitHub public, judges truy cập được

## Schedule

**2/7 (chiều - tối):** ✅ Hoàn thành — cài môi trường, test OpenAI API, tạo repo GitHub, cấu trúc thư mục cơ bản.

**3/7:** ✅ Hoàn thành — schema 4 bảng thiết kế xong, `generate_data.py` viết + test xong, `docs/test_cases.md` viết xong (bao gồm cả câu hỏi ranking + explanatory mới thêm), `agent.py` khung function-calling cơ bản đã viết.

**4/7:**
- Verify `OPENAI_API_KEY` hoạt động thật (test 1 câu đơn giản)
- Chạy thử `agent.py` qua toàn bộ câu hỏi demo cơ bản (2.1-2.16 trong test_cases.md)
- Viết thêm câu hỏi mẫu nếu cần cho đủ 15-20 câu

**5/7:**
- Sửa lỗi phát sinh từ bước 4, mở rộng để trả lời tốt câu hỏi so sánh/theo quý
- Bắt đầu 2a (chart generation) nếu Output 2 gốc đã ổn định

**6/7:**
- Tiếp tục 2a/2b (chart + anomaly detection) — theo nguyên tắc "rủi ro thời gian" ở trên
- Viết system prompt hoàn chỉnh, tinh chỉnh dựa trên kết quả test

**7/7:**
- Hoàn thiện Output 2 (bao gồm mở rộng nếu kịp) tới mức ổn định
- Bắt đầu Output 3 (guardrail) — chạy bộ test 3.1-3.11 trong test_cases.md

#### Giai đoạn workshop (8-10/7 — ENABLE)

**8-10/7:**
- Tham dự workshop liên quan "model integration," "deployment patterns"
- Buổi tối: hoàn thiện Output 3 + bắt đầu Output 4 (Streamlit)
- 10/7: xác nhận track trên Devpost trước 9:00 PM — hard deadline
- Cuối 10/7: Output 1, 2, 3 chạy end-to-end, Output 4 có khung sườn

#### Ngày build chính thức (11/7 — BUILD)

**Sáng:** rà soát Output 2, tận dụng mentor nếu còn lỗi; hoàn thiện Output 4, kết nối Streamlit với agent (bao gồm hiển thị chart nếu có).

**Chiều:** test toàn bộ hệ thống qua bộ `docs/test_cases.md` (cả 4 outputs), sửa lỗi phát sinh, polish giao diện.

**Tối:** quay video demo, viết bài Devpost, viết AI documentation.

## Nguồn tham khảo

- Devpost requirements: problem statements 1/7, xác nhận track 10/7 9PM ICT, deadline nộp bài 12/7 9AM ICT.
- Bộ test case đầy đủ: `docs/test_cases.md`
