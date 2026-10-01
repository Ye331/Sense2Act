package com.sense2act.backend.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;

/**
 * 契约基线:实际生成的 OpenAPI spec 必须与 docs/openapi/baseline.json 一致(DoD:swagger=api-design)。
 * 改了接口后重新生成并提交基线:mvn test -Dtest=OpenApiBaselineTest -DupdateOpenapi=true
 * 破坏性变更(§9 路径/字段/状态值)先在 api-design.md §11 登记,CI 里有 oasdiff 闸门。
 */
@SpringBootTest
@AutoConfigureMockMvc
@Testcontainers
class OpenApiBaselineTest {

    private static final Path BASELINE = Path.of("docs/openapi/baseline.json");

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    @Autowired
    MockMvc mockMvc;

    @Test
    void 契约基线一致() throws Exception {
        String live = mockMvc.perform(get("/v3/api-docs"))
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);

        if (Boolean.getBoolean("updateOpenapi")) {
            ObjectMapper sorted = new ObjectMapper()
                    .configure(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true);
            Files.createDirectories(BASELINE.getParent());
            Files.writeString(BASELINE, sorted.writerWithDefaultPrettyPrinter()
                    .writeValueAsString(new ObjectMapper().readTree(live)));
            return; // 已重写基线,提交生成文件即可
        }

        assertTrue(Files.exists(BASELINE),
                "docs/openapi/baseline.json 不存在。运行:"
                        + "mvn test -Dtest=OpenApiBaselineTest -DupdateOpenapi=true 生成并提交");
        JsonNode committed = new ObjectMapper().readTree(Files.readString(BASELINE));
        assertEquals(committed, new ObjectMapper().readTree(live),
                "接口与基线不一致:改了接口就重新生成基线并提交,破坏性变更先走 api-design.md §11");
    }
}
