#!/bin/bash

# Exit on error
set -e

echo "Starting development environment..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker and try again."
    exit 1
fi

# Start dependent services
echo "Starting dependent services with Docker Compose..."
docker compose up -d localstack

# Wait for services to be ready
echo "Waiting for services to be ready..."
sleep 5

# Set environment variables for local development
export PORT=8086
export AWS_ENDPOINT_URL=http://localhost:4566
export POSTGRES_URI="postgresql://aiauser:Admin123\$@localhost:5432/aia_documents"
export S3_BUCKET_NAME=docsupload
export ENV=dev
export HOST=localhost
export LOG_CONFIG=logging-dev.json

# Provide default fake AWS credentials for localstack if not defined
export AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-test}
export AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-test}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-eu-west-2}

if [ -f .env ]; then
    echo "Loading environment variables from .env..."
    export $(grep -v '^#' .env | xargs)
fi

# Check uvicorn is available
if ! command -v uvicorn &> /dev/null; then
    echo "Error: uvicorn is not installed. Please ensure your virtual environment is activated and dependencies are installed."
    exit 1
fi

# Start the application
echo "Starting FastAPI application..."
uvicorn app.api.main:app --host $HOST --port $PORT --reload --log-config=$LOG_CONFIG

# Cleanup function
cleanup() {
    echo "Shutting down..."
    echo "Development server stopped."
}

# Register cleanup function
trap cleanup EXIT
