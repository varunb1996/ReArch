# Self-hosting image for the ReArch API (the public GitHub Pages demo is static and does not use it).
# Runs in public mode, so it is safe to expose; put the UI's origin in REARCH_CORS_ORIGINS.
#
#   docker build -t rearch-api .
#   docker run -p 7860:7860 -e GROQ_API_KEY=... -e REARCH_CORS_ORIGINS=https://your-ui rearch-api
#
# Not built in CI or on the author's machine yet: treat the first build as a test.
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/home/user/.cache/fastembed \
    REARCH_PUBLIC_MODE=1 \
    REARCH_DATA_DIR=/home/user/data
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fetch at build time what would otherwise download during the first visitor's
# analysis: the embedding model and the tree-sitter grammars.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')" \
    && python -c "import tree_sitter_language_pack as t; t.download(['python', 'javascript'])"

COPY --chown=user api ./api
COPY --chown=user parser ./parser
COPY --chown=user demo ./demo

EXPOSE 7860
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]
