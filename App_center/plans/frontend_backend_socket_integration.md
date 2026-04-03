# Frontend ↔ Backend — Tích hợp Socket & API (App_center)

> **Cập nhật:** 2026-04-03  
> **Stack:** FastAPI (backend) · React + Vite (frontend) · WebSocket + REST

---

## 1. Tổng quan kiến trúc

```
                     ┌──────────────────────────────────────┐
                     │          App_center Backend           │
                     │          (FastAPI :8000)              │
  Producers          │                                       │          Consumers
  ─────────          │  ┌─────────────┐  ┌───────────────┐  │          ─────────
  Recognition_api ──►│  │ /ws/producer│  │ /ws/consumer  │──┼──► React Frontend
  webcam_server   ──►│  │ /events/    │  │   ?topic=*    │  │          :5173
  IoT / Script    ──►│  │  ingest     │  └───────────────┘  │
                     │  └─────────────┘                     │
                     │        │  EventBus (asyncio.Queue)    │
                     │        └──────────────────────────────┤
                     │  REST: /events/recent /health …       │
                     └──────────────────────────────────────┘
```

**Luồng dữ liệu:**
1. **Producer** gửi raw event → `/ws/producer` hoặc `POST /events/ingest`
2. **Backend** normalize → đưa vào `EventBus` (asyncio.Queue)
3. **Dispatcher** broadcast `NormalizedEvent` → tất cả WS consumers đang subscribe
4. **Frontend** (`useEventHub.ts`) nhận qua `/ws/consumer?topic=*` → cập nhật React state

---

## 2. Schema sự kiện chuẩn hóa

### 2.1 `NormalizedEvent` — Object consumer nhận được

| Field | Type | Mô tả |
|-------|------|-------|
| `id` | `string` (UUID v4) | ID duy nhất của event |
| `timestamp` | `string` (ISO 8601 UTC) | Thời điểm event được tạo |
| `source` | `string` | Tên/ID thiết bị gửi (vd: `camera_01`) |
| `type` | `EventType` | Loại sự kiện (xem §2.3) |
| `topic` | `EventTopic` | Topic được suy ra từ `type` |
| `priority` | `EventPriority` | Độ ưu tiên |
| `payload` | `object` | Dữ liệu thô từ thiết bị (đã xử lý base64) |
| `metadata.received_at` | `string` (ISO 8601) | Thời điểm Hub nhận event |
| `metadata.normalized` | `boolean` | Luôn `true` |
| `metadata.version` | `string` | `"1.0"` |

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-04-03T10:00:00.000Z",
  "source": "face_recognition_api",
  "type": "face_recognition",
  "topic": "security",
  "priority": "high",
  "payload": {
    "event": "verify_matched",
    "matched": true,
    "username": "nguyen_van_a",
    "score": 0.8542,
    "face_image_url": "http://hub:8000/faces/face_xxx.jpg"
  },
  "metadata": {
    "received_at": "2026-04-03T10:00:00.001Z",
    "normalized": true,
    "version": "1.0"
  }
}
```

### 2.2 `RawEvent` — Object producer gửi lên

```json
{
  "source": "camera_01",
  "type": "face_recognition",
  "priority": "high",
  "payload": { ... }
}
```

> **Flat format cũng hợp lệ** (không cần `payload` wrapper):
> ```json
> {
>   "event": "verify3_angle",
>   "source": "0",
>   "matched": true,
>   "username": "long",
>   "score": 0.843,
>   "face_crop_b64": "<base64>"
> }
> ```
> Backend tự gom các field thừa vào `payload`, map `event` → `type`.

### 2.3 Event Types & Topic mapping tự động

| `type` | `topic` tự động | Icon FE |
|--------|----------------|---------|
| `face_recognition` | `security` | 👤 |
| `nfc_enroll` | `security` | — |
| `card_verify` | `security` | — |
| `fingerprint` | `security` | 🖐 |
| `card_reader` | `security` | 💳 |
| `custom` | `custom` | 📋 |

### 2.4 Priority values

| Value | Màu FE |
|-------|--------|
| `low` | `text-green-400` |
| `medium` | `text-yellow-400` |
| `high` | `text-orange-400` |
| `urgent` | `text-red-400` |

---

## 3. WebSocket Endpoints

### 3.1 Producer WebSocket — `ws://host:8000/ws/producer`

**Kết nối:**
```
ws://localhost:8000/ws/producer
ws://localhost:8000/ws/producer?source=camera_01   # override source
```

**Frontend gửi** (JSON string):
```json
{
  "source": "camera_entrance_01",
  "type": "face_recognition",
  "priority": "high",
  "payload": {
    "person_id": "EMP001",
    "confidence": 0.97
  }
}
```

