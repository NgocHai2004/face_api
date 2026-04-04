# Hướng dẫn tích hợp đăng ký NFC + Khuôn mặt (App Integration Guide)

> **Base URL:** `http://<SERVER_IP>:8000`  
> **Content-Type:** `application/json` (trừ SSE stream)

---

## Tổng quan luồng đăng ký

Có **2 flow** để đăng ký người dùng với thẻ NFC:

| Flow | Khi nào dùng | Đặc điểm |
|------|-------------|----------|
| **Flow A – Stream + Finish** | App có UI camera live | SSE stream 2 bước, kiểm soát khi nào lưu |
| **Flow B – Start → Stream → Finish** | Cần khởi tạo user trước | 3 bước, tách biệt rõ ràng |

**Điều kiện đăng ký thành công (ít nhất 1 trong 2):**
- Đã chụp đủ **3 góc khuôn mặt** (Thẳng, Trái, Phải)
- Đã quét được **thẻ NFC** (`card_id`)

---

## Flow A – 2 bước (Khuyến nghị)

```
App                         Server                     NFC Reader (PN532)
 |                              |                              |
 |-- GET /enroll/nfc/stream --> |                              |
 |   (SSE: auto tạo session)    |                              |
 |<-- session_created ----------|                              |
 |<-- stream_started -----------|                              |
 |<-- angle_instruction --------|  (hướng dẫn quay mặt)       |
 |                              |<-- POST /enroll/nfc/card ----|
 |<-- nfc_scanned --------------|                              |
 |<-- enroll_nfc_angle ---------|  (mỗi góc thành công)        |
 |<-- face_complete ------------|  (đủ 3 góc)                  |
 |                              |                              |
 |-- POST /enroll/nfc/finish -> |                              |
 |<-- { success: true } --------|                              |
 |<-- stream_ended -------------|                              |
```

### Bước 1 — Kết nối SSE stream

```
GET /enroll/nfc/stream?username=alice&position=NhanVien&source=0&expiry_date=2027-12-31
```

| Param | Bắt buộc | Mô tả |
|-------|----------|-------|
| `username` | ✅ | Tên người dùng (không dấu, không space) |
| `position` | ❌ | Chức vụ (VD: `NhanVien`, `QuanLy`, `BaoVe`) |
| `source` | ❌ | Camera source: `0` = webcam, hoặc RTSP/HTTP URL (mặc định: `0`) |
| `expiry_date` | ❌ | Ngày hết hạn ISO 8601 (VD: `2027-12-31` hoặc `2027-12-31T00:00:00`) |

**Headers cần thiết:**
```
Accept: text/event-stream
Cache-Control: no-cache
```

**Ví dụ (JavaScript):**
```javascript
const source = new EventSource(
  'http://192.168.1.100:8000/enroll/nfc/stream?username=alice&position=NhanVien&source=0'
);

source.onmessage = (e) => {
  const data = JSON.parse(e.data);
  handleSSEEvent(data);
};
```

**Ví dụ (Dart/Flutter):**
```dart
final request = http.Request(
  'GET',
  Uri.parse('http://192.168.1.100:8000/enroll/nfc/stream'
    '?username=alice&position=NhanVien&source=0'),
);
request.headers['Accept'] = 'text/event-stream';
final response = await client.send(request);
response.stream
  .transform(utf8.decoder)
  .transform(const LineSplitter())
  .listen((line) {
    if (line.startsWith('data: ')) {
      final data = jsonDecode(line.substring(6));
      handleSSEEvent(data);
    }
  });
```

### Bước 2 — Kết thúc đăng ký

```
POST /enroll/nfc/finish?username=alice
```

Gọi sau khi app thấy đủ điều kiện (face_complete hoặc nfc_scanned).

**Response thành công (HTTP 200):**
```json
{
  "success": true,
  "satisfied": true,
  "username": "alice",
  "position": "NhanVien",
  "expiry_date": "2027-12-31T00:00:00",
  "face_ok": true,
  "card_ok": true,
  "angles_captured": ["THANG", "TRAI", "PHAI"],
  "card_id": "A66AB0AA",
  "registered_with": "khuôn mặt 3 góc + thẻ NFC (A66AB0AA)",
  "message": "✅ Đăng ký thành công cho 'alice' với: khuôn mặt 3 góc + thẻ NFC (A66AB0AA)."
}
```

