# PyFR runtime image used by CFDFlow's PyFR backend (CFD_PYFR_DOCKER_IMAGE).
#   docker build -t cfdflow/pyfr:2.0.3 docker/pyfr
# Compute backend: OpenMP (CPU). For GPUs build on a CUDA/ROCm base image and set CFD_PYFR_DEVICE.
FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 OMP_NUM_THREADS=4
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc g++ gfortran make git ca-certificates \
        libopenmpi-dev openmpi-bin libhdf5-dev pkg-config \
 && rm -rf /var/lib/apt/lists/*
# PyFR's OpenMP backend needs libxsmm as a shared library built without BLAS (see PyFR docs)
ARG LIBXSMM_COMMIT=main
RUN git clone https://github.com/libxsmm/libxsmm.git /tmp/libxsmm \
 && cd /tmp/libxsmm && git checkout ${LIBXSMM_COMMIT} \
 && make -j"$(nproc)" STATIC=0 BLAS=0 \
 && cp lib/libxsmm.so* /usr/local/lib/ && ldconfig \
 && rm -rf /tmp/libxsmm
RUN pip install "pyfr==2.0.3" meshio
ENV LD_LIBRARY_PATH=/usr/local/lib PYFR_XSMM_LIBRARY_PATH=/usr/local/lib/libxsmm.so
WORKDIR /case
CMD ["pyfr", "--help"]
