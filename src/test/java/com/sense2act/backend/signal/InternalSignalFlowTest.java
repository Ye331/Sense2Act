package com.sense2act.backend.signal;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.common.events.SignalCreatedEvent;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.org.Organization;
import com.sense2act.backend.domain.org.OrganizationMapper;
import com.sense2act.backend.domain.org.OrgStatsMapper;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.context.event.EventListener;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E2-1 / E2-2 / E2-4 验收:检测队列一次给齐输入、limit 闸门(D9)、扫描标记幂等、
 * 信号推送幂等(同 document+detector+hits,键序无关)、score 原样落库、
 * 超阈值自动开调查(created,带策略默认值)、signal_created 事件仅在新建时发布。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class InternalSignalFlowTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    /** 捕获提交后发布的 SignalCreatedEvent,断言"仅新建发一次"。 */
    @TestConfiguration(proxyBeanMethods = false)
    static class Capture {
        @Bean
        SignalEventRecorder signalEventRecorder() {
            return new SignalEventRecorder();
        }
    }

    static class SignalEventRecorder {
        final List<SignalCreatedEvent> events = new ArrayList<>();

        @EventListener
        void on(SignalCreatedEvent e) {
            events.add(e);
        }
    }

    @Autowired
    SignalEventRecorder recorder;

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    JdbcTemplate jdbcTemplate;

    @Autowired
    DocumentMapper documentMapper;

    @Autowired
    OrganizationMapper organizationMapper;

    @Autowired
    OrgStatsMapper orgStatsMapper;

    // ---------- 种子:1 机构 + 3 文档(带 org_stats 基线) ----------

    String orgA;
    String docA;   // 有机构有类别(基线可嵌)
    String docB;   // 无机构
    String docC;   // 机构×类别无基线行

    void seed() {
        if (orgA != null) {
            return;
        }
        String suffix = String.valueOf(System.nanoTime());
        Organization org = new Organization();
        org.setId(IdGen.next("org"));
        org.setName("信号测试医院" + suffix);
        org.setAliases(List.of());
        organizationMapper.insert(org);
        orgA = org.getId();

        docA = insertDoc("sig-a-" + suffix, orgA, "医疗信息化", "3250000.00", "2026-09-24");
        docB = insertDoc("sig-b-" + suffix, null, null, null, "2026-09-20");
        docC = insertDoc("sig-c-" + suffix, orgA, "不存在的类别", "100.00", "2026-09-18");

        orgStatsMapper.recompute(orgA, "医疗信息化");   // docA 的基线
    }

    private String insertDoc(String key, String orgId, String category, String amount, String publishDate) {
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType("announcement");
        d.setTitle("信号链路文档-" + key);
        d.setContentText("正文-" + key);
        d.setOrgId(orgId);
        d.setAmount(amount == null ? null : new BigDecimal(amount));
        d.setPublishDate(LocalDate.parse(publishDate));
        d.setRegion("华东/某省某市");
        d.setCategory(category);
        d.setUrl("http://signal-flow.example/" + key);
        d.setRaw(Map.of("k", "v"));
        d.setContentHash("hash-" + key);
        d.setSignalScanned(false);
        documentMapper.insert(d);
        return d.getId();
    }

    @Test
    @Order(1)
    void 检测队列_一次给齐文档基线画像_limit闸门() throws Exception {
        seed();
        MvcResult result = mockMvc.perform(get("/api/v1/internal/detection-queue")
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andReturn();
        JsonNode items = objectMapper.readTree(
                result.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("items");
        assertThat(items.size()).isEqualTo(3);
        JsonNode first = items.get(0);
        assertThat(first.path("document").path("id").asText()).isEqualTo(docA);
        assertThat(first.path("document").path("org_name").asText()).startsWith("信号测试医院");
        assertThat(first.path("document").path("content_text").asText()).startsWith("正文-");
        assertThat(first.path("org_stats").path("category").asText()).isEqualTo("医疗信息化");
        assertThat(first.path("org_stats").path("sample_count").asInt()).isEqualTo(1);
        assertThat(first.path("profiles").toString()).contains("医疗AI", "数据平台");   // V3 种子画像
        // docB 无机构 / docC 机构×类别无基线行:org_stats 序列化为省略(全局 non_null),不报错
        assertThat(items.get(1).has("org_stats")).isFalse();
        assertThat(items.get(2).has("org_stats")).isFalse();

        mockMvc.perform(get("/api/v1/internal/detection-queue?limit=1").header("X-Internal-Key", "test-key"))
                .andExpect(jsonPath("$.data.items.length()").value(1));
        for (String bad : new String[]{"0", "101", "-1"}) {
            mockMvc.perform(get("/api/v1/internal/detection-queue?limit=" + bad).header("X-Internal-Key", "test-key"))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value(40001));
        }
    }

    @Test
    @Order(2)
    void 扫描标记_批量置位且幂等_队列排除已扫() throws Exception {
        seed();
        String body = "{\"document_ids\":[\"%s\",\"%s\"],\"detector\":\"t\"}".formatted(docA, docB);
        mockMvc.perform(post("/api/v1/internal/detection-scan-complete")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.updated").value(2));
        mockMvc.perform(post("/api/v1/internal/detection-scan-complete")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.updated").value(0));
        mockMvc.perform(get("/api/v1/internal/detection-queue").header("X-Internal-Key", "test-key"))
                .andExpect(jsonPath("$.data.items.length()").value(1))
                .andExpect(jsonPath("$.data.items[0].document.id").value(docC));
        mockMvc.perform(post("/api/v1/internal/detection-scan-complete")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"document_ids\":[],\"detector\":\"t\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
    }

    private static final String HITS_A = """
            [{"rule_type":"amount_anomaly","rule_id":null,"rule_version":null,"weight":0.6,
              "detail":{"z":5.9,"baseline_mean":820000}}]""";

    @Test
    @Order(3)
    void 推送低分信号_pending不触发调查_幂等键序无关() throws Exception {
        seed();
        MvcResult first = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"document\":{\"id\":\"%s\"},\"hits\":%s,\"score\":0.42,\"detector\":\"detector-a\"}"
                                .formatted(docA, HITS_A)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("pending"))
                .andExpect(jsonPath("$.data.auto_investigated").value(false))
                .andReturn();
        String signalId = objectMapper.readTree(
                first.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("signal_id").asText();
        assertThat(recorder.events).hasSize(1);   // 新建即发一次
        // 同内容键序不同(键集相同)→ 同一条信号(jsonb 相等忽略键序),不再发事件
        String reordered = """
                [{"weight":0.6,"detail":{"baseline_mean":820000,"z":5.9},"rule_version":null,"rule_id":null,"rule_type":"amount_anomaly"}]""";
        mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"document\":{\"id\":\"%s\"},\"hits\":%s,\"score\":0.42,\"detector\":\"detector-a\"}"
                                .formatted(docA, reordered)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.signal_id").value(signalId))
                .andExpect(jsonPath("$.data.status").value("pending"));
        assertThat(recorder.events).hasSize(1);
        // 不同 detector → 新信号(幂等键含 detector),再发一次事件
        mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"document\":{\"id\":\"%s\"},\"hits\":%s,\"score\":0.42,\"detector\":\"detector-b\"}"
                                .formatted(docA, HITS_A)))
                .andExpect(jsonPath("$.data.signal_id").value(org.hamcrest.Matchers.not(signalId)));
        assertThat(recorder.events).hasSize(2);
        // score 原样落库(0.42,后端不重算)
        BigDecimal score = jdbcTemplate.queryForObject(
                "SELECT score FROM signals WHERE id = ?", BigDecimal.class, signalId);
        assertThat(score).isEqualByComparingTo("0.42");
    }

    @Test
    @Order(4)
    void 推送高分信号_超阈值自动开调查() throws Exception {
        seed();
        MvcResult result = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"document\":{\"id\":\"%s\"},\"hits\":%s,\"score\":0.95,\"detector\":\"detector-a\"}"
                                .formatted(docC, HITS_A)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("investigating"))
                .andExpect(jsonPath("$.data.auto_investigated").value(true))
                .andReturn();
        String signalId = objectMapper.readTree(
                result.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("signal_id").asText();
        assertThat(recorder.events).hasSize(3);
        // E2-4:调查 created + 策略默认轮次/预算
        Map<String, Object> inv = jdbcTemplate.queryForMap(
                "SELECT status, max_rounds, token_budget FROM investigations WHERE signal_id = ?", signalId);
        assertThat(inv.get("status")).isEqualTo("created");
        assertThat(((Number) inv.get("max_rounds")).intValue()).isEqualTo(8);
        assertThat(((Number) inv.get("token_budget")).intValue()).isEqualTo(60000);
        String status = jdbcTemplate.queryForObject(
                "SELECT status FROM signals WHERE id = ?", String.class, signalId);
        assertThat(status).isEqualTo("investigating");
    }

    @Test
    @Order(5)
    void 推送参数校验_40001_40401() throws Exception {
        seed();
        for (String body : new String[]{
                "{\"document\":{\"id\":\"%s\"},\"hits\":[],\"score\":0.5,\"detector\":\"d\"}".formatted(docA),
                "{\"document\":{\"id\":\"%s\"},\"hits\":%s,\"score\":1.5,\"detector\":\"d\"}".formatted(docA, HITS_A),
                "{\"document\":{\"id\":\"%s\"},\"hits\":%s,\"score\":0.5,\"detector\":\"\"}".formatted(docA, HITS_A)}) {
            mockMvc.perform(post("/api/v1/internal/signals")
                            .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON).content(body))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value(40001));
        }
        mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"document\":{\"id\":\"doc_NOPE\"},\"hits\":%s,\"score\":0.5,\"detector\":\"d\"}"
                                .formatted(HITS_A)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }
}
