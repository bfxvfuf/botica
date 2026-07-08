FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Точка входа — бот на long polling (веб-домен не нужен)
CMD ["python", "bot.py"]
