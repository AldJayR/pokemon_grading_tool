FROM python:3.12.2-slim

# Set the working directory
WORKDIR /app

# Copy the requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and its dependencies
RUN playwright install chromium --with-deps



# Copy the rest of your application code
COPY . .

# Expose the port your application runs on
EXPOSE 8000

# Command to run your application
CMD ["gunicorn", "pokemon_grading_tool.asgi:application", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
