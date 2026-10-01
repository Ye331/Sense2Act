package com.sense2act.backend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/** 应用配置,值全部来自环境变量(见 .env.example),环境差异不进代码。 */
@ConfigurationProperties(prefix = "app")
public record AppProperties(Jwt jwt, String internalKey, String snapshotDir, Seed seed) {

    public record Jwt(String secret, Duration ttl) {
    }

    public record Seed(boolean enabled, String adminPassword, String analystPassword, String viewerPassword) {
    }
}
