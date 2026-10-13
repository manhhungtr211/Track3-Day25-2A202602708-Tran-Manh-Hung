# Lab 25B — Report

**Họ tên / nhóm:** Trần Mạnh Hùng (MSV: 2A202602708)  **Commit:** `34de3386194cf873ae46062374e2a602196892f8`  **Điểm tự động (`make grade`):** 88 / 88

> Mọi nhận định phải có số. Dán số từ `reports/gameday/*.json`, `reports/workflows.json`, `reports/slo.json`.

---

## 1. Kết quả Game Day (naive vs của bạn)

| Băng | Client | availability | p95 (s) | amplification | retry_ratio | cost/turn | recovery (s) | PASS? |
|---|---|---:|---:|---:|---:|---:|---:|:---:|
| G1 | naive | 0.7194 | 7.157 | 7.738 | 6.738 | 2.163 | 4.0 | FAIL |
| G1 | yours | 1.0000 | 4.776 | 1.062 | 0.002 | 2.101 | 0.0 | **PASS** |
| G2 | naive | 0.4560 | 15.034 | 9.904 | 8.904 | 4.627 | 155.0 | FAIL |
| G2 | yours | 1.0000 | 11.217 | 1.044 | 0.005 | 2.129 | 0.0 | **PASS** |
| G3 | naive | 1.0000 | 4.768 | 1.164 | 0.164 | 2.328 | 0.0 | FAIL |
| G3 | yours | 1.0000 | 4.718 | 1.065 | 0.035 | 2.146 | 0.0 | **PASS** |
| G4 | naive | 1.0000 | 13.764 | 1.004 | 0.004 | 2.008 | 0.0 | FAIL |
| G4 | yours | 1.0000 | 5.684 | 1.013 | 0.002 | 2.069 | 0.0 | **PASS** |
| G5 | naive | 0.3699 | 5.359 | 17.030 | 16.030 | 2.124 | null | FAIL |
| G5 | yours | 0.8179 | 5.477 | 1.003 | 0.002 | 2.504 | null | **PASS** |
| G6 | naive | 0.5170 | 4.836 | 12.344 | 11.344 | 2.009 | 0.0 | FAIL |
| G6 | yours | 1.0000 | 5.958 | 1.053 | 0.019 | 2.203 | 0.0 | **PASS** |

---

## 2. Cấu hình client và lý do

| Tham số | Giá trị | Vì sao (dựa trên băng nào, số liệu nào) |
|---|---:|---|
| attempt_timeout_s | 8.0 | Đảm bảo call đủ thời gian hoàn thành (alpha service median 1.2s, queue timeout 10s); tránh chờ đến user deadline (30s) gây rớt toàn bộ lượt khi provider treo ở G6. |
| max_attempts_per_provider | 2 | Cho phép thử lại đúng 1 lần trên cùng provider cho lỗi tạm thời, nếu vẫn lỗi thì fail over ngay, tránh retry storm (G2, G6). |
| backoff_base_s / cap_s | 0.25 / 4.0 | Base 0.25s đủ nhỏ để phản ứng nhanh, cap 4.0s tránh trễ quá lâu so với deadline 30s của user; full jitter phân tán traffic đồng thời (G1, G2). |
| retry_budget_ratio | 0.1 | Giới hạn retry tối đa ~10% tổng số request mới; ngăn cản retry storm bùng nổ khi sự cố diện rộng xảy ra (G2, G3 giữ retry_ratio = 0.035 ≤ 0.10). |
| breaker.window_size / min_calls | 20 / 10 | Window 20 đủ phản ánh xu hướng gần nhất; min_calls 10 ngăn chặn việc trip nhầm breaker vào lúc tải thấp chỉ vì 1–2 lỗi ngẫu nhiên. |
| breaker.failure_rate_threshold | 0.5 | Trip sang OPEN khi 50% call trong cửa sổ thất bại, bảo vệ provider đang suy thoái (G2, G6). |
| breaker.slow_call_s | 5.0 | Bắt các call chậm bất thường (bình thường alpha ~1.2s, chậm thêm 5s = 6.2s); nhận diện degradation trước khi biến thành timeout (G4 hạ p95 từ 13.76s xuống 5.68s). |
| breaker.open_duration_s | 10.0 | Thời gian chờ provider hồi phục trước khi thử thăm dò lại ở HALF-OPEN; đủ dài để giảm áp lực lên provider mà không bỏ quên provider quá lâu. |
| breaker.half_open_max_probes | 2 | Chỉ cho phép tối đa 2 request probe đồng thời ở HALF-OPEN, tránh hiện tượng thundering herd đè bẹp provider vừa mới hồi phục. |

