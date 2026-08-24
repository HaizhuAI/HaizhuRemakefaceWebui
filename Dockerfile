FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway gateway
COPY webui webui
COPY state state
COPY data data
ENV ADMIN_PASSWORD=admin123 PORT=8611
EXPOSE 8611
CMD ["sh", "-c", "python3 -m uvicorn gateway.app:app --host 0.0.0.0 --port $PORT"]
