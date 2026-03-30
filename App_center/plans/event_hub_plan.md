# Event Hub - Kiến trúc Trung gian Realtime

## Tổng quan

Hệ thống Event Hub đóng vai trò **trung gian chuẩn hóa sự kiện** giữa các thiết bị/dịch vụ gửi sự kiện (producers) và các consumer muốn nhận sự kiện đã chuẩn hóa (web, app, API).

```
Producers                 Event Hub (FastAPI)              Consumers
---------                 ------------------               ---------
Camera Face Recognition  --> [WS /ws/producer]             [WS /ws/consumer] --> React Web
Call Center System       --> [WS /ws/producer]  [Queue]   [WS /ws/consumer] --> Mobile App
IoT Sensors              --> [REST POST /event]  [Norm]   [REST GET /events] --> Third-party
Other Devices            --> [WS /ws/producer]             [WS /ws/consumer] --> Other
```

---

## Kiến trúc chi tiết

### Luồng xử lý sự kiện

```
Producer gửi raw event
        |
        v
[WebSocket /ws/producer hoặc REST POST /events/ingest]
        |
        v
[Event Receiver] -- validate schema cơ bản
        |
        v
[Event Normalizer] -- chuẩn hóa: thêm id, timestamp, source, type
        |
        v
[In-Memory Queue / EventBus] -- asyncio.Queue + dict[topic -> set[consumers]]
        |
        v
[Event Dispatcher] -- broadcast tới tất cả consumers đang subscribe
        |
        v
[WebSocket /ws/consumer?topic=face_recognition]
        |
        v
Consumer (React Web, App, v.v.)
```

---

## Cấu trúc thư mục dự án

```
App_center/
├── backend/                    # FastAPI Event Hub
│   ├── main.py                 # FastAPI app entry point
│   ├── core/
│   │   ├── event_bus.py        # In-memory event bus + queue
│   │   ├── normalizer.py       # Event normalization logic
│   │   └── models.py           # Pydantic models cho events
│   ├── api/
│   │   ├── ws_producer.py      # WebSocket endpoint cho producers
│   │   ├── ws_consumer.py      # WebSocket endpoint cho consumers
│   │   └── rest_events.py      # REST API: ingest + query events
│   ├── requirements.txt
│   └── .env
│
└── frontend/                   # React Dashboard
    ├── src/
    │   ├── App.tsx
    │   ├── hooks/
    │   │   └── useEventHub.ts  # Custom hook WebSocket consumer
    │   ├── components/
    │   │   ├── EventStream.tsx  # Live feed sự kiện
    │   │   ├── EventCard.tsx    # Chi tiết 1 sự kiện
    │   │   └── TopicFilter.tsx  # Lọc theo topic/loại sự kiện
    │   └── types/
    │       └── event.ts         # TypeScript types
    ├── package.json
    └── vite.config.ts
```

---

## Chuẩn hóa sự kiện (Event Schema)

Mọi sự kiện sau khi qua normalizer đều có cấu trúc thống nhất:

```json
{
  "id": "uuid-v4",
  "timestamp": "2026-03-28T08:00:00.000Z",
  "source": "camera_01",
  "type": "face_recognition",
  "topic": "security",
  "priority": "high",
  "payload": {
    // raw data từ producer, giữ nguyên
  },
  "metadata": {
    "received_at": "2026-03-28T08:00:00.001Z",
    "normalized": true,
    "version": "1.0"
  }
}
```

### Các loại sự kiện hỗ trợ (event types)

| type | Mô tả | topic |
|------|-------|-------|
| `face_recognition` | Camera nhận diện khuôn mặt | `security` |
| `call_received` | Cuộc gọi đến | `communication` |
| `call_ended` | Cuộc gọi kết thúc | `communication` |
| `device_status` | Trạng thái thiết bị | `system` |
| `alert` | Cảnh báo | `alert` |
| `custom` | Sự kiện tùy chỉnh | `custom` |

---

## Chi tiết Technical

### Backend - FastAPI

#### `core/event_bus.py` - In-Memory Event Bus
- Sử dụng `asyncio.Queue` để nhận sự kiện từ producers
- Lưu `dict[topic, set[WebSocket]]` để quản lý consumers đang subscribe
- Background task dispatcher chạy liên tục broadcast events
- Giới hạn queue size (mặc định 1000) để tránh memory overflow
- Lưu lịch sử N sự kiện gần nhất (mặc định 100) per topic