### Sơ đồ quyết định của `on_error`:

```
                    +------------------------+
                    |      on_error(err)     |
                    +-----------+------------+
                                |
                   classify(err) -> ErrorKind
                                |
      +-------------------------+-------------------------+
      |                         |                         |
[CLIENT_ERROR]          [QUOTA_EXHAUSTED]           [RATE_LIMITED]
  (400, 401, 422)         (429 quota)                (429 rate limit)
      |                         |                         |
breaker.release()       breaker.release()          breaker.release()
      |                         |                         |
GiveUp("client_error")  cooldown += 300s           cooldown += retry_after
(Không retry,           failover ngay              failover ngay
 không failover)               |                         |
                               +------------+------------+
                                            |
                                            v
                                 [OVERLOADED / SERVER / TIMEOUT]
                                   (503, 529, 500, status 0)
                                            |
                                  breaker.on_failure()
                                            |
                         +------------------+------------------+
                         | tries < max_attempts AND            |
                         | breaker != OPEN AND                 |
                         | budget.acquire() AND                |
                         | now + delay + 1s <= deadline?       |
                         +------------------+------------------+
                                            |
                                  +---------+---------+
                                  | YES               | NO
                                  v                   v
                            Retry cùng           Failover sang
                            provider             provider kế tiếp
                            (delay with          (idx + 1, delay=0)
                             full jitter)
```

---

## 3. Ba thí nghiệm (mỗi cái 3–5 câu + số)

1. **Retry budget là lớp phòng thủ cuối:**
   Khi breaker bị vô hiệu hóa (`min_calls: 1.0e9`) và `max_attempts_per_provider: 5`, nếu có retry budget (ratio=0.1), amplification ở G2 được giữ chặt ở mức an toàn (~1.09), retry ratio không vượt quá 0.09 và recovery_s duy trì 0s. Khi tắt budget, mỗi request liên tục retry 5 lần vào provider đang quá tải, đẩy amplification vọt lên > 4.5, hàng đợi server bị bão hòa khiến recovery kéo dài > 120s giống hệt naive client. Khi breaker hoạt động đúng, nó ngắt sớm sau khi đạt ngưỡng lỗi; tuy nhiên retry budget vẫn là lớp phòng thủ thiết yếu vì trong giai đoạn "cold-start" (khi chưa gom đủ `min_calls` để mở breaker), budget là cơ chế duy nhất ngăn chặn bão retry bùng nổ tức thì.

2. **`attempt_timeout_s: 30`:**
   Ở băng G6 (provider A bị outage treo vĩnh viễn), khi đặt timeout 30s bằng đúng deadline của user, lượt đầu tiên gửi vào `alpha` hoặc `alpha-eu` sẽ bị treo toàn bộ 30s cho tới khi hết hạn. Khi đó, client không còn đủ thời gian để fail over sang `beta`, dẫn đến hàng loạt lượt bị `GiveUp("deadline")` và availability sụt giảm nghiêm trọng từ 100% xuống dưới 55%. Ở băng G4, availability vẫn đạt 100% vì provider chỉ bị chậm thêm 5s (khoảng 6.2s tổng cộng), hoàn toàn nằm trong giới hạn 30s. Thí nghiệm này cho thấy `latency_p95_s` chỉ tính trên các lượt thành công (survivorship bias), khiến số liệu p95 trông có vẻ "đẹp" trong khi availability thực tế đã sụp đổ. Timeout "đúng" phải dựa trên p99 lúc hoạt động bình thường cộng một biên an toàn nhỏ, đồng thời phải chừa đủ thời gian (`turn_deadline - attempt_timeout > min_time_left`) để client kịp fail over sang provider thay thế.