**Response lỗi chưa đủ điều kiện (HTTP 422):**
```json
{
  "success": false,
  "satisfied": false,
  "face_ok": false,
  "card_ok": false,
  "angles_captured": ["THANG"],
  "missing_angles": ["TRAI", "PHAI"],
  "card_id": null,
  "username": "alice",
  "message": "❌ Chưa đủ điều kiện đăng ký..."
}
```

---

## Flow B – 3 bước (Tách biệt)

```
POST /enroll/nfc/start      → Tạo user + session
GET  /enroll/nfc/stream     → SSE stream camera (song song với NFC reader)
POST /enroll/nfc/finish     → Lưu DB
```

### Bước 0 — Khởi tạo trước (tùy chọn)

```
POST /enroll/nfc/start?username=alice&position=NhanVien&expiry_date=2027-12-31
```

**Response (HTTP 200):**
```json
{
  "success": true,
  "user_status": "created",
  "username": "alice",
  "position": "NhanVien",
  "expiry_date": "2027-12-31T00:00:00",
  "message": "✅ Đã tạo user 'alice'...",
  "next_steps": {
    "sse_stream": "GET /enroll/nfc/stream?username=alice&source=0",
    "submit_card": "POST /enroll/nfc/card?username=alice&card_id=<UID>",
    "finish": "POST /enroll/nfc/finish?username=alice"
  }
}
```

---

## SSE Events — Tất cả sự kiện

### Bảng tổng hợp

| `event` | `done` | Mô tả | App nên làm gì |
|---------|--------|-------|----------------|
| `session_created` | false | Session vừa được tạo tự động | Hiển thị thông báo khởi động |
| `stream_started` | false | Camera bắt đầu chạy | Hiện camera preview |
| `angle_instruction` | false | Hướng dẫn góc cần quay | Cập nhật UI hướng dẫn, hiển thị `frame_b64` |
| `angle_mismatch` | false | Sai góc, chờ điều chỉnh | Hiện cảnh báo, cập nhật preview |
| `enroll_nfc_angle` | false | Chụp 1 góc thành công | Cập nhật progress bar, hiện `face_crop_b64` |
| `nfc_scanned` | false | Thẻ NFC vừa được quét | Hiện thông báo thẻ, cho phép nhấn Finish |
| `face_complete` | false | Đủ 3 góc, chờ lệnh finish | Kích hoạt nút "Hoàn thành" |
| `frame_error` | false | Lỗi đọc frame camera | Hiện cảnh báo nhỏ, tự retry |
| `embedding_error` | false | Không trích được face vector | Hiện cảnh báo, tự retry |
| `user_deleted` | true | User bị xóa giữa chừng | Thoát luồng đăng ký |
| `stream_ended` | true | Stream kết thúc | Đóng kết nối SSE |

### Chi tiết từng event

#### `session_created`
```json
{
  "event": "session_created",
  "username": "alice",
  "message": "✅ Đã tạo session đăng ký cho 'alice'.",
  "done": false
}
```

#### `stream_started`
```json
{
  "event": "stream_started",
  "username": "alice",
  "message": "🎬 Bắt đầu stream đăng ký cho 'alice'...",
  "done": false
}
```

#### `angle_instruction`
```json
{
  "event": "angle_instruction",
  "step": 1,
  "total_steps": 3,
  "required_angle": "THANG",
  "frame_b64": "<base64 JPEG>",
  "done": false,
  "message": "Bước 1/3 — Hãy quay mặt: Thẳng"
}
```

> `frame_b64`: JPEG full frame, decode base64 để hiển thị preview camera.  
> `required_angle`: một trong `THANG` | `TRAI` | `PHAI`

#### `angle_mismatch`
```json
{
  "event": "angle_mismatch",
  "step": 1,
  "total_steps": 3,
  "required_angle": "THANG",
  "direction": "TRAI",
  "frame_b64": "<base64 JPEG>",
  "done": false,
  "message": "Cần: Thẳng, đang: Trái"
}
```

#### `enroll_nfc_angle` ✅
```json
{
  "event": "enroll_nfc_angle",
  "type": "face_recognition",
  "step": 1,
  "total_steps": 3,
  "required_angle": "THANG",
  "captured": "THANG",
  "username": "alice",
  "position": "NhanVien",
  "expiry_date": "2027-12-31T00:00:00",
  "source": "0",
  "timestamp": "2026-04-03T10:15:30.123456",
  "face_crop_b64": "<base64 JPEG ảnh mặt crop>",
  "frame_b64": "<base64 JPEG full frame>",
  "done": false,
  "message": "✅ Đã chụp góc Thẳng cho 'alice'!"
}
```

