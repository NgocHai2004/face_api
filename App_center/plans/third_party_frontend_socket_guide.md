# Hướng dẫn: Frontend bên thứ 3 nhận sự kiện từ App_center

> Tài liệu này dành cho **bất kỳ frontend nào** (React, Vue, Angular, plain JS…)  
> muốn **nhận realtime events** từ **App_center Event Hub** qua WebSocket.

---

## Kết nối WebSocket Consumer

```
ws://localhost:8000/ws/consumer?topic=*
```

| `topic` param | Ý nghĩa |
|--------------|---------|
| `*` | Nhận **tất cả** sự kiện (khuyến nghị) |
| `security` | Chỉ nhận sự kiện bảo mật |

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/consumer?topic=*')

ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data)
  if (msg.type === '__system__') return  // bỏ qua system message của Hub
  handleEvent(msg)
}
```

---

## Cấu trúc chung mọi sự kiện nhận được (`NormalizedEvent`)

```jsonc
{
  "id":        "uuid-v4",               // ID duy nhất
  "timestamp": "2026-04-03T10:00:00Z",  // Thời điểm event phát sinh
  "source":    "face_recognition_api",  // Nguồn gửi
  "type":      "nfc_enroll",            // Loại sự kiện (xem bảng bên dưới)
  "topic":     "security",
  "priority":  "high",
  "payload":   { /* chi tiết bên dưới */ },
  "metadata":  { "received_at": "...", "normalized": true, "version": "1.0" }
}
```

**`payload.event`** = sub-type, dùng để phân biệt chi tiết trong cùng một `type`.

---

## Bảng tổng hợp tất cả sự kiện App_center phát ra

| `NormalizedEvent.type` | `payload.event` | `payload.type` | Khi nào phát |
|------------------------|----------------|----------------|-------------|
| `nfc_enroll` | `enroll_nfc_angle` | `face_recognition` | Chụp xong 1 góc mặt trong luồng đăng ký NFC |
| `nfc_enroll` | `enroll_nfc_done` | `nfc_enroll` | Hoàn tất đăng ký NFC (lưu DB xong) |
| `card_reader` | `enroll_nfc_card` | `card_reader` | NFC reader quét được thẻ hợp lệ |
| `card_reader` | `enroll_card_duplicate` | `card_reader` | NFC reader quét thẻ đã thuộc về user khác |
| `face_recognition` | `enroll3_angle` | `face_recognition` | Chụp xong 1 góc mặt trong luồng đăng ký chỉ khuôn mặt |
| `face_recognition` | `enroll3_done` | `face_recognition` | Hoàn tất đăng ký 3 góc khuôn mặt |
| `face_recognition` | `verify_matched` | _(không có)_ | Xác thực khuôn mặt thành công |
| `face_recognition` | `verify_unmatched` | _(không có)_ | Xác thực khuôn mặt thất bại |

> ⚠️ **Quan trọng — cách nhận diện sự kiện đúng:**
> - Dùng **`payload.event`** để phân biệt loại sự kiện cụ thể, KHÔNG dùng `payload.type`
> - `payload.type` là field nội bộ producer ghi vào trước khi gửi Hub, có thể gây nhầm lẫn
> - `NormalizedEvent.type` (outer) mới là type đã được Hub chuẩn hóa

> **Phân biệt hai luồng đăng ký khuôn mặt:**
> - **`enroll_nfc_angle`** (`NormalizedEvent.type=nfc_enroll`) — luồng `/enroll/nfc/stream` (NFC + khuôn mặt đồng thời)
> - **`enroll3_angle`** (`NormalizedEvent.type=face_recognition`) — luồng `/enroll3` (chỉ khuôn mặt)

---

## Chi tiết luồng `nfc_enroll` (Đăng ký NFC + Khuôn mặt)

### Luồng sự kiện

```
[Bắt đầu session]
       │
       ▼
  enroll_nfc_angle  ← Góc 1 (THANG) chụp thành công
       │
       ▼
  enroll_nfc_angle  ← Góc 2 (TRAI) chụp thành công
       │
       ▼
  enroll_nfc_angle  ← Góc 3 (PHAI) chụp thành công
       │
  (song song bất kỳ lúc nào)
  enroll_nfc_card   ← NFC reader quét thẻ
       │
       ▼
  enroll_nfc_done   ← Lưu DB xong, kết thúc
