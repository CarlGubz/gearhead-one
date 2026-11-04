# CalculiX solver image used by the backend's CalculiX adapter when `ccx` is not installed locally.
#   docker build -t cadweb/calculix docker/calculix
# The adapter runs:  docker run --rm -v <job dir>:/work cadweb/calculix -i job
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends calculix-ccx \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
# one thread: the multithreaded SPOOLES build gives spurious eigenvalues (see backend calculix.py)
ENV OMP_NUM_THREADS=1 CCX_NPROC_EQUATION_SOLVER=1
ENTRYPOINT ["ccx"]
