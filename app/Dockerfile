# Base images are pinned by digest. To refresh a digest after bumping a tag:
#   docker buildx imagetools inspect <image:tag> --format '{{.Manifest.Digest}}'
# JVM flags are not set here; they come from JAVA_TOOL_OPTIONS in the k8s manifests so every
# arm runs the identical command line.

FROM maven:3.9.16-eclipse-temurin-21@sha256:8f6ac126f7810bb5549c4cd122d2bf0e9cda5bdeb0838aa928f09e779fd8bef8 AS build
WORKDIR /build
COPY pom.xml ./
RUN mvn -q -B -DskipTests dependency:go-offline
COPY src ./src
RUN mvn -q -B -DskipTests package

FROM eclipse-temurin:21.0.12_8-jre-jammy@sha256:eebd356ad7358b7094758e5787a6726f332917cfd56feab6457c56dab895cdbf
RUN groupadd --system --gid 10001 bench \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin bench
WORKDIR /app
COPY --from=build /build/target/bucket-bench.jar /app/bucket-bench.jar
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/bucket-bench.jar"]
