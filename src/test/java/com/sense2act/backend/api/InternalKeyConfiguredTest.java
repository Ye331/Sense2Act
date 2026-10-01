package com.sense2act.backend.api;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** 配置了 INTERNAL_API_KEY 时的门卫行为(与 AuthFlowTest 的未配置场景互补)。 */
@SpringBootTest(properties = "app.internal-key=test-key-123")
@AutoConfigureMockMvc
@Testcontainers
class InternalKeyConfiguredTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    @Autowired
    MockMvc mockMvc;

    @Test
    void 缺key_40101() throws Exception {
        mockMvc.perform(get("/api/v1/internal/ingest-queue"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
    }

    @Test
    void 错key_40101() throws Exception {
        mockMvc.perform(get("/api/v1/internal/ingest-queue")
                        .header("X-Internal-Key", "wrong"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
    }

    @Test
    void 对key_过门卫后40401_证明校验通过() throws Exception {
        mockMvc.perform(get("/api/v1/internal/ingest-queue")
                        .header("X-Internal-Key", "test-key-123"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }
}
