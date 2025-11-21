FROM python:3.13-alpine

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY ./api ./api
COPY ./core ./core
COPY ./cli.py ./cli.py
COPY ./main.py ./main.py
COPY ./system_prompt.txt ./system_prompt.txt
COPY ./translations_normalized.json ./translations_normalized.json
COPY ./data ./data
COPY ./integrations ./integrations

CMD ["python", "-m", "integrations.tuya.bridge_soc"]