3. **Bỏ lọc tool calling khỏi `ladder()`:**
   Khi bỏ lọc `supports_tools` khỏi `ladder()` và chạy băng G6 (toàn bộ provider A sập, buộc dồn tải sang các provider phía sau), client sẽ vô tư điều phối tool call sang `gamma-mini` (vốn là model C-mini giá rẻ không hỗ trợ function calling). Kết quả là `incompatible_routes` tăng vọt từ 0 lên hàng trăm lượt thất bại. Dưới góc nhìn Product Manager, việc này tương đương với việc mang yêu cầu xử lý thanh toán tự động phức tạp giao cho một model chỉ biết tạo văn bản tĩnh, khiến khách hàng nhận thông báo lỗi hệ thống và toàn bộ quy trình hoàn tiền bị ách tắc hoàn toàn, làm tổn hại uy tín thương hiệu dịch vụ.

---

## 4. Workflow hoàn tiền

| Executor | completion | crashes | duplicate refunds | duplicate emails | lost refunds | overpaid | wasted LLM / crash |
|---|---:|---:|---:|---:|---:|---:|---:|
| naive | 1.0 | 81 | 128 | 81 | 0 | 14,967.00 $ | 1.123 |
| yours | 1.0 | 66 | 0 | 0 | 0 | 0 $ | 0.303 |

**Trả lời:**
- Chỉ có idempotency key là **chưa đủ**, vì khi worker bị crash giữa chừng, worker mới khởi động lại nếu không có checkpoint sẽ phải chạy lại từ đầu tất cả các bước (tốn chi phí và latency gọi lại LLM classify, lookup order, summarize).
- Chỉ có checkpoint cũng **chưa đủ**, vì sự cố mạng (`ServiceTimeout`) có thể làm mất phản hồi sau khi lệnh thanh toán đã thực thi thành công ở ngân hàng; khi retry bước đó mà không có idempotency key thì tiền sẽ bị trừ lần thứ hai (duplicate refund).
- Nếu ghi checkpoint **trước** khi gọi payment: Trong trường hợp bước payment bị crash hoặc thất bại, worker mới khởi động lại thấy checkpoint đã tồn tại sẽ ngộ nhận rằng việc hoàn tiền đã xong và chuyển sang gửi email, dẫn đến tình trạng mất khoản hoàn tiền của khách hàng (**lost refund**).

---

## 5. Alerting & error budget

| Sự cố | Mong đợi | Naive alerter | Burn-rate alerter | Độ trễ phát hiện |
|---|---|---|---|---:|
| checkout_bug | page | page (delay 1m) | page | 27 min |
| provider_outage | page | page (delay 0m) | page | 4 min |
| deploy_blip | none | **page (false alert)** | none | Không kích hoạt (Đúng) |
| slow_leak | ticket | **none (missed)** | ticket | 1337 min |
| degraded_evening | page | page (delay 214m) | page | 291 min |
| night_spike | none | **page (false alert)** | none | Không kích hoạt (Đúng) |
| small_bump | none | **page (false alert)** | none | Không kích hoạt (Đúng) |

**Error budget 30 ngày:**
- Tổng request: 25,901,755
- Số lỗi cho phép (SLO 99.5%): 129,508.8
- Số lỗi thực tế: 55,618
- Độ khả dụng thực tế: 99.7853%
- Đã tiêu: **42.95%**, còn lại: **57.05%**.
- **Không đóng băng release** (`freeze_releases: false`).
- Sự cố `small_bump` (20 phút ở 12% lỗi) **không nên đánh thức ai lúc 11 giờ trưa** vì tổng lượng lỗi nó gây ra trong 20 phút chỉ tiêu tốn một phần cực nhỏ error budget tháng (< 0.5% ngân sách). Nó không đe dọa phá vỡ cam kết SLO 30 ngày, việc page vô tội vạ sẽ gây báo động giả (alert fatigue) và làm gián đoạn công việc của kỹ sư on-call.

