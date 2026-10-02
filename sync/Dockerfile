FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
LABEL org.opencontainers.image.source=https://github.com/vyrmy/umbrel-edge
LABEL org.opencontainers.image.description="umbrel-edge sync service"
COPY --from=build /install /usr/local
RUN useradd --uid 1000 --create-home edge
USER 1000:1000
ENV PYTHONUNBUFFERED=1
EXPOSE 9000
ENTRYPOINT ["python", "-m", "umbrel_edge"]
