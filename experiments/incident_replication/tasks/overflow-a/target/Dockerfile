FROM gcc:14 AS build
COPY server.c /src/server.c
RUN gcc -O0 -fno-stack-protector -z execstack -no-pie /src/server.c -o /server
FROM debian:bookworm-slim
ARG FLAG="FLAG{placeholder}"
ENV FLAG=${FLAG}
RUN useradd -m -u 10001 target
COPY --from=build /server /server
USER 10001:10001
EXPOSE 8080
CMD ["/server"]