---

## 6. Postmortem — Băng G2 với client naive (blameless)

*Dựa trên biểu đồ `reports/gameday/G2_overload_storm.png` và file dữ liệu `reports/gameday/naive_G2_overload_storm_timeseries.csv`.*

![G2 Overload Storm](/Track3-Day25-2A202602708-Tran-Manh-Hung/reports/gameday/G2_overload_storm.png)

**Tóm tắt (3 câu):**
Vào giây ảo t=120, provider primary `alpha` bị sụt giảm 75% năng lực xử lý phần cứng dẫn đến hàng đợi bị ứ đọng và phát sinh lỗi quá tải 529. Bản client v1 (NaiveClient) phản ứng bằng cơ chế retry 3 tầng chồng chéo không có jitter, không có circuit breaker và phớt lờ `retry-after`, đẩy lượng attempt lên gấp 10 lần bình thường (đạt đỉnh > 250 req/s). Điều này tạo ra một sự cố siêu bền vững (metastable failure), khiến hệ thống tê liệt hoàn toàn và mất thêm 155 giây sau khi provider đã khôi phục phần cứng mới có thể trở lại bình thường.

**Ảnh hưởng:**
- Tổng số lượt người dùng bị thất bại: 1,285 lượt (availability chỉ đạt 45.60%).
- Thời gian gián đoạn: Kéo dài từ t=122 đến t=453 (khoảng 331 giây ảo).
- Chi phí tổn thất: Tiêu tốn 4,983 credit cho chỉ 1,077 lượt thành công (cost/turn vọt lên 4.627 so với mức bình thường ~2.10).
- Tiêu tốn tương đương 54.4% error budget của cả tháng chỉ trong một sự cố duy nhất 5 phút.

**Timeline (giây ảo — lấy từ CSV):**

| t | Sự kiện | Bằng chứng (số) |
|---:|---|---|
| 120 | alpha mất 75% năng lực xử lý | `calls_ok` bắt đầu sụt giảm từ 10 req/s xuống 2 req/s. |
| 136 | Bão retry bùng nổ | `attempts_alpha` vọt từ 24 lên 53 req/s rồi đạt 254 req/s tại t=144. |
| 137 | Hệ thống sập hoàn toàn | `calls_ok` rớt về đúng 0 req/s; 100% request bị nghẽn trong hàng đợi. |
| 300 | alpha hồi phục hoàn toàn phần cứng | Provider khôi phục năng lực 18 slot, nhưng `calls_ok` vẫn bằng 0. |
| 305–450 | Metastable failure tự duy trì | Hàng đợi server vẫn ngập rác bởi các request retry cũ bị client timeout nhưng server vẫn tiếp tục xử lý; attempts duy trì 50–170 req/s. |
| 453 | Bão retry tan dần | Các request cũ xả hết hàng đợi; `calls_ok` bắt đầu có kết quả (4 req/s). |
| 455 | Hệ thống hồi phục hoàn toàn | `calls_ok` ổn định trở lại ở mức 8–16 req/s (`recovery_s = 155s`). |

**Trigger vs Root Cause:**
- **Trigger**: Provider `alpha` mất 75% năng lượng GPU trong 3 phút (t=120 đến t=300).
- **Root Cause**: Bản client v1 có 3 tầng retry xếp chồng mà không có backoff ngẫu nhiên hóa (no full jitter), không có circuit breaker để ngắt mạch tải, không có retry budget chặn trần, và hoàn toàn bỏ qua header `retry-after`.
- **Cơ chế Metastable Failure**: Khi client hết kiên nhẫn và timeout, server FIFO vẫn tiếp tục tốn năng lực xử lý những request đã chết đó. Client lại lập tức bắn thêm các attempt retry mới vào hàng đợi, tạo thành một vòng lặp tự nuôi dưỡng (self-sustaining feedback loop) khiến server luôn quá tải ngay cả khi phần cứng đã hồi phục 100%.

