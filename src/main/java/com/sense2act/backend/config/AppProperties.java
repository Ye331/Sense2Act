package com.sense2act.backend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.math.BigDecimal;
import java.time.Duration;

/** 应用配置,值全部来自环境变量(见 .env.example),环境差异不进代码。 */
@ConfigurationProperties(prefix = "app")
public record AppProperties(Jwt jwt, String internalKey, String snapshotDir, Seed seed, Embedding embedding,
                            Investigation investigation) {

    public record Jwt(String secret, Duration ttl) {
    }

    public record Seed(boolean enabled, String adminPassword, String analystPassword, String viewerPassword) {
    }

    /**
     * D12:embedding 全部由外部端点计算,后端只做一次无状态出站调用(文本进、向量出)。
     * endpoint 未配置 → 语义检索降级为关键词,不报错。
     */
    public record Embedding(String endpoint, String apiKey, Duration timeout) {
    }

    /** 调查运行参数(D7):created 超时清理阈值;token 单价用于 cost_estimate 折算。 */
    public record Investigation(Duration createdTtl, BigDecimal tokenUnitPrice) {
    }
}
