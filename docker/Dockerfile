# Base: official Ultralytics image includes CUDA, cuDNN, PyTorch, and ultralytics
# Switch to cpu variant on non-GPU machines: ultralytics/ultralytics:latest-cpu
FROM ultralytics/ultralytics:latest

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ src/
COPY scripts/ scripts/

# Make scripts executable
RUN chmod +x scripts/*.py

ENV PYTHONPATH=/app/src

ENTRYPOINT ["python"]
CMD ["scripts/detect.py", "--help"]
