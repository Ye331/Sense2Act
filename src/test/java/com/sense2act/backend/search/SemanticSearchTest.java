package com.sense2act.backend.search;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.web.util.UriComponentsBuilder;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E1-6 验收(D12):入库自动补算 embedding、纯语义检索、keyword+semantic 的 RRF 融合、
 * 无 embedding 文档不进语义结果、端点失败降级关键词且不报错。
 *
 * 假端点:文本含"苹果"→ 维度 0 置 1,含"香蕉"→ 维度 1 置 1(512 维 one-hot),
 * 查询与文档同规则 → 相似度可控可断言。failMode 置 true 模拟端点不可用。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)   // 单实例共享种子,seed() 的 docX!=null 守卫才有效
class SemanticSearchTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    static final AtomicBoolean failMode = new AtomicBoolean(false);
    static HttpServer embeddingServer;
    static final ObjectMapper mapper = new ObjectMapper();

    @DynamicPropertySource
    static void embeddingEndpoint(DynamicPropertyRegistry registry) throws Exception {
        embeddingServer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        embeddingServer.createContext("/embed", exchange -> {
            try {
                if (failMode.get()) {
                    exchange.sendResponseHeaders(500, -1);
                    return;
                }
                @SuppressWarnings("unchecked")
                List<String> inputs = (List<String>) mapper.readValue(exchange.getRequestBody(), Map.class).get("inputs");
                List<float[]> embeddings = inputs.stream().map(SemanticSearchTest::fakeEmbed).toList();
                byte[] body = mapper.writeValueAsBytes(Map.of("embeddings", embeddings));
                exchange.getResponseHeaders().set("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, body.length);
                try (OutputStream out = exchange.getResponseBody()) {
                    out.write(body);
                }
            } finally {
                exchange.close();
            }
        });
        embeddingServer.start();
        registry.add("app.embedding.endpoint",
                () -> "http://127.0.0.1:" + embeddingServer.getAddress().getPort() + "/embed");
    }

    @AfterAll
    static void stopServer() {
        if (embeddingServer != null) {
            embeddingServer.stop(0);
        }
    }

    static float[] fakeEmbed(String text) {
        float[] v = new float[512];
        if (text.contains("苹果")) {
            v[0] = 1f;
        }
        if (text.contains("香蕉")) {
            v[1] = 1f;
        }
        return v;
    }

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    JdbcTemplate jdbcTemplate;

    String docX;   // 苹果招标公告(两路都命中)
    String docY;   // 香蕉招标公告(仅关键词路)
    String docZ;   // 苹果园政策文件(仅语义路)

    @Test
    @Order(1)
    void 推送即补算embedding() throws Exception {
        seed();
        Long embedded = jdbcTemplate.queryForObject(
                "SELECT count(*) FROM documents WHERE embedding IS NOT NULL", Long.class);
        assertThat(embedded).isEqualTo(3);
    }

    @Test
    @Order(2)
    void 纯语义_相似文档在前_顺序与断言() throws Exception {
        seed();
        MvcResult result = mockMvc.perform(get(docsUri("semantic", "苹果"))
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(3))
                .andReturn();
        List<String> ids = itemIds(result);
        assertThat(ids.get(2)).isEqualTo(docY);                       // 香蕉与查询相似度 0,垫底
        assertThat(ids.subList(0, 2)).containsExactlyInAnyOrder(docX, docZ);
    }

    @Test
    @Order(3)
    void keyword与semantic同给_RRF融合_双命中排最前() throws Exception {
        seed();
        MvcResult result = mockMvc.perform(get(docsUri("keyword", "招标", "semantic", "苹果"))
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(3))
                .andReturn();
        // X 两路都进(融合分最高);Y 关键词 rank2 + 语义垫底;Z 仅语义路
        assertThat(itemIds(result)).containsExactly(docX, docY, docZ);
    }

    @Test
    @Order(4)
    void 无embedding的文档不进语义结果_不报错() throws Exception {
        seed();
        jdbcTemplate.update("UPDATE documents SET embedding = NULL WHERE id = ?", docX);
        MvcResult result = mockMvc.perform(get(docsUri("semantic", "苹果"))
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(2))
                .andReturn();
        assertThat(itemIds(result)).containsExactlyInAnyOrder(docZ, docY);
    }

    @Test
    @Order(5)
    void 端点失败_降级关键词兜底_不报错() throws Exception {
        failMode.set(true);
        // semantic + keyword 同给:查询向量拿不到 → 纯关键词结果(含被清空 embedding 的 X)
        mockMvc.perform(get(docsUri("semantic", "香蕉", "keyword", "招标"))
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(2));
        // 仅有 semantic:语义文本按关键词兜底(标题/正文含"苹果"的 X、Z)
        MvcResult result = mockMvc.perform(get(docsUri("semantic", "苹果"))
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(2))
                .andReturn();
        assertThat(itemIds(result)).containsExactlyInAnyOrder(docX, docZ);
        failMode.set(false);
    }

    // ---------- 种子与工具 ----------

    /** 幂等种子:三篇文档只在库里不存在时推送(每个测试方法独立上下文内可重复调用)。 */
    void seed() throws Exception {
        if (docX != null) {
            return;
        }
        String suffix = String.valueOf(System.nanoTime());
        String sourceId = createSource(suffix);
        String body = """
                {"source_id":"%s","items":[
                  {"url":"http://semantic.example/x/%s","doc_type":"announcement","title":"苹果招标公告",
                   "org_name":"语义测试医院","amount":"300.00","publish_date":"2026-09-20",
                   "category":"语义测试类","content_text":"苹果品类集中招标"},
                  {"url":"http://semantic.example/y/%s","doc_type":"announcement","title":"香蕉招标公告",
                   "org_name":"语义测试医院","amount":"150.00","publish_date":"2026-09-10",
                   "category":"语义测试类","content_text":"香蕉品类集中招标"},
                  {"url":"http://semantic.example/z/%s","doc_type":"policy","title":"苹果园政策文件",
                   "org_name":"语义测试医院","publish_date":"2026-08-01",
                   "category":"语义测试类","content_text":"苹果园种植补贴政策"}
                ]}
                """.formatted(sourceId, suffix, suffix, suffix);
        mockMvc.perform(post("/api/v1/internal/documents")
                        .header("X-Internal-Key", "test-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.accepted").value(3));
        docX = docId("苹果招标公告");
        docY = docId("香蕉招标公告");
        docZ = docId("苹果园政策文件");
    }

    private String createSource(String suffix) throws Exception {
        String token = login();
        MvcResult result = mockMvc.perform(post("/api/v1/sources")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"语义源-%s\",\"type\":\"web_page\",\"url\":\"http://semantic.example/%s\"}"
                                .formatted(suffix, suffix)))
                .andExpect(status().isCreated())
                .andReturn();
        return objectMapper.readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("id").asText();
    }

    private String docId(String title) {
        return jdbcTemplate.queryForObject("SELECT id FROM documents WHERE title = ?", String.class, title);
    }

    private List<String> itemIds(MvcResult result) throws Exception {
        JsonNode items = objectMapper.readTree(
                result.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("items");
        List<String> ids = new ArrayList<>();
        items.forEach(i -> ids.add(i.path("id").asText()));
        return ids;
    }

    private String login() throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/auth/token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"admin@sense2act.local\",\"password\":\"admin123\"}"))
                .andExpect(status().isOk())
                .andReturn();
        return objectMapper.readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("token").asText();
    }

    private static URI docsUri(String... pairs) {
        UriComponentsBuilder b = UriComponentsBuilder.fromUriString("/api/v1/documents");
        for (int i = 0; i < pairs.length; i += 2) {
            b.queryParam(pairs[i], pairs[i + 1]);
        }
        return b.build().encode().toUri();
    }
}