**Backend ACK trả về** (sau mỗi message):
```json
{
  "status": "ok",
  "event_id": "uuid...",
  "queued": true,
  "topic": "security",
  "type": "face_recognition"
}
```

**Lỗi trả về:**
```json
{ "status": "error", "message": "Missing required field: 'type' or 'event'" }
{ "status": "error", "message": "Invalid JSON format" }
{ "status": "error", "message": "Normalization failed: ..." }
```

**Triển khai trong `ProducerPanel.tsx`:**
```typescript
// App_center/frontend/src/components/ProducerPanel.tsx
const ws = new WebSocket(`${WS_BASE_URL}/ws/producer`)

ws.onopen  = () => setWsStatus('connected')
ws.onerror = () => setWsStatus('disconnected')
ws.onclose = () => setWsStatus('disconnected')

ws.onmessage = (ev) => {
  const data = JSON.parse(ev.data)
  // data.status === 'ok' → addLog ACK
  // data.status === 'error' → addLog error
}

// Gửi event:
ws.send(JSON.stringify({ source, type, priority, payload }))
```

---

### 3.2 Consumer WebSocket — `ws://host:8000/ws/consumer`

**Kết nối:**
```
ws://localhost:8000/ws/consumer          # mặc định: nhận tất cả (topic=*)
ws://localhost:8000/ws/consumer?topic=*  # nhận tất cả topics
ws://localhost:8000/ws/consumer?topic=security  # chỉ nhận topic security
```

**Ngay khi kết nối**, backend gửi system message:
```json
{
  "type": "__system__",
  "status": "connected",
  "subscribed_topic": "*",
  "message": "Subscribed to topic: *. Receiving live events."
}
```

**Mỗi khi có event mới**, backend push `NormalizedEvent` (xem §2.1).

**Client có thể gửi lệnh đổi topic:**
```json
{ "action": "change_topic", "topic": "security" }
```

Backend phản hồi:
```json
{
  "type": "__system__",
  "status": "topic_changed",
  "subscribed_topic": "security"
}
```

**Triển khai trong `useEventHub.ts`:**
```typescript
// App_center/frontend/src/hooks/useEventHub.ts
const url = `${WS_BASE_URL}/ws/consumer?topic=${encodeURIComponent(topic)}`
const ws  = new WebSocket(url)

ws.onopen = () => {
  reconnectCount.current = 0
  setStatus('connected')   // ConnectionStatus: 'connecting' | 'connected' | 'disconnected' | 'error'
}

ws.onmessage = (ev) => {
  const msg: WsMessage = JSON.parse(ev.data)

  // Lọc system messages
  if (msg.type === '__system__') {
    if (msg.subscribed_topic) setSubscribedTopic(msg.subscribed_topic)
    return
  }

  // NormalizedEvent → prepend vào state
  const event = msg as NormalizedEvent
  setEvents(prev => [event, ...prev].slice(0, MAX_EVENTS /* 200 */))
}

ws.onerror = () => setStatus('error')

ws.onclose = () => {
  setStatus('disconnected')
  scheduleReconnect()  // backoff: 1s → 2s → 5s → 10s
}
```

**Auto-reconnect backoff:**

| Lần thứ | Delay |
|---------|-------|
| 1 | 1 000 ms |
| 2 | 2 000 ms |
| 3 | 5 000 ms |
| 4+ | 10 000 ms |

---

## 4. REST API Endpoints

### 4.1 `POST /events/ingest` — Producer gửi qua HTTP

**Request body** (`RawEvent`):
```json
{
  "source": "face_recognition_api",
  "type": "face_recognition",
  "priority": "high",
  "payload": {
    "event": "verify_matched",
    "matched": true,
    "username": "nguyen_van_a",
    "score": 0.8542,
    "face_crop_b64": "<base64_jpeg>"
  }
}
```

> **Lưu ý:** Nếu `payload` chứa `face_crop_b64` hoặc `face_crop_base64` → Hub tự lưu thành file `/mnt/faces/`, thay bằng `face_image_url` + `face_image_path`.

**Response** (`IngestResponse`):
```json
{
  "success": true,
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Event queued successfully. topic=security"
}
```

**Triển khai trong `ProducerPanel.tsx`:**
```typescript
const res = await fetch(`${API_BASE_URL}/events/ingest`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ source, type, priority, payload }),
})
const data = await res.json()
// data.success === true  → OK
// data.success === false → error (queue full)
```

---

### 4.2 `GET /events/recent` — Query lịch sử

```
GET /events/recent?topic=*&limit=50&from_db=false
```

