FROM python:3.14-slim
WORKDIR /app
RUN pip install --no-cache-dir "flask>=3.1.3"
COPY app.py .
ENV PORT=5055 BIND_ALL=1 PYTHONUNBUFFERED=1
EXPOSE 5055
CMD ["python", "app.py"]
