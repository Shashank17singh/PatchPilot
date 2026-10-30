FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# PatchPilot compiles and runs the project it repairs, so a C++ toolchain is required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ make cmake git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

# Run as an unprivileged user. The project to repair is mounted at /workspace.
RUN useradd --create-home --uid 1000 patchpilot
USER patchpilot
WORKDIR /workspace

ENTRYPOINT ["patchpilot"]
CMD ["/workspace"]
