# Event Hub - Hệ thống Trung gian Sự kiện Realtime

## Tổng quan

**Event Hub** là middleware trung gian chuẩn hóa sự kiện realtime giữa các thiết bị/dịch vụ (producers) và các consumers (web, app, API).

```
Producers                    Event Hub (FastAPI)               Consumers
─────────                    ──────────────────               ─────────
Camera Face Recognition  ──► [WS /ws/producer]                [WS /ws/consumer] ──► React Web
Call Center System       ──► [REST POST /events/ingest] ──►   [WS /ws/consumer] ──► Mobile App
IoT Sensors              ──► [WS /ws/producer]   [Queue]      [GET /events/recent] ─► API
```

---

## Cài đặt & Chạy

### Yêu cầu
- Python 3.11+
- Node.js 20+

### Khởi động nhanh

```bash
chmod +x start.sh
./start.sh
```

### Hoặc chạy thủ công

**Backend:**
```bash
cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

---

## URL & Endpoints

| Service | URL |
|---------|-----|
| React Dashboard | http://localhost:5173 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Health Check | http://localhost:8000/health |

### WebSocket Endpoints

| Endpoint | Dùng cho |
|----------|----------|
| `ws://localhost:8000/ws/producer` | Producers gửi raw events |
| `ws://localhost:8000/ws/consumer?topic=*` | Consumers nhận tất cả events |
| `ws://localhost:8000/ws/consumer?topic=security` | Nhận events theo topic |

### REST API

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| POST | `/events/ingest` | Gửi event qua HTTP |
| GET | `/events/recent?topic=&limit=` | Query lịch sử events |
| GET | `/events/topics` | Danh sách topics active |
| GET | `/events/types` | Event types được hỗ trợ |
| GET | `/health` | Health check + stats |

---

## Schema sự kiện chuẩn hóa

```json
{
  "id": "uuid-v4",
  "timestamp": "2026-03-28T08:00:00.000Z",
  "source": "camera_01",
  "type": "face_recognition",
  "topic": "security",
  "priority": "high",
  "payload": {
    "person_id": "EMP001",
    "confidence": 0.98
  },
  "metadata": {
    "received_at": "2026-03-28T08:00:00.001Z",
    "normalized": true,
    "version": "1.0"
  }
}
```

### Event Types & Topics

| Type | Topic tự động |
|------|---------------|
| `face_recognition` | `security` |
| `call_received` | `communication` |
| `call_ended` | `communication` |
| `call_missed` | `communication` |
| `device_status` | `system` |
| `alert` | `alert` |
| `custom` | `custom` |

---

## Ví dụ Producer gửi event

### Qua WebSocket
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/producer')
ws.onopen = () => {
  ws.send(JSON.stringify({
    source: 'camera_01',
    type: 'face_recognition',
    priority: 'high',
    payload: { person_id: 'EMP001', confidence: 0.98 }
  }))
}
ws.onmessage = (e) => console.log(JSON.parse(e.data))
// { status: 'ok', event_id: 'uuid...', topic: 'security' }
```

### Qua REST HTTP
```bash
curl -X POST http://localhost:8000/events/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "camera_01",
    "type": "face_recognition",
    "priority": "high",
    "payload": {"person_id": "EMP001", "confidence": 0.98}
  }'
```

### Consumer nhận events
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/consumer?topic=*')
ws.onmessage = (e) => {
  const event = JSON.parse(e.data)
  console.log(event.type, event.topic, event.payload)
}
```

---

## Cấu trúc dự án

```
App_center/
├── backend/
│   ├── main.py               # FastAPI entry point
│   ├── core/
│   │   ├── models.py         # Pydantic schemas
│   │   ├── event_bus.py      # In-memory event bus
│   │   └── normalizer.py     # Event normalization
│   ├── api/
│   │   ├── ws_producer.py    # WS endpoint producers
│   │   ├── ws_consumer.py    # WS endpoint consumers
│   │   └── rest_events.py    # REST API endpoints
│   └── requirements.txt
│
├── frontend/
│   └── src/
│       ├── App.tsx
│       ├── hooks/
│       │   └── useEventHub.ts  # WebSocket consumer hook
│       ├── components/
│       │   ├── EventStream.tsx
│       │   ├── EventCard.tsx
│       │   ├── TopicFilter.tsx
│       │   ├── StatusBar.tsx
│       │   └── ProducerPanel.tsx
│       └── types/event.ts
│
├── start.sh                  # Script khởi động
└── README.md
```
BaseURL: https://clawapigate.com/v1
API key: sk-8cc87e7da38f71750cc50fde113027d9e535da42278da0bd
