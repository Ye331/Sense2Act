package com.sense2act.backend.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.ErrorCode;
import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.Bucket;
import io.github.bucket4j.ConsumptionProbe;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

/**
 * 内部接口限流(E5-5,选型 Bucket4j,决策见 backlog):按 X-Internal-Key 建令牌桶,
 * 容量 = 每分钟阈值,贪心补币(整分钟一次补满)—— 单机内存即可,满足"防止某个外部服务拖垮后端"。
 * 超限回 42901 统一信封,并带 Retry-After(秒)。阈值 0 或未配置 → 不限流。
 * 认证仍由 InternalKeyFilter 负责:本过滤器只看键值分桶,不校验合法性(无键的请求由认证层 40101)。
 */
public class InternalRateLimitFilter extends OncePerRequestFilter {

    private final long permitsPerMinute;
    private final ObjectMapper objectMapper;
    private final ConcurrentMap<String, Bucket> buckets = new ConcurrentHashMap<>();

    public InternalRateLimitFilter(long permitsPerMinute, ObjectMapper objectMapper) {
        this.permitsPerMinute = permitsPerMinute;
        this.objectMapper = objectMapper;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        if (permitsPerMinute <= 0) {
            return true;
        }
        return !request.getRequestURI().startsWith("/api/v1/internal/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String key = request.getHeader("X-Internal-Key");
        if (key == null || key.isBlank()) {
            chain.doFilter(request, response);   // 认证层会回 40101
            return;
        }
        Bucket bucket = buckets.computeIfAbsent(key, k -> Bucket.builder()
                .addLimit(Bandwidth.builder().capacity(permitsPerMinute)
                        .refillGreedy(permitsPerMinute, Duration.ofMinutes(1)).build())
                .build());
        ConsumptionProbe probe = bucket.tryConsumeAndReturnRemaining(1);
        if (probe.isConsumed()) {
            chain.doFilter(request, response);
            return;
        }
        long retryAfterSeconds = Math.max(1, probe.getNanosToWaitForRefill() / 1_000_000_000);
        response.setStatus(ErrorCode.RATE_LIMITED.httpStatus().value());
        response.setContentType("application/json");
        response.setCharacterEncoding("UTF-8");
        response.setHeader("Retry-After", String.valueOf(retryAfterSeconds));
        objectMapper.writeValue(response.getWriter(),
                ApiResponse.error(ErrorCode.RATE_LIMITED, "内部接口限流:每分钟 " + permitsPerMinute + " 次,稍后重试"));
    }
}