```

> **Song song:** `enroll_nfc_card` có thể đến **bất kỳ lúc nào** — trước, trong, hoặc sau khi chụp góc mặt. Điều kiện finish: **đủ 3 góc mặt HOẶC có thẻ** (hoặc cả hai).

---

### Sự kiện 1 — `enroll_nfc_angle` (chụp 1 góc thành công)

**`NormalizedEvent.type`** = `nfc_enroll` · **`topic`** = `security`

> ⚠️ **Lưu ý:** Trong `payload` còn có field `"type": "face_recognition"` — đây là field gốc từ producer gửi lên, **không phải** `NormalizedEvent.type`. Frontend nên dùng `payload.event === "enroll_nfc_angle"` để nhận diện sự kiện này, không dùng `payload.type`.

```json
{
  "id": "uuid...",
  "type": "nfc_enroll",
  "topic": "security",
  "priority": "high",
  "payload": {
    "event":          "enroll_nfc_angle",
    "type":           "face_recognition",
    "step":           2,
    "total_steps":    3,
    "required_angle": "TRAI",
    "captured":       "TRAI",
    "username":       "Nguyễn Ngọc Hải",
    "position":       "bảo vệ",
    "expiry_date":    "2026-04-09T00:00:00",
    "source":         "http://192.168.21.47:8090/stream",
    "timestamp":      "2026-04-03T04:03:24.950307",
    "done":           false,
    "message":        "✅ Đã chụp góc Trái cho 'Nguyễn Ngọc Hải'!",
    "face_image_url": "http://localhost:8000/faces/face_abc123.jpg"
  }
}
```

| Field | Giá trị có thể có | Mô tả |
|-------|------------------|-------|
| `step` | `1`, `2`, `3` | Thứ tự góc đang chụp |
| `total_steps` | `3` | Luôn là 3 |
| `required_angle` / `captured` | `THANG`, `TRAI`, `PHAI` | Góc vừa chụp |
| `done` | `false` | Chưa hoàn tất (vẫn còn góc khác) |
| `face_image_url` | URL ảnh | Ảnh khuôn mặt đã lưu tại Hub |

---

### Sự kiện 2 — `enroll_nfc_card` (NFC reader quét thẻ)

**`type`** = `card_reader` · **`topic`** = `security`

```json
{
  "id": "uuid...",
  "type": "card_reader",
  "topic": "security",
  "priority": "high",
  "payload": {
    "event":     "enroll_nfc_card",
    "card_id":   "A66AB0AA",
    "username":  "nguyen_van_a",
    "position":  "Nhan vien",
    "replaced":  false,
    "old_card":  null,
    "timestamp": "2026-04-03T10:00:02.000000",
    "message":   "💳 Thẻ A66AB0AA đã được quét cho 'nguyen_van_a'."
  }
}
```

| Field | Mô tả |
|-------|-------|
| `card_id` | UID thẻ NFC dạng hex in hoa (vd: `A66AB0AA`) |
| `replaced` | `true` nếu user đã có thẻ khác trước đó, nay bị thay |
| `old_card` | Card cũ nếu `replaced=true`, ngược lại `null` |

---

### Sự kiện 3 — `enroll_card_duplicate` (thẻ đã thuộc user khác)

**`type`** = `card_reader` · **`topic`** = `security`

```json
{
  "id": "uuid...",
  "type": "card_reader",
  "topic": "security",
  "priority": "high",
  "payload": {
    "event":                   "enroll_card_duplicate",
    "card_id":                 "A66AB0AA",
    "requested_by":            "nguyen_van_b",
    "current_owner":           "nguyen_van_a",
    "current_owner_position":  "Nhan vien",
    "current_owner_expiry":    "2027-12-31T00:00:00",
    "matched":                 false,
    "reason":                  "card_already_registered",
    "timestamp":               "2026-04-03T10:00:05.000000",
    "message":                 "❌ Thẻ A66AB0AA đã được đăng ký cho 'nguyen_van_a' (Nhan vien)"
  }
}
```

> **Frontend nên hiển thị cảnh báo** khi nhận event này: thẻ bị từ chối, người dùng cần dùng thẻ khác.

---

### Sự kiện 4 — `enroll_nfc_done` (hoàn tất đăng ký)

**`type`** = `nfc_enroll` · **`topic`** = `security`

```json
{
  "id": "uuid...",
  "type": "nfc_enroll",
  "topic": "security",
  "priority": "high",
  "payload": {
    "event":           "enroll_nfc_done",
    "done":            true,
    "username":        "nguyen_van_a",
    "position":        "Nhan vien",
    "expiry_date":     "2027-12-31T00:00:00",
    "face_ok":         true,
    "card_ok":         true,
    "angles_captured": ["THANG", "TRAI", "PHAI"],
    "card_id":         "A66AB0AA",
    "registered_with": "khuôn mặt 3 góc + thẻ NFC (A66AB0AA)",
    "timestamp":       "2026-04-03T10:00:10.000000",
    "message":         "✅ Đăng ký thành công cho 'nguyen_van_a' với: khuôn mặt 3 góc + thẻ NFC (A66AB0AA)."
  }
}
```

| Field | Giá trị | Mô tả |
|-------|---------|-------|
| `done` | `true` | Luôn `true` ở event này |
| `face_ok` | `true/false` | Đã đăng ký khuôn mặt 3 góc hay không |
| `card_ok` | `true/false` | Đã đăng ký thẻ NFC hay không |
| `angles_captured` | `[]` hoặc `["THANG","TRAI","PHAI"]` | Các góc đã chụp (rỗng nếu không có mặt) |
| `card_id` | string hoặc `null` | UID thẻ NFC (null nếu không có thẻ) |
| `registered_with` | string | Mô tả phương thức đã đăng ký |

**Các tổ hợp `registered_with`:**
- `"khuôn mặt 3 góc"` — chỉ có mặt
- `"thẻ NFC (A66AB0AA)"` — chỉ có thẻ
- `"khuôn mặt 3 góc + thẻ NFC (A66AB0AA)"` — cả hai

---

## Các sự kiện khác (tham khảo)

### `enroll3_angle` — Chụp 1 góc khi đăng ký chỉ khuôn mặt

**`type`** = `face_recognition`

```json
{
  "type": "face_recognition",
  "payload": {
    "event":          "enroll3_angle",
    "step":           1,
    "total_steps":    3,
    "required_angle": "THANG",
    "captured":       "THANG",
    "username":       "nguyen_van_a",
    "position":       "Nhan vien",
    "expiry_date":    "2027-12-31T00:00:00",
    "source":         "0",
    "timestamp":      "2026-04-03T10:00:00.123456",
    "done":           false,
    "message":        "✅ Đã chụp góc THANG cho 'nguyen_van_a'!",
    "face_image_url": "http://localhost:8000/faces/face_xxx.jpg"
  }
}
```

### `enroll3_done` — Hoàn tất đăng ký chỉ khuôn mặt

**`type`** = `face_recognition`

```json
{
  "type": "face_recognition",
  "payload": {
    "event":           "enroll3_done",
    "done":            true,
    "username":        "nguyen_van_a",
    "position":        "Nhan vien",
    "expiry_date":     "2027-12-31T00:00:00",
    "angles_captured": ["THANG", "TRAI", "PHAI"],
    "source":          "0",
    "timestamp":       "2026-04-03T10:00:05.000000",
    "message":         "✅ Đăng ký thành công 3 góc cho 'nguyen_van_a'!"
  }
}
```

### `verify_matched` — Xác thực khuôn mặt thành công

**`type`** = `face_recognition`

```json
{
  "type": "face_recognition",
  "payload": {
    "event":          "verify_matched",
    "matched":        true,
    "username":       "nguyen_van_a",
    "position":       "Nhan vien",
    "score":          0.8542,
    "message":        "✅ Xác thực thành công: nguyen_van_a (0.8542)",
    "face_image_url": "http://localhost:8000/faces/face_xyz.jpg"
  }
}
```

### `verify_unmatched` — Xác thực khuôn mặt thất bại

**`type`** = `face_recognition`

```json
{
  "type": "face_recognition",
  "payload": {
    "event":            "verify_unmatched",
    "matched":          false,
    "username":         null,
    "nearest":          "nguyen_van_a",
    "nearest_position": "Nhan vien",
    "score":            0.3210,
    "message":          "❌ Không nhận diện được — gần nhất: nguyen_van_a (score=0.321)",
    "face_image_url":   "http://localhost:8000/faces/face_xyz.jpg"
  }
}
```

---

## Ví dụ xử lý đầy đủ (JavaScript)

```javascript
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data)
  if (msg.type === '__system__') return

  const { event } = msg.payload

  switch (event) {

    // ── NFC Enroll ─────────────────────────────────────────────────
    case 'enroll_nfc_angle': {
      const { step, total_steps, captured, username, face_image_url } = msg.payload
      updateAngleProgress(step, total_steps, captured, face_image_url)
      // vd: hiển thị "✅ Góc 1/3: THANG — nguyen_van_a"
      break
    }

    case 'enroll_nfc_card': {
      const { card_id, username, replaced, old_card } = msg.payload
      showCardScanned(card_id, username, replaced ? `thay thẻ ${old_card}` : '')
      // vd: hiển thị badge "💳 Đã quét thẻ A66AB0AA"
      break
    }

    case 'enroll_card_duplicate': {
      const { card_id, current_owner, requested_by } = msg.payload
      showError(`❌ Thẻ ${card_id} đã thuộc về ${current_owner}, không thể dùng cho ${requested_by}`)
      break
    }

    case 'enroll_nfc_done': {
      const { username, face_ok, card_ok, card_id, angles_captured, registered_with } = msg.payload
      showSuccess(`🎉 ${username} đăng ký xong: ${registered_with}`)
      // Refresh danh sách user hoặc chuyển màn hình
      break
    }

    // ── Face Enroll (không kèm NFC) ────────────────────────────────
    case 'enroll3_angle': {
      const { step, total_steps, captured, face_image_url } = msg.payload
      updateAngleProgress(step, total_steps, captured, face_image_url)
      break
    }

    case 'enroll3_done': {
      const { username, angles_captured } = msg.payload
      showSuccess(`🎉 ${username} đã đăng ký xong ${angles_captured.length} góc mặt`)
      break
    }

    // ── Verify ─────────────────────────────────────────────────────
    case 'verify_matched': {
      const { username, score, face_image_url } = msg.payload
      showVerifyResult(true, username, score, face_image_url)
      break
    }

    case 'verify_unmatched': {
      const { nearest, score, face_image_url } = msg.payload
      showVerifyResult(false, nearest, score, face_image_url)
      break
    }
  }
}
```

---

## Auto-reconnect

```javascript
const DELAYS = [1000, 2000, 5000, 10000]
let retries = 0