> `face_crop_b64`: ảnh khuôn mặt đã crop, dùng để hiển thị preview góc đã chụp.

#### `nfc_scanned` 💳
```json
{
  "event": "nfc_scanned",
  "username": "alice",
  "card_id": "A66AB0AA",
  "done": false,
  "message": "💳 Đã quét thẻ: A66AB0AA. Nhấn 'Kết thúc' để lưu."
}
```

#### `face_complete`
```json
{
  "event": "face_complete",
  "username": "alice",
  "angles_captured": ["THANG", "TRAI", "PHAI"],
  "card_id": "A66AB0AA",
  "done": false,
  "message": "✅ Đã chụp đủ 3 góc mặt! Nhấn 'Kết thúc' để lưu."
}
```

#### `stream_ended`
```json
{
  "event": "stream_ended",
  "username": "alice",
  "done": true,
  "message": "🔚 Stream đã kết thúc."
}
```

---

## API phụ trợ

### Xem trạng thái session

```
GET /enroll/nfc/session-status?username=alice
```

**Response:**
```json
{
  "has_session": true,
  "username": "alice",
  "position": "NhanVien",
  "expiry_date": "2027-12-31T00:00:00",
  "angles_captured": ["THANG", "TRAI"],
  "missing_angles": ["PHAI"],
  "face_ok": false,
  "card_id": "A66AB0AA",
  "card_ok": true,
  "finished": false,
  "ready_to_finish": true
}
```

> `ready_to_finish: true` → có thể gọi `/finish` bất kỳ lúc nào.

### Reset session

```
DELETE /enroll/nfc/reset?username=alice
```

Dùng khi user muốn đăng ký lại từ đầu.

---

## NFC Reader — Gửi thẻ vào session

Module đọc thẻ (PN532) tự động gọi API này khi quét được thẻ:

```
POST /enroll/nfc/card?username=alice&card_id=A66AB0AA
```

| Param | Bắt buộc | Mô tả |
|-------|----------|-------|
| `username` | ✅ | Tên user đang trong phiên đăng ký |
| `card_id` | ✅ | UID thẻ NFC dạng hex in hoa (VD: `A66AB0AA`) |

**Response thành công (HTTP 200):**
```json
{
  "success": true,
  "username": "alice",
  "card_id": "A66AB0AA",
  "replaced": false,
  "old_card": null,
  "message": "✅ Đã lưu card_id 'A66AB0AA' cho user 'alice'."
}
```

**Response lỗi thẻ đã có chủ (HTTP 409):**
```json
{
  "success": false,
  "card_id": "A66AB0AA",
  "username": "alice",
  "current_owner": "bob",
  "reason": "card_already_registered",
  "timestamp": "2026-04-03T10:00:00",
  "message": "❌ Thẻ A66AB0AA đã được đăng ký cho user 'bob'."
}
```

---

## Socket Events — Realtime push lên App_center

Ngoài SSE, server tự động push sự kiện lên App_center qua WebSocket producer tại `ws://<SERVER>:8000/ws/producer`.

| `event` | `type` | Khi nào bắn |
|---------|--------|-------------|
| `enroll_nfc_angle` | `nfc_enroll` | Mỗi khi chụp thành công 1 góc |
| `enroll_nfc_done` | `nfc_enroll` | Sau khi `/finish` thành công |
| `enroll_nfc_card` | `card_reader` | Khi NFC reader gửi thẻ vào session |
| `enroll_card_duplicate` | `card_reader` | Thẻ đã thuộc về user khác |

**Payload `enroll_nfc_done`:**
```json
{
  "event": "enroll_nfc_done",
  "type": "nfc_enroll",
  "done": true,
  "username": "alice",
  "position": "NhanVien",
  "expiry_date": "2027-12-31T00:00:00",
  "face_ok": true,
  "card_ok": true,
  "angles_captured": ["THANG", "TRAI", "PHAI"],
  "card_id": "A66AB0AA",
  "registered_with": "khuôn mặt 3 góc + thẻ NFC (A66AB0AA)",
  "timestamp": "2026-04-03T10:15:45.000000",
  "message": "✅ Đăng ký thành công cho 'alice'..."
}
```

---

## Ví dụ tích hợp hoàn chỉnh (JavaScript)

