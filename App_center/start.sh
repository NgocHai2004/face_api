#!/bin/bash
# start.sh - Khởi động Event Hub (Backend + Frontend)

set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

echo ""
echo "⚡ Starting Event Hub..."
echo "================================"

# Kill existing processes on ports 8000 and 5173
echo "🔧 Cleaning up existing processes..."
fuser -k 8000/tcp 2>/dev/null || true
fuser -k 5173/tcp 2>/dev/null || true
sleep 1

# Start Backend
echo "🚀 Starting Backend (FastAPI) on :8000..."
cd "$BACKEND_DIR"
venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID"

# Wait for backend to be ready
echo "   Waiting for backend..."
for i in {1..10}; do
  if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "   ✓ Backend ready!"
    break
  fi
  sleep 1
done

# Start Frontend
echo "🌐 Starting Frontend (React/Vite) on :5173..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!
echo "   Frontend PID: $FRONTEND_PID"

echo ""
echo "================================"
echo "✅ Event Hub is running!"
echo ""
echo "  Web Dashboard : http://localhost:5173"
echo "  API Docs      : http://localhost:8000/docs"
echo "  Health Check  : http://localhost:8000/health"
echo ""
echo "  WS Producer   : ws://localhost:8000/ws/producer"
echo "  WS Consumer   : ws://localhost:8000/ws/consumer?topic=*"
echo "  REST Ingest   : POST http://localhost:8000/events/ingest"
echo ""
echo "  Press Ctrl+C to stop all services"
echo "================================"
echo ""

# Trap Ctrl+C
trap "echo ''; echo 'Stopping...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

# Wait
wait