function connect() {
  const ws = new WebSocket('ws://localhost:8000/ws/consumer?topic=*')

  ws.onopen    = () => { retries = 0 }
  ws.onmessage = (ev) => { /* xử lý như trên */ }
  ws.onclose   = () => {
    const delay = DELAYS[Math.min(retries++, DELAYS.length - 1)]
    setTimeout(connect, delay)
  }
}

connect()
```

---

## Lưu ý quan trọng

| # | Lưu ý |
|---|-------|
| 1 | **Lọc `type === '__system__'`** — đây là thông báo nội bộ Hub, phải bỏ qua |
| 2 | **`face_image_url`** — Hub đã xử lý base64 thành URL ảnh, frontend dùng trực tiếp `<img src={face_image_url}>` |
| 3 | **`enroll_nfc_card` và `enroll_nfc_angle` độc lập** — thẻ có thể quét trước, trong, hoặc sau khi chụp góc |
| 4 | **`enroll_card_duplicate`** — frontend nên hiển thị cảnh báo nổi bật, yêu cầu người dùng dùng thẻ khác |
| 5 | **`enroll_nfc_done.face_ok` và `card_ok`** — kiểm tra 2 field này để biết user được đăng ký theo phương thức nào |
| 6 | **`done: false`** trên các `enroll_nfc_angle` — session vẫn còn, chưa lưu DB; chỉ `enroll_nfc_done` mới là kết thúc thật sự |
