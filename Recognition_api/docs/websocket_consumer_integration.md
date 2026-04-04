# WebSocket Consumer — Hướng dẫn tích hợp Realtime Events

> **Hub endpoint:** `ws://<SERVER_IP>:8000/ws/consumer`  
> **Ví dụ:** `ws://192.168.21.47:8000/ws/consumer?topic=security`

Đây là tài liệu dành cho **bên thứ 3** muốn nhận sự kiện nhận diện realtime (khuôn mặt, thẻ NFC, vân tay) từ hệ thống thông qua WebSocket.

---

## Mục lục

1. [Tổng quan kiến trúc](#tổng-quan-kiến-trúc)
2. [Kết nối WebSocket](#kết-nối-websocket)
3. [Message đầu tiên — Connected](#message-đầu-tiên--connected)
4. [Cấu trúc NormalizedEvent](#cấu-trúc-normalizedevent)
5. [Topics & Types](#topics--types)
6. [Danh sách sự kiện (Events)](#danh-sách-sự-kiện-events)
   - [Xác thực khuôn mặt](#xác-thực-khuôn-mặt)
   - [Đăng ký khuôn mặt](#đăng-ký-khuôn-mặt)
   - [Xác thực thẻ NFC/RFID](#xác-thực-thẻ-nfcrfid)
   - [Đăng ký NFC + Khuôn mặt](#đăng-ký-nfc--khuôn-mặt)
   - [Xác thực vân tay](#xác-thực-vân-tay)
7. [Đổi topic sau khi kết nối](#đổi-topic-sau-khi-kết-nối)
8. [Ví dụ tích hợp theo ngôn ngữ](#ví-dụ-tích-hợp-theo-ngôn-ngữ)
   - [JavaScript / TypeScript](#javascript--typescript)
   - [Dart / Flutter](#dart--flutter)
   - [Python](#python)
9. [Xử lý lỗi & Reconnect](#xử-lý-lỗi--reconnect)
10. [Bảng tóm tắt sự kiện](#bảng-tóm-tắt-sự-kiện)

---

## Tổng quan kiến trúc

```
┌─────────────────────┐      WebSocket producer      ┌──────────────────────┐
│  Recognition API    │ ──── ws://…/ws/producer ────► │                      │
│  (face / NFC / fp)  │                               │    App_center Hub    │
└─────────────────────┘                               │  ws://<SERVER>:8000  │
                                                      │                      │
┌─────────────────────┐      WebSocket consumer       │                      │
│   App của bạn       │ ◄─── ws://…/ws/consumer ───── │                      │
│  (bên thứ 3)        │      ?topic=security          └──────────────────────┘
└─────────────────────┘
```

**App_center** là trung gian (Event Hub): nhận sự kiện từ các module (camera, NFC reader, vân tay), chuẩn hóa, rồi broadcast realtime tới tất cả consumer đang kết nối.

---

## Kết nối WebSocket

### URL

```
ws://<SERVER_IP>:8000/ws/consumer?topic=<TOPIC>
```

| Tham số | Bắt buộc | Giá trị hợp lệ | Mô tả |
|---------|----------|----------------|-------|
| `topic` | ❌ | `security` \| `*` | Topic cần theo dõi. Mặc định: nhận tất cả |

### Ví dụ URL

```
ws://192.168.21.47:8000/ws/consumer?topic=security   ← chỉ nhận topic security
ws://192.168.21.47:8000/ws/consumer?topic=*          ← nhận tất cả topics
ws://192.168.21.47:8000/ws/consumer                  ← mặc định: nhận tất cả
```

> **Lưu ý:** Hiện tại toàn bộ sự kiện (khuôn mặt, thẻ NFC, vân tay) đều thuộc topic `security`. Dùng `topic=security` là đủ.

### cURL để kiểm tra nhanh

```bash
curl 'ws://192.168.21.47:8000/ws/consumer?topic=security' \
  -H 'Upgrade: websocket' \
  -H 'Connection: Upgrade' \
  -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: mlfduH5ObzuAedpRTvXO8g=='
```

---

## Message đầu tiên — Connected

Ngay sau khi kết nối thành công, Hub tự động gửi 1 message xác nhận:

```json
{
  "type": "__system__",
  "status": "connected",
  "subscribed_topic": "security",
  "message": "Subscribed to topic: security. Receiving live events."
}
```

> Không cần gửi bất kỳ thông tin đăng nhập nào. Kết nối là **read-only** — app chỉ nhận, không cần gửi message.

---

## Cấu trúc NormalizedEvent

Mọi sự kiện realtime đều có cấu trúc thống nhất:

```json
{
  "id":        "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-04-03T10:15:30.123456+00:00",
  "source":    "face_recognition_api",
  "type":      "face_recognition",
  "topic":     "security",
  "priority":  "high",
  "payload":   { ...dữ liệu sự kiện cụ thể... },
  "metadata":  {
    "received_at": "2026-04-03T10:15:30.200000+00:00",
    "normalized":  true,
    "version":     "1.0"
  }
}
```

| Field | Kiểu | Mô tả |
|-------|------|-------|
| `id` | UUID string | ID duy nhất của event |
| `timestamp` | ISO 8601 UTC | Thời điểm event được tạo |
| `source` | string | Module nguồn (vd: `face_recognition_api`) |
| `type` | string | Loại event (xem [Topics & Types](#topics--types)) |
| `topic` | string | Topic phân loại (`security` hoặc `custom`) |
| `priority` | string | `low` \| `medium` \| `high` \| `urgent` |
| `payload` | object | Dữ liệu chi tiết của event |
| `metadata` | object | Thông tin xử lý nội bộ của Hub |

> **Dữ liệu quan trọng nằm trong `payload`.** Hãy đọc kỹ phần [Danh sách sự kiện](#danh-sách-sự-kiện-events) bên dưới.

---

## Topics & Types

### Topics

| Topic | Mô tả |
|-------|-------|
| `security` | Tất cả sự kiện nhận diện (mặt, thẻ, vân tay) |
| `custom` | Sự kiện từ nguồn không xác định |

### Event Types

| `type` | Mô tả |
|--------|-------|
| `face_recognition` | Xác thực / đăng ký khuôn mặt |
| `nfc_enroll` | Đăng ký NFC + khuôn mặt |
| `card_verify` | Xác thực thẻ NFC/RFID |
| `finger_verify` | Xác thực vân tay R305 |
| `finger_enroll` | Đăng ký vân tay R305 |
| `card_reader` | Đọc thẻ (legacy) |

---

## Danh sách sự kiện (Events)

Mỗi event được mô tả bằng `event` trong `payload` — đây là trường quan trọng nhất để app phân biệt loại sự kiện.

---

### Xác thực khuôn mặt

#### `verify_matched` — Xác thực thành công ✅

Khi camera phát hiện khuôn mặt và khớp với người dùng trong DB (score ≥ 0.45).

```json
{
  "id": "...",
  "type": "face_recognition",
  "topic": "security",
  "priority": "high",
  "source": "face_recognition_api",
  "timestamp": "2026-04-03T10:15:30.123456+00:00",
  "payload": {
    "event":         "verify_matched",
    "type":          "face_recognition",
    "phase":         "matched",
    "username":      "alice",
    "position":      "NhanVien",
    "expiry_date":   "2027-12-31T00:00:00",
    "score":         0.8432,
    "source":        "0",
    "timestamp":     "2026-04-03T10:15:30.123456",
    "matched":       true,
    "face_image_url": "http://192.168.21.47:8000/faces/face_20260403_101530.jpg",
    "message":       "✅ Xác thực thành công: alice (0.8432)"
  }
}
```

> **`face_image_url`**: URL ảnh khuôn mặt đã crop (lưu trên Hub). Có thể dùng thẳng trong `<img src="...">`.

#### `verify_unmatched` — Có người nhưng không nhận ra ❌

Khi phát hiện khuôn mặt nhưng score < 0.45 (không khớp bất kỳ user nào trong DB).

```json
{
  "type": "face_recognition",
  "topic": "security",
  "payload": {
    "event":         "verify_unmatched",
    "type":          "face_recognition",
    "phase":         "scanning",
    "username":      null,
    "matched":       false,
    "source":        "0",
    "timestamp":     "2026-04-03T10:15:31.000000",
    "face_image_url": "http://192.168.21.47:8000/faces/face_20260403_101531.jpg"
  }
}
```

> Push socket có **cooldown 10 giây** — cùng 1 trường hợp unmatched sẽ không push liên tục trong 10s.

#### `verify_no_face` — Không có mặt trong frame

> **Sự kiện này KHÔNG được push lên WebSocket** (chỉ xuất hiện qua SSE stream). App không cần xử lý.

---

### Đăng ký khuôn mặt

#### `enroll3_angle` — Chụp được 1 góc ✅

Mỗi khi hệ thống chụp thành công 1 trong 3 góc mặt (Thẳng / Trái / Phải).

```json
{
  "type": "face_recognition",
  "topic": "security",
  "payload": {
    "event":         "enroll3_angle",
    "type":          "face_recognition",
    "username":      "alice",
    "position":      "NhanVien",
    "captured":      "THANG",
    "step":          1,
    "total_steps":   3,
    "source":        "0",
    "timestamp":     "2026-04-03T10:15:30.123456",
    "face_image_url": "http://192.168.21.47:8000/faces/face_20260403_101530.jpg",
    "message":       "✅ Đã chụp góc Thẳng cho 'alice'!"
  }
}
```

| `captured` | Ý nghĩa |
|------------|---------|
| `THANG` | Mặt thẳng |
| `TRAI` | Mặt quay trái |
| `PHAI` | Mặt quay phải |

#### `enroll3_done` — Đăng ký khuôn mặt hoàn thành ✅

Sau khi lưu embedding 3 góc vào DB thành công.

```json
{
  "type": "face_recognition",
  "topic": "security",
  "payload": {
    "event":           "enroll3_done",
    "type":            "face_recognition",
    "username":        "alice",
    "position":        "NhanVien",
    "expiry_date":     "2027-12-31T00:00:00",
    "angles_captured": ["THANG", "TRAI", "PHAI"],
    "timestamp":       "2026-04-03T10:15:45.000000",
    "message":         "✅ Đăng ký khuôn mặt thành công cho 'alice'."
  }
}
```

---

### Xác thực thẻ NFC/RFID

#### `verify_card_matched` — Thẻ hợp lệ ✅

```json
{
  "type": "card_verify",
  "topic": "security",
  "payload": {
    "event":       "verify_card_matched",
    "type":        "card_verify",
    "status":      "success",
    "card_id":     "A66AB0AA",
    "username":    "alice",
    "position":    "NhanVien",
    "expiry_date": "2027-12-31T00:00:00",
    "matched":     true,
    "reason":      "ok",
    "timestamp":   "2026-04-03T10:15:30.123456",
    "message":     "✅ Xác thực thẻ thành công: alice"
  }
}
```

#### `verify_card_failed` — Thẻ không hợp lệ ❌

```json
{
  "type": "card_verify",
  "topic": "security",
  "payload": {
    "event":       "verify_card_failed",
    "type":        "card_verify",
    "status":      "failed",
    "card_id":     "A66AB0AA",
    "username":    "alice",
    "position":    "NhanVien",
    "expiry_date": "2027-01-01T00:00:00",
    "matched":     false,
    "reason":      "expired",
    "timestamp":   "2026-04-03T10:15:30.123456",
    "message":     "❌ Thẻ của alice đã hết hạn"
  }
}
```

| `reason` | Mô tả |
|----------|-------|
| `expired` | Thẻ đã hết hạn (`expiry_date` trong quá khứ) |
| `card_not_found` | UID thẻ chưa đăng ký trong hệ thống |

---

### Đăng ký NFC + Khuôn mặt

#### `enroll_nfc_angle` — Chụp được 1 góc (luồng NFC + Face)

```json
{
  "type": "nfc_enroll",
  "topic": "security",
  "payload": {
    "event":         "enroll_nfc_angle",
    "type":          "nfc_enroll",
    "username":      "alice",
    "position":      "NhanVien",
    "expiry_date":   "2027-12-31T00:00:00",
    "captured":      "THANG",
    "step":          1,
    "total_steps":   3,
    "source":        "0",
    "timestamp":     "2026-04-03T10:15:30.123456",
    "face_image_url": "http://192.168.21.47:8000/faces/face_20260403_101530.jpg",
    "message":       "✅ Đã chụp góc Thẳng cho 'alice'!"
  }
}
```

#### `enroll_nfc_done` — Đăng ký NFC + Face hoàn thành ✅

```json
{
  "type": "nfc_enroll",
  "topic": "security",
  "payload": {
    "event":           "enroll_nfc_done",
    "type":            "nfc_enroll",
    "username":        "alice",
    "position":        "NhanVien",
    "expiry_date":     "2027-12-31T00:00:00",
    "face_ok":         true,
    "card_ok":         true,
    "angles_captured": ["THANG", "TRAI", "PHAI"],
    "card_id":         "A66AB0AA",
    "registered_with": "khuôn mặt 3 góc + thẻ NFC (A66AB0AA)",
    "timestamp":       "2026-04-03T10:15:45.000000",
    "message":         "✅ Đăng ký thành công cho 'alice'..."
  }
}
```

#### `enroll_card_duplicate` — Thẻ đã thuộc về người khác ⚠️

```json
{
  "type": "card_verify",
  "topic": "security",
  "payload": {
    "event":         "enroll_card_duplicate",
    "type":          "card_verify",
    "card_id":       "A66AB0AA",
    "username":      "alice",
    "current_owner": "bob",
    "reason":        "card_already_registered",
    "timestamp":     "2026-04-03T10:00:00",
    "message":       "❌ Thẻ A66AB0AA đã được đăng ký cho user 'bob'."
  }
}
```

---

### Xác thực vân tay

#### `verify_finger_matched` — Vân tay hợp lệ ✅

```json
{
  "type": "finger_verify",
  "topic": "security",
  "payload": {
    "event":       "verify_finger_matched",
    "type":        "finger_verify",
    "status":      "success",
    "finger_id":   3,
    "username":    "alice",
    "position":    "NhanVien",
    "expiry_date": "2027-12-31T00:00:00",
    "matched":     true,
    "confidence":  150,
    "reason":      "ok",
    "timestamp":   "2026-04-03T10:15:30.123456",
    "message":     "✅ Xác thực vân tay thành công: alice"
  }
}
```

> `confidence`: Điểm tin cậy từ cảm biến R305 (0–200, càng cao càng chắc chắn).

#### `verify_finger_failed` — Vân tay không hợp lệ ❌

```json
{
  "type": "finger_verify",
  "topic": "security",
  "payload": {
    "event":      "verify_finger_failed",
    "type":       "finger_verify",
    "status":     "failed",
    "finger_id":  5,
    "username":   null,
    "position":   "",
    "matched":    false,
    "confidence": 0,
    "reason":     "finger_not_found",
    "timestamp":  "2026-04-03T10:15:30.123456",
    "message":    "❌ Vân tay slot 5 không tìm thấy trong hệ thống"
  }
}
```

| `reason` | Mô tả |
|----------|-------|
| `expired` | Tài khoản hết hạn |
| `finger_not_found` | Vân tay chưa đăng ký trong hệ thống |

---

## Đổi topic sau khi kết nối

Sau khi kết nối, app có thể gửi lệnh JSON để đổi topic mà không cần ngắt kết nối:

```json
{
  "action": "change_topic",
  "topic": "*"
}
```

Server phản hồi:

```json
{
  "type": "__system__",
  "status": "topic_changed",
  "subscribed_topic": "*"
}
```

---

## Ví dụ tích hợp theo ngôn ngữ

### JavaScript / TypeScript

```javascript
class SecurityEventConsumer {
  constructor(serverUrl, onEvent) {
    this.serverUrl = serverUrl;
    this.onEvent = onEvent;
    this.ws = null;
    this._reconnectDelay = 1000;
  }

  connect(topic = 'security') {
    const url = `${this.serverUrl}/ws/consumer?topic=${topic}`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log('[WS] Connected to Hub');
      this._reconnectDelay = 1000;
    };

    this.ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);

      // Bỏ qua system message
      if (msg.type === '__system__') return;

      // Đọc event từ payload
      const event = msg.payload?.event;
      this.onEvent(event, msg.payload, msg);
    };

    this.ws.onclose = () => {
      console.warn('[WS] Disconnected. Reconnecting in', this._reconnectDelay, 'ms');
      setTimeout(() => {
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, 30000);
        this.connect(topic);
      }, this._reconnectDelay);
    };

    this.ws.onerror = (err) => {
      console.error('[WS] Error:', err);
    };
  }

  disconnect() {
    this.ws?.close();
  }
}

// Sử dụng
const consumer = new SecurityEventConsumer('ws://192.168.21.47:8000', (event, payload, raw) => {
  switch (event) {
    case 'verify_matched':
      console.log(`✅ Xác thực: ${payload.username} (score: ${payload.score})`);
      showAccessGranted(payload.username, payload.face_image_url);
      break;

    case 'verify_unmatched':
      console.log('❌ Khuôn mặt không nhận ra');
      showAccessDenied();
      break;

    case 'verify_card_matched':
      console.log(`💳 Thẻ hợp lệ: ${payload.username}`);
      showAccessGranted(payload.username);
      break;

    case 'verify_card_failed':
      console.log(`💳 Thẻ không hợp lệ: ${payload.reason}`);
      showAccessDenied(payload.reason);
      break;

    case 'verify_finger_matched':
      console.log(`🖐 Vân tay hợp lệ: ${payload.username}`);
      showAccessGranted(payload.username);
      break;

    case 'enroll3_done':
    case 'enroll_nfc_done':
      console.log(`🎉 Đăng ký xong: ${payload.username}`);
      break;

    default:
      console.log('[WS] Unhandled event:', event, payload);
  }
});

consumer.connect('security');
```

---

### Dart / Flutter

```dart
import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';

class SecurityEventConsumer {
  final String serverIp;
  WebSocketChannel? _channel;

  SecurityEventConsumer({required this.serverIp});

  void connect({String topic = 'security', required Function(String, Map) onEvent}) {
    final uri = Uri.parse('ws://$serverIp:8000/ws/consumer?topic=$topic');
    _channel = WebSocketChannel.connect(uri);

    _channel!.stream.listen(
      (raw) {
        final msg = jsonDecode(raw as String) as Map<String, dynamic>;

        // Bỏ qua system message
        if (msg['type'] == '__system__') return;

        final payload = msg['payload'] as Map<String, dynamic>? ?? {};
        final event = payload['event'] as String? ?? '';
        onEvent(event, payload);
      },
      onDone: () {
        Future.delayed(const Duration(seconds: 3), () {
          connect(topic: topic, onEvent: onEvent);
        });
      },
      onError: (err) {
        debugPrint('[WS] Error: $err');
      },
    );
  }

  void disconnect() => _channel?.sink.close();
}

// Sử dụng
final consumer = SecurityEventConsumer(serverIp: '192.168.21.47');
consumer.connect(
  topic: 'security',
  onEvent: (event, payload) {
    switch (event) {
      case 'verify_matched':
        final username = payload['username'];
        final score = payload['score'];
        final imageUrl = payload['face_image_url'];
        print('✅ $username ($score) — $imageUrl');
        break;

      case 'verify_card_matched':
        print('💳 ${payload['username']}');
        break;

      case 'verify_finger_matched':
        print('🖐 ${payload['username']} confidence=${payload['confidence']}');
        break;

      default:
        print('[WS] $event: $payload');
    }
  },
);
```

---

### Python

```python
import asyncio
import json
import websockets

SERVER = "ws://192.168.21.47:8000"

async def consume_events():
    url = f"{SERVER}/ws/consumer?topic=security"
    reconnect_delay = 1

    while True:
        try:
            async with websockets.connect(url) as ws:
                print(f"[WS] Connected to {url}")
                reconnect_delay = 1

                async for raw in ws:
                    msg = json.loads(raw)

                    # Bỏ qua system message
                    if msg.get("type") == "__system__":
                        continue

                    payload = msg.get("payload", {})
                    event = payload.get("event", "")

                    if event == "verify_matched":
                        print(f"✅ Xác thực: {payload['username']} score={payload['score']}")
                    elif event == "verify_unmatched":
                        print("❌ Khuôn mặt không nhận ra")
                    elif event == "verify_card_matched":
                        print(f"💳 Thẻ hợp lệ: {payload['username']}")
                    elif event == "verify_card_failed":
                        print(f"💳 Thẻ thất bại: {payload['reason']}")
                    elif event == "verify_finger_matched":
                        print(f"🖐 Vân tay: {payload['username']} conf={payload['confidence']}")
                    elif event in ("enroll3_done", "enroll_nfc_done"):
                        print(f"🎉 Đăng ký xong: {payload['username']}")
                    else:
                        print(f"[WS] {event}: {payload}")

        except Exception as e:
            print(f"[WS] Disconnected ({e}). Retry in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 30)

asyncio.run(consume_events())
```

---

## Xử lý lỗi & Reconnect

| Tình huống | Xử lý đề xuất |
|------------|---------------|
| Connection dropped | Auto-reconnect với exponential backoff (1s → 2s → 4s … max 30s) |
| JSON parse error | Log lỗi và bỏ qua message đó |
| `type: "__system__"` | Message hệ thống — không cần xử lý nghiệp vụ |
| Hub restart | Connection sẽ bị đóng → app sẽ reconnect → nhận tiếp events mới |
| Payload thiếu field | Dùng `?.` hoặc `.get()` với giá trị mặc định — không crash |

**Quan trọng:** Hub không yêu cầu authentication. Tuy nhiên nên giới hạn IP kết nối ở tầng network (firewall/VPN) nếu triển khai môi trường production.

---

## Bảng tóm tắt sự kiện

| `payload.event` | `type` | Khi nào | Có `face_image_url`? |
|-----------------|--------|---------|----------------------|
| `verify_matched` | `face_recognition` | Camera nhận ra người dùng | ✅ |
| `verify_unmatched` | `face_recognition` | Có mặt nhưng không khớp | ✅ |
| `enroll3_angle` | `face_recognition` | Chụp 1 góc đăng ký | ✅ |
| `enroll3_done` | `face_recognition` | Hoàn thành đăng ký khuôn mặt | ❌ |
| `verify_card_matched` | `card_verify` | Thẻ NFC/RFID hợp lệ | ❌ |
| `verify_card_failed` | `card_verify` | Thẻ hết hạn hoặc không tìm thấy | ❌ |
| `enroll_nfc_angle` | `nfc_enroll` | Chụp 1 góc (luồng NFC+Face) | ✅ |
| `enroll_nfc_done` | `nfc_enroll` | Hoàn thành đăng ký NFC+Face | ❌ |
| `enroll_card_duplicate` | `card_verify` | Thẻ đã thuộc user khác khi đăng ký | ❌ |
| `verify_finger_matched` | `finger_verify` | Vân tay hợp lệ | ❌ |
| `verify_finger_failed` | `finger_verify` | Vân tay hết hạn hoặc chưa đăng ký | ❌ |

> **`face_image_url`**: URL ảnh do Hub lưu, thay thế cho `face_crop_b64` (base64). Dùng trực tiếp trong `<img src>` hoặc `Image.network()` (Flutter).

---

*Tài liệu này mô tả giao diện Consumer WebSocket của App_center Hub — phiên bản 1.0.*