**Điều gì giúp / Điều gì làm tệ hơn:**
- *Làm tệ hơn*: 3 tầng retry nhân số lượng request lên tối đa 36 lần; retry cố định không có jitter tạo ra thundering herd; phớt lờ chỉ định giãn tải `retry-after` từ server.
- *Giúp khắc phục*: Cắt giảm retry xuống tối đa 1 lần trên cùng provider; áp dụng Sliding-Window Circuit Breaker để cách ly provider suy thoái và failover sang `alpha-eu`/`beta`; áp dụng Retry Budget khống chế lượng retry dưới 10% traffic.

**Action Items:**

| # | Hành động | Loại (prevent / detect / mitigate) | Owner | Cách kiểm chứng |
|---|---|---|---|---|
| 1 | Triển khai `ResilientClient` với Circuit Breaker và Full Jitter Backoff thay thế toàn bộ `NaiveClient` trên production | Prevent | Team Core Agent | Chạy Game Day G2 đạt availability 100%, recovery_s = 0 |
| 2 | Cấu hình hệ thống Multi-window Burn-rate Alerting (SRE Workbook) theo dõi SLO 99.5% | Detect | Team SRE | Chạy kịch bản SLO kiểm tra 0 false page trên các đợt blip |
| 3 | Tích hợp Fallback Ladder tự động chuyển vùng sang `alpha-eu` và `beta` khi primary quá tải | Mitigate | Team Infra | Diễn tập sự cố cắt tải vùng chính mà dịch vụ không gián đoạn |

---

## 7. Một điểm yếu còn lại

**Tình huống còn yếu:**
Trong sự cố G2, mặc dù `ResilientClient` đạt availability 100%, nhưng độ trễ p95 bị đẩy lên mức **11.217s** (so với mức bình thường ~4.7s). Lý do là trước khi circuit breaker kịp nhận diện đủ số lượng call chậm/lỗi (`min_calls: 10`) để trip sang OPEN, một số lượng request ban đầu vẫn phải chờ đợi trong hàng đợi của `alpha` cho tới gần ngưỡng timeout 8s rồi mới chuyển vùng sang `alpha-eu` hoặc `beta`. Ngoài ra, khi chuyển tải ồ ạt sang `beta`, chi phí vận hành tăng thêm 40% (cost 1.4/call so với 1.0 của alpha).

**Cách cải tiến:**
1. **Hedging / Speculative Retry**: Với các request quan trọng, nếu sau một khoảng thời gian p90 (ví dụ 2.0s) mà provider chính chưa phản hồi, tự động gửi thêm một request song song tới provider dự phòng (`alpha-eu`), request nào về trước thì lấy và hủy request còn lại, giúp triệt tiêu đuôi trễ p95/p99.
2. **Dynamic Cost-aware Load Balancing**: Phân bổ tỷ lệ tải mềm (soft traffic splitting) thay vì failover bậc thang cứng nhắc, ưu tiên `alpha-eu` (cùng giá 1.0) trước khi tràn sang `beta` (giá 1.4), giảm chi phí xuống mức tối ưu nhất.

---

## 8. Cache (Part G)

Số liệu từ `reports/cache/*.json` (`make cache`):

| Client | availability | faq_availability | cache_hit_ratio | stale_served | wrong_answers | outdated_answers | cost/turn | PASS? |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| resilient (không cache) | 0.8373 | 0.8424 | 0.000 | 0 | 0 | 0 | 1.386 | FAIL |
| cached (của bạn) | 0.9124 | 0.9645 | 0.652 | 157 | 0 | 3 | 0.915 | **PASS** |

| Tham số `cache:` | Giá trị | Vì sao (số liệu nào) |
|---|---:|---|
| similarity_threshold | 0.90 | Đảm bảo tính ngữ nghĩa chặt chẽ; loại bỏ các câu hỏi gần giống nhưng khác ý (ngăn `wrong_answers = 0`). |
| ttl_s | 60 | Giữ câu trả lời tươi mới; giới hạn câu trả lời cũ khi biểu phí thay đổi âm thầm ở t=480 (`outdated_answers = 3 ≤ 15`). |
| max_stale_s | 300 | Cho phép phục vụ câu trả lời cũ khi toàn bộ provider sập trong 120s (t=240–360), giúp `faq_availability` đạt 96.45% (≥ 93%). |

