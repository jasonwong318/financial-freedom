# 共用 image:api 同 ui 兩個 service 都用呢個(指令喺 compose 指定)
FROM python:3.12-slim

WORKDIR /app

# 先裝依賴(layer cache:requirements 冇變就唔使重裝)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY api/ ./api/
COPY ui/ ./ui/
COPY tests/ ./tests/

EXPOSE 8000 8501

# 預設起 API;UI service 喺 compose override command
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
