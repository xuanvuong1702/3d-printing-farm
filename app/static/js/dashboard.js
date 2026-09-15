/*
 * Alpine.js component cho `/dashboard` (E6-1/C2, fallback SSE thêm ở
 * C3) — xem docstring `app/templates/dashboard.html` cho ngữ cảnh đầy
 * đủ, không lặp lại ở đây.
 *
 * Vai trò (Quyết định MỚI phát sinh #1, docs/State_E6-1_v3.md):
 * Alpine.js đảm nhiệm TOÀN BỘ state reactive/realtime của trang này —
 * mở `EventSource` thủ công tới `/printers/realtime/stream` (SSE có
 * sẵn từ E2-2/C3, KHÔNG sửa), parse từng dòng JSON (mảng
 * `PrinterRealtimeResponse`, xem `app/realtime/schemas.py`), gán thẳng
 * vào `this.printers` để Alpine tự re-render `x-for` — KHÔNG dùng
 * `htmx-ext-sse` vì payload là JSON thô, không phải HTML fragment.
 *
 * `printerDashboardData()` là hàm factory global (Alpine gọi qua
 * `x-data="printerDashboardData()"`) — quy ước chuẩn của Alpine.js
 * cho component có logic phức tạp hơn 1 vài field đơn giản.
 *
 * C3 (MỚI): `sseConnected` theo dõi trạng thái CHÍNH `EventSource`
 * (khác `printer.realtime_connected` per-printer từ backend, xem
 * docstring `dashboard.html`). Logic mở kết nối được tách thành
 * `connectSSE()` để `init()` và `reconnectSSE()` (nút refresh thủ
 * công dự phòng, `docs/UI-Style-Guide.md` mục 5) dùng chung — không
 * lặp lại code mở `EventSource` ở 2 nơi.
 */

function printerDashboardData() {
    return {
        printers: [],
        eventSource: null,
        sseConnected: true,

        /**
         * Khởi tạo state ban đầu từ `rawPrinters` (chuỗi JSON lấy từ
         * thuộc tính `data-printers`, đã render sẵn ở server-side lúc
         * tải trang — xem `app/main.py::dashboard`), rồi mở kết nối
         * SSE để nhận cập nhật tiếp theo. Nếu parse JSON ban đầu lỗi
         * (không nên xảy ra vì server tự sinh chuỗi này, nhưng vẫn
         * phòng thủ), giữ `printers = []` thay vì để lỗi chặn toàn bộ
         * Alpine component.
         */
        init(rawPrinters) {
            try {
                this.printers = JSON.parse(rawPrinters);
            } catch (error) {
                console.error("Không parse được state máy in ban đầu:", error);
                this.printers = [];
            }

            this.connectSSE();
        },

        /**
         * Mở (hoặc mở lại) `EventSource` tới `/printers/realtime/
         * stream`. Đóng kết nối cũ trước nếu có, để `reconnectSSE()`
         * gọi lại hàm này không để rò rỉ 1 `EventSource` cũ vẫn còn
         * mở song song.
         */
        connectSSE() {
            if (this.eventSource) {
                this.eventSource.close();
            }

            this.eventSource = new EventSource("/printers/realtime/stream");

            this.eventSource.onopen = () => {
                this.sseConnected = true;
            };

            this.eventSource.onmessage = (event) => {
                try {
                    this.printers = JSON.parse(event.data);
                } catch (error) {
                    console.error("Không parse được dữ liệu realtime SSE:", error);
                }
            };

            // Lỗi kết nối (mất mạng, server restart, trình duyệt tự
            // retry theo cơ chế mặc định của EventSource...) đánh dấu
            // sseConnected=false để hiện banner + nút refresh thủ công
            // (`.connection-banner`, `app/templates/dashboard.html`).
            this.eventSource.onerror = (error) => {
                console.error("Kết nối SSE realtime gặp lỗi:", error);
                this.sseConnected = false;
            };
        },

        /**
         * Gọi khi người dùng bấm nút "Làm mới" trên banner mất kết
         * nối — mở lại `EventSource` thủ công thay vì chờ cơ chế retry
         * mặc định của trình duyệt.
         */
        reconnectSSE() {
            this.connectSSE();
        },

        /**
         * Format 2 field nhiệt độ thành 1 chuỗi "205°C / 210°C" (đúng
         * `docs/UI-Style-Guide.md` mục 3). `null`/`undefined` (khi
         * `realtime_connected=False`) hiển thị "—" thay vì "0°C" để
         * không nhầm lẫn "chưa có dữ liệu" với "giá trị 0 thật".
         */
        formatTemps(printer) {
            const formatOne = (value) =>
                value === null || value === undefined
                    ? "—"
                    : `${Math.round(value)}°C`;
            return `${formatOne(printer.extruder_temp)} / ${formatOne(printer.bed_temp)}`;
        },

        /**
         * Rút gọn `filename` nếu quá dài (đúng yêu cầu style guide
         * mục 3 "tên file đang in rút gọn nếu quá dài") — ngưỡng 30
         * ký tự, thêm dấu "…" ở cuối phần còn lại.
         */
        truncateFilename(filename) {
            if (!filename) {
                return "";
            }
            const maxLength = 30;
            if (filename.length <= maxLength) {
                return filename;
            }
            return `${filename.slice(0, maxLength - 1)}…`;
        },
    };
}
