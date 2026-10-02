package com.sense2act.backend.backtest;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E5-5 限流验收:阈值调成 5/分钟,第 6 发内部请求 42901 + Retry-After;
 * 用户侧接口不经此过滤器(同前缀但 /api/v1/internal/ 之外不受影响)。
 */
@SpringBootTest(properties = {"app.internal-key=test-key", "app.internal-rate-limit-per-minute=5"})
@AutoConfigureMockMvc
@Testcontainers
class RateLimitTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Test
    void 内部接口超阈值42901_用户接口不受影响() throws Exception {
        // 5 发领取队列(内部只读端点,幂等):全部放行
        for (int i = 0; i < 5; i++) {
            mockMvc.perform(get("/api/v1/internal/ingest-queue")
                            .header("X-Internal-Key", "test-key"))
                    .andExpect(status().isOk());
        }
        // 第 6 发 → 42901 + Retry-After
        mockMvc.perform(get("/api/v1/internal/ingest-queue")
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isTooManyRequests())
                .andExpect(jsonPath("$.code").value(42901))
                .andExpect(r -> assertThat(r.getResponse().getHeader("Retry-After")).isNotNull());
        // 无键请求不消耗令牌,仍走认证层 40101
        mockMvc.perform(get("/api/v1/internal/ingest-queue"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
        // 用户侧登录+读不受内部限流影响
        String token = login("viewer@sense2act.local", "viewer123");
        mockMvc.perform(get("/api/v1/dashboard/summary").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk());
    }

    private String login(String email, String password) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/auth/token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"%s\",\"password\":\"%s\"}".formatted(email, password)))
                .andExpect(status().isOk())
                .andReturn();
        JsonNode node = objectMapper.readTree(
                result.getResponse().getContentAsString(StandardCharsets.UTF_8));
        return node.path("data").path("token").asText();
    }
}
