# ── Base image ────────────────────────────────────────────────
# Dùng python:3.11-slim để nhẹ; đổi sang 3.10/3.12 nếu cần
FROM python:3.11-slim

# ── System dependencies ───────────────────────────────────────
# build-essential + g++ cần để compile insightface (C++ extension)
# libgl1 + libglib2.0-0 cần cho OpenCV headless
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        g++ \
        cmake \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies ───────────────────────────────
# Copy requirements trước để tận dụng Docker layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy source code ──────────────────────────────────────────
COPY . .

# ── Create directories ────────────────────────────────────────
RUN mkdir -p face_images ssl

# ── Expose port ───────────────────────────────────────────────
EXPOSE 8001

# ── Entrypoint ────────────────────────────────────────────────
CMD ["python", "run.py"]