#### `core/normalizer.py` - Chuẩn hóa sự kiện
- Nhận raw dict từ producer
- Thêm `id` (UUID), `timestamp`, `received_at`
- Validate và map `type`, `topic`, `priority`
- Trả về `NormalizedEvent` (Pydantic model)

#### WebSocket Endpoints

**Producer** (`/ws/producer`):
- Producer connect, gửi JSON event bất cứ lúc nào
- Event được validate → normalize → đưa vào queue
- Hỗ trợ nhiều producers connect đồng thời

**Consumer** (`/ws/consumer?topic=face_recognition`):
- Consumer connect với query param `topic` (hoặc `*` để nhận tất cả)
- Event Hub tự động broadcast khi có event mới
- Khi disconnect tự động unsubscribe

#### REST API

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| POST | `/events/ingest` | Producer gửi event qua HTTP |
| GET | `/events/recent?topic=&limit=` | Lấy N sự kiện gần nhất |
| GET | `/events/topics` | Danh sách topics đang active |
| GET | `/health` | Health check |

### Frontend - React

#### `useEventHub` hook
- Tự động connect WebSocket tới `/ws/consumer?topic=`
- Auto-reconnect khi mất kết nối
- Trả về `events[]`, `status`, `subscribe(topic)`, `unsubscribe(topic)`

#### Components
- **EventStream**: Hiển thị live feed dạng list, scroll xuống tự động
- **EventCard**: Card hiển thị 1 sự kiện với icon theo type, timestamp, payload expandable
- **TopicFilter**: Checkbox/tag chọn topics muốn subscribe
- **StatusBar**: Hiển thị connection status + số events/giây

---

## Sơ đồ luồng WebSocket

```
Producer Device          Event Hub Backend           Consumer Web
      |                         |                          |
      |--[WS Connect]---------->|                          |
      |                         |                          |
      |                         |<--[WS Connect topic=*]---|
      |                         |                          |
      |--[send raw event]------>|                          |
      |                         |--[normalize event]       |
      |                         |--[push to queue]         |
      |                         |--[dispatch to consumers] |
      |                         |--[broadcast event]------>|
      |                         |                          |
      |--[send raw event]------>|                          |
      |                         |--[normalize + dispatch]->|
      |                         |                          |
```

---

## Các bước triển khai

### Phase 1 - Backend Core
1. Khởi tạo FastAPI project, cấu hình CORS
2. Tạo Pydantic models (`RawEvent`, `NormalizedEvent`)
3. Implement `EventBus` class với asyncio.Queue
4. Implement `EventNormalizer` class
5. Implement WebSocket endpoint cho producers (`/ws/producer`)
6. Implement WebSocket endpoint cho consumers (`/ws/consumer`)
7. Implement REST API endpoints
8. Background task dispatcher

### Phase 2 - Frontend React
1. Khởi tạo Vite + React + TypeScript project
2. Implement `useEventHub` custom hook
3. Implement `EventStream` component
4. Implement `EventCard` component
5. Implement `TopicFilter` component
6. Ghép nối App.tsx với tất cả components

### Phase 3 - Testing
1. Test producer gửi face_recognition event qua WebSocket
2. Test producer gửi call event qua REST API
3. Verify consumers nhận đúng events theo topic filter
4. Test multiple consumers đồng thời
5. Test reconnection logic

---

## Stack công nghệ

| Layer | Công nghệ |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, WebSockets, asyncio |
| Data validation | Pydantic v2 |
| Frontend | React 18, TypeScript, Vite |
| Realtime | Native WebSocket API |
| Styling | TailwindCSS |
| HTTP Client | Axios |

---

## Ví dụ sự kiện từ Producer

### Camera nhận diện khuôn mặt
```json
{
  "source": "camera_entrance_01",
  "type": "face_recognition",
  "payload": {
    "person_id": "EMP001",
    "confidence": 0.98,
    "action": "entry",
    "location": "main_entrance"
  }
}
```

### Call center
```json
{
  "source": "call_system",
  "type": "call_received",
  "payload": {
    "call_id": "CALL-2026-001",
    "caller": "0901234567",
    "department": "support"
  }
}
```