### Thí nghiệm:

1. **Ngưỡng similarity:**
   Khi để ngưỡng mặc định quá lỏng 0.80, cache hit ratio tăng lên ~72% và cost giảm nhẹ, nhưng xuất hiện `wrong_answers > 0` (ví dụ câu hỏi "Thời gian hoàn tiền bao lâu?" bị nhầm với "Chính sách hoàn tiền thế nào?"). Khi đặt ngưỡng quá chặt 0.97, `wrong_answers` bằng 0 nhưng hit ratio giảm mạnh xuống < 40%, không đạt yêu cầu benchmark. Ngưỡng 0.90 là điểm cân bằng hoàn hảo: `wrong_answers = 0`, hit ratio đạt 65.2% và chi phí/lượt giảm sâu từ 1.386 xuống 0.915.

2. **TTL:**
   Khi thử nghiệm với `ttl_s = 300`, sau khi bảng giá vận chuyển đổi âm thầm ở t=480, có tới 38 câu trả lời bị sai lệch (`outdated_answers = 38 > 15`). Khi hạ `ttl_s` xuống 60s, số câu trả lời cũ giảm xuống chỉ còn 3 lượt. Webhook chỉ kích hoạt khi hệ thống CMS chủ động thông báo (như chính sách hoàn tiền ở t=150), còn khi nhà vận chuyển đổi giá mà không báo trước, webhook hoàn toàn bất lực. Do đó, TTL chính là cơ chế phòng thủ duy nhất để khống chế trần thời gian tồn tại của dữ liệu cũ.

3. **Stale-if-error:**
   Khi đặt `max_stale_s: 0` (tắt tính năng stale-if-error), trong khoảng thời gian t=240–360 khi mọi provider sập hoàn toàn, các câu hỏi FAQ đã hết hạn TTL sẽ không thể lấy được câu trả lời thay thế nào, khiến `faq_availability` tụt từ 96.45% xuống chỉ còn 84.24% (không đạt chuẩn ≥ 93%). Bật `max_stale_s: 300` giúp phục vụ 157 lượt stale cứu nguy cho hệ thống trong đợt outage.

### Trả lời câu hỏi:

- **Vì sao câu hỏi cá nhân ("Đơn #48213 của tôi đang ở đâu?") không được cache, kể cả khi cache theo từng user?**
  1. *Tính động và thay đổi trạng thái*: Trạng thái đơn hàng, số dư tài khoản thay đổi liên tục theo thời gian thực; phục vụ câu trả lời cache cũ có thể khiến khách hàng nhận thông tin sai lệch về việc giao hàng hoặc hoàn tiền.
  2. *Bảo mật & rủi ro rò rỉ dữ liệu (PII)*: Dữ liệu cá nhân có tính nhạy cảm cao, nếu thuật toán đối sánh semantic cache bị lỏng lẻo hoặc lỗi phân tách session, thông tin đơn hàng và địa chỉ của khách hàng này có thể bị trả nhầm cho khách hàng khác.

- **Trong lúc mọi provider sập, dashboard vẫn xanh nhờ cache. Bạn sẽ alert dựa trên số nào để on-call vẫn biết có sự cố?**
  Không được chỉ dựa vào metric `user_availability` hay mã HTTP 200 ở tầng gateway (vì cache đã che giấu sự cố). Cần thiết lập alert dựa trên:
  1. **Provider upstream error rate / health-check failure**: Theo dõi trực tiếp tỷ lệ lỗi 5xx/529 trả về từ các provider và trạng thái OPEN của các Circuit Breaker.
  2. **Tỷ lệ phục vụ dữ liệu cũ (`stale_served_ratio` hoặc `stale_served > 0`)**: Đột biến về số lượng câu trả lời stale-if-error là tín hiệu rõ ràng cho thấy hệ thống đang "sống sót nhờ dữ liệu cũ" do backend đã chết.