```javascript
class NFCEnrollSession {
  constructor(baseUrl, username, options = {}) {
    this.baseUrl = baseUrl;
    this.username = username;
    this.options = options;
    this.eventSource = null;
    this.anglesCapture = [];
    this.cardId = null;
  }

  start(onEvent) {
    const params = new URLSearchParams({
      username: this.username,
      source: this.options.source ?? '0',
      ...(this.options.position && { position: this.options.position }),
      ...(this.options.expiry_date && { expiry_date: this.options.expiry_date }),
    });

    this.eventSource = new EventSource(
      `${this.baseUrl}/enroll/nfc/stream?${params}`
    );

    this.eventSource.onmessage = (e) => {
      const data = JSON.parse(e.data);
      this._handleEvent(data, onEvent);
    };

    this.eventSource.onerror = () => {
      onEvent({ event: 'connection_error', done: true });
      this.close();
    };
  }

  _handleEvent(data, onEvent) {
    switch (data.event) {
      case 'enroll_nfc_angle':
        this.anglesCapture.push(data.captured);
        break;
      case 'nfc_scanned':
        this.cardId = data.card_id;
        break;
      case 'stream_ended':
        this.close();
        break;
    }
    onEvent(data);
  }

  async finish() {
    const res = await fetch(
      `${this.baseUrl}/enroll/nfc/finish?username=${this.username}`,
      { method: 'POST' }
    );
    return res.json();
  }

  async checkStatus() {
    const res = await fetch(
      `${this.baseUrl}/enroll/nfc/session-status?username=${this.username}`
    );
    return res.json();
  }

  async reset() {
    await fetch(
      `${this.baseUrl}/enroll/nfc/reset?username=${this.username}`,
      { method: 'DELETE' }
    );
    this.close();
  }

  close() {
    this.eventSource?.close();
    this.eventSource = null;
  }
}

// Sử dụng
const session = new NFCEnrollSession('http://192.168.1.100:8000', 'alice', {
  position: 'NhanVien',
  source: '0',
  expiry_date: '2027-12-31',
});

session.start((data) => {
  console.log('[SSE]', data.event, data.message);

  if (data.event === 'face_complete' || data.event === 'nfc_scanned') {
    // Kích hoạt nút "Hoàn thành"
    document.getElementById('btn-finish').disabled = false;
  }
});

document.getElementById('btn-finish').onclick = async () => {
  const result = await session.finish();
  if (result.success) {
    alert(`Đăng ký thành công: ${result.registered_with}`);
  }
};
```

---

## Mã lỗi HTTP

| HTTP Code | Nguyên nhân | Xử lý |
|-----------|-------------|-------|
| `400` | `expiry_date` không đúng định dạng ISO 8601 | Kiểm tra lại format ngày |
| `404` | Session không tồn tại khi gọi `/finish` | Gọi `/stream` hoặc `/start` trước |
| `409` | Session đã kết thúc, hoặc thẻ NFC đã có chủ khác | Xem `reason` trong response |
| `422` | Chưa đủ điều kiện (không có mặt lẫn thẻ) | Hướng dẫn user quét thêm |

---

## Sơ đồ trạng thái session

```
[Chưa có session]
       │
       ▼ GET /enroll/nfc/stream  (hoặc POST /start)
[session_created]
       │
       ▼ camera chạy
[Đang stream]─────────────────────────────────────────┐
  │ angle_instruction                                  │
  │ → angle_mismatch (loop)                            │
  │ → enroll_nfc_angle ×3                              │ POST /enroll/nfc/card
  │       ▼                                            │
  │  [face_complete]        [nfc_scanned] ─────────────┘
  │       │                      │
  │       └──────────┬───────────┘
  │                  ▼
  │         POST /enroll/nfc/finish
  │                  │
  │                  ▼
  │            [Lưu MongoDB]
  │                  │
  └─► stream_ended ◄─┘
       │
 [Session kết thúc / xóa]
```

---

## Ghi chú quan trọng

- **Thread-safe:** Server dùng in-memory session dict. Mỗi `username` chỉ có 1 session tại một thời điểm.
- **Tự động tạo session:** `/stream` tự tạo session nếu chưa có → không bắt buộc gọi `/start` trước.
- **card_id format:** Luôn là hex in hoa, 8 ký tự (VD: `A66AB0AA`). Server tự `.upper().strip()`.
- **Frame preview:** `frame_b64` và `face_crop_b64` là JPEG encode base64. Render bằng `<img src="data:image/jpeg;base64,{value}">`.
- **Đóng SSE:** Khi nhận `done: true`, client nên đóng `EventSource` để giải phóng kết nối.
- **NFC reader song song:** Module PN532 gọi `POST /enroll/nfc/card` độc lập, không cần app điều phối.
