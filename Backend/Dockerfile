# ---- 构建层:Maven + JDK 21(依赖走 BuildKit 缓存,重建镜像不重下)----
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /app
COPY pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp -q dependency:go-offline
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp -q package -DskipTests

# ---- 运行层:JRE,非 root,带 curl 供健康检查 ----
FROM eclipse-temurin:21-jre
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1001 --create-home appuser
WORKDIR /app
RUN mkdir -p /app/snapshots && chown -R appuser:appuser /app
USER appuser
COPY --from=build /app/target/*.jar /app/app.jar
ENV TZ=Asia/Shanghai JAVA_OPTS=""
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=90s --retries=5 \
    CMD curl -fsS http://localhost:8080/actuator/health || exit 1
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]
