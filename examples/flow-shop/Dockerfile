# Docker build for the flow-shop fixture; build context is this directory (see contract.docker.yaml).
FROM python:3.14-slim
WORKDIR /app
RUN pip install --no-cache-dir "flask>=3.1.3"
COPY app.py .
# app.py binds 127.0.0.1, which is unreachable through a published container port. Inside the container only,
# bind all interfaces. The tracked app.py is not edited and no route or behaviour changes. The host side is a
# separate matter (see "Docker mode" in documentation/getting-started.md).
RUN sed -i 's/host="127.0.0.1"/host="0.0.0.0"/' app.py && grep -q 'host="0.0.0.0"' app.py
ENV PORT=5088 PYTHONUNBUFFERED=1
EXPOSE 5088
CMD ["python", "app.py"]