| Query param | Default | Mô tả |
|-------------|---------|-------|
| `topic` | `*` | Topic cần query, `*` = tất cả |
| `limit` | `50` | Số events tối đa (1–500) |
| `from_db` | `false` | `true` = MongoDB (lịch sử lâu dài), `false` = in-memory |

**Response** (`RecentEventsResponse`):
```json
{
  "topic": "*",
  "count": 50,
  "events": [ /* NormalizedEvent[] */ ]
}
```

**Triển khai trong `useEventHub.ts`** (load history khi mount / đổi topic):
```typescript
const res = await fetch(
  `${API_BASE_URL}/events/recent?topic=${t}&limit=200&from_db=true`
)
const data = await res.json()
// data.events → NormalizedEvent[]
// Merge với realtime events, deduplicate by id
setEvents(prev => {
  const existingIds = new Set(prev.map(e => e.id))
  const merged = [...prev, ...data.events.filter(e => !existingIds.has(e.id))]
  return merged.slice(0, maxEvents)
})
```

---

### 4.3 `GET /events/topics` — Danh sách topics active

```
GET /events/topics
```

**Response** (`TopicsResponse`):
```json
{
  "topics": ["security", "custom"],
  "total_consumers": 3
}
```

---

### 4.4 `GET /health` — Health check

```
GET /health
```

**Response:**
```json
{
  "status": "ok",
  "queue_size": 0,
  "active_topics": ["security"],
  "total_consumers": 2,
  "mongodb": {
    "status": "ok",
    "total_events": 1024
  }
}
```

---

### 4.5 `GET /events/types` — Event types hỗ trợ

```
GET /events/types
```

**Response:**
```json
{
  "event_types": ["face_recognition", "nfc_enroll", "card_verify", "fingerprint", "card_reader", "custom"],
  "topics": ["security", "custom"],
  "priorities": ["low", "medium", "high", "urgent"],
  "type_to_topic_mapping": {
    "face_recognition": "security",
    "fingerprint": "security",
    "card_reader": "security",
    "custom": "custom"
  }
}
```

---

### 4.6 `DELETE /events` — Xóa toàn bộ events

```
DELETE /events
```

**Response:**
```json
{
  "message": "Đã xóa tất cả events",
  "deleted_mongo": 46,
  "cleared_memory": true
}
```

---

## 5. Payload chi tiết theo Event Type

### 5.1 `face_recognition` — Nhận diện khuôn mặt

#### `verify_matched` — Xác thực thành công ✅
```json
{
  "event": "verify_matched",
  "phase": "matched",
  "matched": true,
  "username": "nguyen_van_a",
  "position": "Nhan vien",
  "score": 0.8542,
  "source": "http://192.168.x.x:8090/stream",
  "timestamp": "2026-04-02T10:00:00",
  "message": "✅ Xác thực thành công: nguyen_van_a (0.8542)",
  "face_image_url": "http://hub:8000/faces/face_xxx.jpg",
  "face_image_path": "/mnt/faces/face_xxx.jpg"
}
```

#### `verify_unmatched` — Có mặt nhưng không khớp ❌
```json
{
  "event": "verify_unmatched",
  "phase": "scanning",
  "matched": false,
  "username": null,
  "nearest": "nguyen_van_a",
  "nearest_position": "Nhan vien",
  "score": 0.3210,
  "source": "http://192.168.x.x:8090/stream",
  "timestamp": "2026-04-02T10:00:01",
  "message": "❌ Không nhận diện được — gần nhất: nguyen_van_a (score=0.321)",
  "face_image_url": "http://hub:8000/faces/face_xxx.jpg"
}
```

#### `enroll3_angle` — Chụp 1 góc đăng ký 📸
```json
{
  "event": "enroll3_angle",
  "step": 1,
  "total_steps": 3,
  "required_angle": "THANG",
  "captured": "THANG",
  "username": "nguyen_van_a",
  "source": "0",
  "timestamp": "2026-04-02T10:00:00",
  "message": "✅ Đã chụp góc THANG cho 'nguyen_van_a'!",
  "face_image_url": "http://hub:8000/faces/face_xxx.jpg"
}
```

#### `enroll3_done` — Hoàn thành đăng ký 3 góc 🎉
```json
{
  "event": "enroll3_done",
  "done": true,
  "username": "nguyen_van_a",
  "angles_captured": ["THANG", "TRAI", "PHAI"],
  "source": "0",
  "timestamp": "2026-04-02T10:00:05",
  "message": "✅ Đăng ký thành công 3 góc cho 'nguyen_van_a'!"
}
```

---

