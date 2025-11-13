FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копирование зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование приложения (исключая старый main.py)
COPY app ./app

# УДАЛЯЕМ старый main.py если он существует
RUN rm -f /app/app/main.py

# Переменные окружения
ENV PORT=8080
ENV PYTHONPATH=/app

CMD ["sh", "-c", "uvicorn app.webhook:app --host 0.0.0.0 --port ${PORT:-8080}"]
