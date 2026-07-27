# Use Python 3.11 as a stable base
FROM python:3.11-slim

# Install system dependencies for Selenium and other tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium-driver \
    chromium \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Set environment variables for non-interactive behavior
ENV PYTHONUNBUFFERED=1

# Command to keep container alive if not running a specific script
CMD ["tail", "-f", "/dev/null"]
