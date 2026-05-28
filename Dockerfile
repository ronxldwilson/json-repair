FROM ghcr.io/ggml-org/llama.cpp:server

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && pip install --no-cache-dir --break-system-packages pydantic \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY scripts/ scripts/
COPY schemas/ schemas/
COPY models/*.gguf models/

ENV PATH="/app:${PATH}"

EXPOSE 8776

ENTRYPOINT ["python3", "scripts/fix_json.py"]
