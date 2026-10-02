package com.sense2act.backend.policy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
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
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E2-3 验收:策略单行读改(V3 种子默认值)、全量 PUT 校验、
 * 阈值改动对推送自动触发即时生效;关注画像(D13)admin 专属 CRUD
 * 且新增/删除实时反映到 detection-queue 的内嵌 profiles。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class PolicyApiTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    JdbcTemplate jdbcTemplate;

    @Autowired
    DocumentMapper documentMapper;

    String docId;      // 供阈值联动推送;signal_scanned=false 保证队列非空
    String admin;

    void seed() {
        if (docId != null) {
            return;
        }
        String suffix = String.valueOf(System.nanoTime());
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType("announcement");
        d.setTitle("策略联动文档-" + suffix);
        d.setContentText("正文-" + suffix);
        d.setOrgId(null);
        d.setAmount(null);
        d.setPublishDate(LocalDate.parse("2026-09-22"));
        d.setRegion("华东");
        d.setCategory("医疗信息化");
        d.setUrl("http://policy-api.example/" + suffix);
        d.setRaw(Map.of("k", "v"));
        d.setContentHash("hash-" + suffix);
        d.setSignalScanned(false);
        documentMapper.insert(d);
        docId = d.getId();
    }

    @Test
    @Order(1)
    void 策略默认值与管理面权限() throws Exception {
        seed();
        admin = login("admin@sense2act.local", "admin123");
        mockMvc.perform(get("/api/v1/admin/investigation-policies").header("Authorization", "Bearer " + admin))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.auto_investigate_threshold").value(0.85))
                .andExpect(jsonPath("$.data.max_concurrent_investigations").value(3))
                .andExpect(jsonPath("$.data.default_max_rounds").value(8))
                .andExpect(jsonPath("$.data.default_token_budget").value(60000))
                .andExpect(jsonPath("$.data.updated_at").exists());
        mockMvc.perform(get("/api/v1/admin/investigation-policies"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
        for (String who : new String[]{"analyst@sense2act.local|analyst123", "viewer@sense2act.local|viewer123"}) {
            mockMvc.perform(get("/api/v1/admin/investigation-policies").header("Authorization",
                            "Bearer " + login(who.split("\\|")[0], who.split("\\|")[1])))
                    .andExpect(status().isForbidden())
                    .andExpect(jsonPath("$.code").value(40301));
        }
    }

    @Test
    @Order(2)
    void 策略全量PUT_生效() throws Exception {
        seed();
        String token = "Bearer " + login("admin@sense2act.local", "admin123");
        mockMvc.perform(put("/api/v1/admin/investigation-policies")
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"auto_investigate_threshold\":0.90,\"max_concurrent_investigations\":5,"
                                + "\"default_max_rounds\":10,\"default_token_budget\":80000}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.auto_investigate_threshold").value(0.90))
                .andExpect(jsonPath("$.data.max_concurrent_investigations").value(5))
                .andExpect(jsonPath("$.data.default_max_rounds").value(10))
                .andExpect(jsonPath("$.data.default_token_budget").value(80000));
        mockMvc.perform(get("/api/v1/admin/investigation-policies").header("Authorization", token))
                .andExpect(jsonPath("$.data.default_token_budget").value(80000));   // 持久化
    }

    @Test
    @Order(3)
    void 策略PUT校验_越界与缺字段() throws Exception {
        seed();
        String token = "Bearer " + login("admin@sense2act.local", "admin123");
        for (String body : new String[]{
                "{\"auto_investigate_threshold\":1.5,\"max_concurrent_investigations\":3,\"default_max_rounds\":8,\"default_token_budget\":60000}",
                "{\"auto_investigate_threshold\":-0.1,\"max_concurrent_investigations\":3,\"default_max_rounds\":8,\"default_token_budget\":60000}",
                "{\"auto_investigate_threshold\":0.8,\"max_concurrent_investigations\":0,\"default_max_rounds\":8,\"default_token_budget\":60000}",
                "{\"auto_investigate_threshold\":0.8,\"max_concurrent_investigations\":3,\"default_token_budget\":60000}"}) {
            mockMvc.perform(put("/api/v1/admin/investigation-policies")
                            .header("Authorization", token).contentType(MediaType.APPLICATION_JSON).content(body))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value(40001));
        }
    }

    @Test
    @Order(4)
    void 阈值下调后_低分信号也自动开调查() throws Exception {
        seed();
        String token = "Bearer " + login("admin@sense2act.local", "admin123");
        mockMvc.perform(put("/api/v1/admin/investigation-policies")
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"auto_investigate_threshold\":0.5,\"max_concurrent_investigations\":3,"
                                + "\"default_max_rounds\":8,\"default_token_budget\":60000}"))
                .andExpect(status().isOk());
        // 0.60 在旧阈值 0.85 之下、新阈值 0.5 之上 → 即时生效自动触发
        MvcResult r = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"document\":{\"id\":\"%s\"},\"hits\":[{\"rule_type\":\"semantic_match\",\"weight\":1.0}],\"score\":0.60,\"detector\":\"policy-test\"}"
                                .formatted(docId)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("investigating"))
                .andExpect(jsonPath("$.data.auto_investigated").value(true))
                .andReturn();
        String signalId = objectMapper.readTree(
                r.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("signal_id").asText();
        Integer count = jdbcTemplate.queryForObject(
                "SELECT count(*) FROM investigations WHERE signal_id = ?", Integer.class, signalId);
        assertThat(count).isEqualTo(1);
    }

    private static final String NEW_PROFILE = "政策测试画像-" + System.nanoTime();

    @Test
    @Order(5)
    void 画像_种子可见_新增即入检测队列() throws Exception {
        seed();
        String token = "Bearer " + login("admin@sense2act.local", "admin123");
        MvcResult r = mockMvc.perform(get("/api/v1/admin/watch-profiles").header("Authorization", token))
                .andExpect(status().isOk())
                .andReturn();
        String list = objectMapper.readTree(
                r.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").toString();
        assertThat(list).contains("医疗AI", "数据平台", "医疗网络安全");   // V3 三个种子画像
        mockMvc.perform(post("/api/v1/admin/watch-profiles")
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"%s\",\"note\":\"E2-3 验收用\"}".formatted(NEW_PROFILE)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").exists())
                .andExpect(jsonPath("$.data.name").value(NEW_PROFILE))
                .andExpect(jsonPath("$.data.enabled").value(true))
                .andExpect(jsonPath("$.data.created_at").exists());
        // detection-queue 内嵌启用画像(队列非空:seed 文档未扫描)
        MvcResult q = mockMvc.perform(get("/api/v1/internal/detection-queue")
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andReturn();
        String profiles = objectMapper.readTree(q.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("items").get(0).path("profiles").toString();
        assertThat(profiles).contains(NEW_PROFILE);
    }

    @Test
    @Order(6)
    void 画像_重名40901_删除即出队列_再删40401() throws Exception {
        seed();
        String token = "Bearer " + login("admin@sense2act.local", "admin123");
        mockMvc.perform(post("/api/v1/admin/watch-profiles")
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"医疗AI\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        String id = jdbcTemplate.queryForObject(
                "SELECT id FROM watch_profiles WHERE name = ?", String.class, NEW_PROFILE);
        mockMvc.perform(delete("/api/v1/admin/watch-profiles/{id}", id).header("Authorization", token))
                .andExpect(status().isOk());
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM watch_profiles WHERE id = ?", Integer.class, id)).isZero();
        MvcResult q = mockMvc.perform(get("/api/v1/internal/detection-queue")
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andReturn();
        String profiles = objectMapper.readTree(q.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("items").get(0).path("profiles").toString();
        assertThat(profiles).doesNotContain(NEW_PROFILE);
        mockMvc.perform(delete("/api/v1/admin/watch-profiles/{id}", id).header("Authorization", token))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
        // 空名校验
        mockMvc.perform(post("/api/v1/admin/watch-profiles")
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\" \"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
    }

    @Test
    @Order(7)
    void 画像_管理面仅admin可写() throws Exception {
        seed();
        String analyst = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(post("/api/v1/admin/watch-profiles")
                        .header("Authorization", analyst).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"不应成功\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value(40301));
        mockMvc.perform(get("/api/v1/admin/watch-profiles").header("Authorization", analyst))
                .andExpect(status().isForbidden());   // /admin/** 整组仅 admin(GET 也是)
    }

    // ---------- 工具 ----------

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
