FROM python:3.12-slim

WORKDIR /app

# Playwright の依存ライブラリをインストール
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libdbus-1-3 \
    libatk1.0-0 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libfontconfig1 \
    libfreetype6 \
    fonts-dejavu \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Python パッケージをインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright をインストール
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium

# コードをコピー
COPY keiba_generator_optimized.py .

# ポート設定
ENV PORT=5000

# 実行
CMD ["python", "keiba_generator_optimized.py"]