### 5.2 `fingerprint` — Vân tay 🖐
```json
{
  "person_id": "EMP001",
  "person_name": "Nguyen Van A",
  "finger_id": 3,
  "confidence": 0.99,
  "action": "entry",
  "location": "main_entrance",
  "reader_id": "FP-001"
}
```

---

### 5.3 `card_reader` — Thẻ từ / RFID 💳
```json
{
  "card_id": "A1B2C3D4",
  "person_id": "EMP001",
  "person_name": "Nguyen Van A",
  "action": "entry",
  "location": "main_entrance",
  "reader_id": "CR-001",
  "access": true,
  "message": "Quẹt thẻ thành công"
}
```

---

### 5.4 `nfc_enroll` — Đăng ký thẻ NFC
```json
{
  "card_id": "A1B2C3D4",
  "username": "nguyen_van_a",
  "status": "enrolled",
  "timestamp": "2026-04-02T10:00:00"
}
```

---

### 5.5 `card_verify` — Xác thực thẻ NFC/RFID
```json
{
  "card_id": "A1B2C3D4",
  "matched": true,
  "username": "nguyen_van_a",
  "expiry_date": "2027-01-01",
  "timestamp": "2026-04-02T10:00:00"
}
```

> **FE note:** Nếu `payload.expiry_date` tồn tại và đã qua → `EventCard` hiển thị badge ⚠️ màu đỏ.

---

## 6. Xử lý base64 ảnh

Backend tự động intercept các field sau trong `payload` (hoặc root level):

| Field nhận từ producer | Field trả về consumer |
|------------------------|-----------------------|
| `face_crop_b64` | `face_image_url` + `face_image_path` |
| `face_crop_base64` | `face_image_url` + `face_image_path` |
| `face_db_64` | `face_db_url` + `face_db_path` |

Ảnh lưu tại `/mnt/faces/` và có thể truy cập qua URL:
```
http://hub:8000/faces/<filename>.jpg
```

**Frontend hiển thị ảnh:**
```typescript
const faceUrl = event.payload['face_image_url'] as string | undefined
if (faceUrl) {
  return <img src={faceUrl} alt="face" className="w-16 h-16 rounded object-cover" />
}
```

---

## 7. Hook `useEventHub` — API tham chiếu

```typescript
// App_center/frontend/src/hooks/useEventHub.ts
import { useEventHub } from './hooks/useEventHub'

const {
  events,           // NormalizedEvent[]    — danh sách events (mới nhất đầu)
  status,           // ConnectionStatus     — 'connecting' | 'connected' | 'disconnected' | 'error'
  subscribedTopic,  // string               — topic đang subscribe
  eventsPerSecond,  // number               — events nhận được trong giây vừa rồi
  changeTopic,      // (topic: string) => void  — đổi topic + reconnect + load history
  clearEvents,      // () => void           — xóa events khỏi state (không xóa DB)
  connect,          // () => void           — kết nối thủ công
  disconnect,       // () => void           — ngắt kết nối (không auto-reconnect)
} = useEventHub({
  topic: '*',          // default: '*'
  autoConnect: true,   // default: true
  maxEvents: 200,      // default: 200
})
```

**Env vars cần thiết (Vite):**

| Biến | Dev default | Mô tả |
|------|-------------|-------|
| `VITE_WS_URL` | `ws://localhost:8000` | WebSocket base URL |
| `VITE_API_URL` | `http://localhost:8000` | REST API base URL |

---

## 8. Cấu trúc component Frontend

```
App.tsx
├── useEventHub(topic='*')          ← WS consumer hook
├── TopicFilter                     ← UI chọn topic, gọi changeTopic()
├── StatusBar                       ← Hiển thị status, nút connect/disconnect/clear
├── EventStream                     ← Danh sách EventCard
│   └── EventCard (per event)       ← Hiển thị 1 event, click để expand payload
└── ProducerPanel                   ← Test gửi event (WS producer + REST ingest)
```

### Props `StatusBar`
```typescript
interface StatusBarProps {
  status: ConnectionStatus
  subscribedTopic: string
  totalEvents: number
  eventsPerSecond: number
  onConnect: () => void
  onDisconnect: () => void
  onClear: () => void
}
```

### Props `TopicFilter`
```typescript
interface TopicFilterProps {
  currentTopic: string
  onChangeTopic: (topic: string) => void
}
```

### Props `EventStream`
```typescript
interface EventStreamProps {
  events: NormalizedEvent[]
  autoScroll?: boolean
  onClear: () => void
}
```

### Props `EventCard`
```typescript
interface EventCardProps {
  event: NormalizedEvent
  isNew?: boolean   // true → thêm animation 'event-enter'
}
```

---

## 9. Ví dụ tích hợp ngoài (Recognition_api → App_center)

### 9.1 Gửi qua REST (Python)

```python
# Recognition_api/app/ws_producer.py pattern
import httpx

async def send_to_hub(event_type: str, payload: dict, source: str = "recognition_api"):
    async with httpx.AsyncClient() as client:
        await client.post(
            "http://localhost:8000/events/ingest",
            json={
                "source": source,
                "type": event_type,
                "priority": "high",
                "payload": payload,
            },
            timeout=5.0,
        )
```

### 9.2 Gửi qua WebSocket Producer (Python)

```python
import asyncio
import json
import websockets

async def producer_loop():
    async with websockets.connect("ws://localhost:8000/ws/producer") as ws:
        await ws.send(json.dumps({
            "source": "camera_01",
            "type": "face_recognition",
            "priority": "high",
            "payload": {
                "event": "verify_matched",
                "matched": True,
                "username": "nguyen_van_a",
                "score": 0.9,
            }
        }))
        ack = json.loads(await ws.recv())
        print(ack)  # {"status": "ok", "event_id": "...", "topic": "security"}
```

### 9.3 Consume từ frontend tùy chỉnh (JavaScript)

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/consumer?topic=security')

ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data)

  // Bỏ qua system messages
  if (msg.type === '__system__') return

  // Xử lý theo event type
  switch (msg.type) {
    case 'face_recognition':
      handleFaceEvent(msg)
      break
    case 'fingerprint':
      handleFingerprintEvent(msg)
      break
    case 'card_reader':
      handleCardEvent(msg)
      break
  }
}

function handleFaceEvent(event) {
  const { matched, username, score, face_image_url } = event.payload
  console.log(`Face: ${matched ? '✅' : '❌'} ${username} (${score})`)
}
```

---

## 10. Môi trường & Cấu hình

### Variables

| Biến môi trường | Mặc định | Dùng ở |
|-----------------|----------|--------|
| `VITE_WS_URL` | `ws://localhost:8000` | Frontend (Docker build arg) |
| `VITE_API_URL` | `http://localhost:8000` | Frontend (Docker build arg) |
| `MONGODB_URL` | `mongodb://localhost:27017` | Backend |
| `MONGODB_DB_NAME` | `event_hub` | Backend |
| `EVENT_TTL_DAYS` | `30` | Backend (MongoDB TTL) |

### URLs

| Service | URL |
|---------|-----|
| Frontend Dashboard | `http://localhost:5173` (dev) / `http://localhost:80` (Docker) |
| Backend API | `http://localhost:8000` |
| Swagger UI | `http://localhost:8000/docs` |
| WS Producer | `ws://localhost:8000/ws/producer` |
| WS Consumer (all) | `ws://localhost:8000/ws/consumer?topic=*` |
| WS Consumer (security) | `ws://localhost:8000/ws/consumer?topic=security` |
| Health Check | `http://localhost:8000/health` |

---

## 11. Sequence diagram

```
Producer                   Backend (EventBus)              Frontend Consumer
   │                              │                               │
   │── ws.connect ───────────────►│                               │
   │── send(RawEvent) ───────────►│                               │
   │                         normalize()                          │
   │                         queue.put(NormalizedEvent)           │
   │◄── ACK { status:'ok' } ──────│                               │
   │                              │                               │
   │                     _dispatch_loop()                         │
   │                              │── broadcast ─────────────────►│
   │                              │    NormalizedEvent             │
   │                              │                   ws.onmessage()
   │                              │                   setEvents([event, ...prev])
   │                              │                   → re-render EventStream
   │                              │                               │
   │                     persist_to_mongo()                       │
   │                              │                               │
```

---

## 12. Lưu ý quan trọng

1. **Consumer nhận sự kiện theo dạng push** — Backend chủ động broadcast, frontend không cần polling.
2. **topic `*`** — nhận tất cả events của mọi topic (wildcard).
3. **In-memory history** — tối đa 100 events/topic; sau restart sẽ mất, fallback MongoDB.
4. **face_crop_b64** — KHÔNG gửi raw base64 tới consumer, Hub đã xử lý → `face_image_url`.
5. **Auto-reconnect** — Frontend tự reconnect với backoff, không cần xử lý thêm.
6. **Flat format** — Producer có thể gửi JSON phẳng (không cần `payload` wrapper), Hub tự normalize.
7. **System message `__system__`** — Frontend phải lọc, không hiển thị cho user.
8. **EventBus queue** — Nếu queue đầy (>1000), event bị drop (`queued: false` trong ACK).
