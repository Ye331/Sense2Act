package com.sense2act.backend.investigation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.investigation.Evidence;
import com.sense2act.backend.domain.investigation.EvidenceMapper;
import com.sense2act.backend.domain.investigation.InvestigationService;
import com.sense2act.backend.domain.org.Organization;
import com.sense2act.backend.domain.org.OrganizationMapper;
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

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E3 验收(除 SSE 长连接外):领取 FIFO 与 CAS/D14 并发闸、context 一次给齐、
 * steps 留痕(seq 单调、事件名词表、轮次/预算 42201、budget_update 派生、cost_estimate 折算)、
 * questions 状态与证据引用校验、stop 状态机与信号回退、created TTL 清理(D7)、用户侧查询与权限。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class InternalInvestigationFlowTest {

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

    @Autowired
    OrganizationMapper organizationMapper;

    @Autowired
    EvidenceMapper evidenceMapper;

    @Autowired
    InvestigationService investigationService;

    String orgA;
    String sigA;   // → invA:全流程(start/context/steps/questions/stop)
    String sigB;   // → invB:D14 挡 + TTL 清理
    String invA;
    String invB;

    void seed() throws Exception {
        if (orgA != null) {
            return;
        }
        String suffix = String.valueOf(System.nanoTime());
        Organization org = new Organization();
        org.setId(IdGen.next("org"));
        org.setName("调查测试医院" + suffix);
        org.setAliases(List.of());
        organizationMapper.insert(org);
        orgA = org.getId();

        String docA = insertDoc("inv-a-" + suffix, orgA, "3250000.00");
        String docB = insertDoc("inv-b-" + suffix, orgA, "980000.00");
        sigA = push(docA, "0.60");
        sigB = push(docB, "0.55");
        // 手动开调查:pending → created(invA 先建,间隔确保 created_at 可比)
        invA = investigate(sigA);
        Thread.sleep(30);
        invB = investigate(sigB);
    }

    private String insertDoc(String key, String orgId, String amount) {
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType("announcement");
        d.setTitle("调查链路文档-" + key);
        d.setContentText("某医院拟采购 AI 辅助诊断系统,预算 " + amount + " 元,正文-" + key);
        d.setOrgId(orgId);
        d.setAmount(new BigDecimal(amount));
        d.setPublishDate(LocalDate.parse("2026-09-25"));
        d.setRegion("华东/某省某市");
        d.setCategory("医疗信息化");
        d.setUrl("http://inv-flow.example/" + key);
        d.setRaw(Map.of("k", "v"));
        d.setContentHash("hash-" + key);
        d.setSignalScanned(true);
        documentMapper.insert(d);
        return d.getId();
    }

    private String push(String docId, String score) throws Exception {
        // 注意括号:.formatted 必须作用于整个拼接串,只吃最后一个字面量会让 %s 原样进请求体
        MvcResult r = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"document\":{\"id\":\"%s\"},\"score\":%s,\"detector\":\"det\","
                                + "\"hits\":[{\"rule_type\":\"amount_anomaly\",\"weight\":0.6,\"detail\":{\"z\":5.1}}]}")
                                .formatted(docId, score)))
                .andExpect(status().isOk())
                .andReturn();
        return objectMapper.readTree(r.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("signal_id").asText();
    }

    private String investigate(String signalId) throws Exception {
        MvcResult r = mockMvc.perform(post("/api/v1/signals/{id}/investigate", signalId)
                        .header("Authorization", "Bearer " + login("analyst@sense2act.local", "analyst123")))
                .andExpect(status().isOk())
                .andReturn();
        return objectMapper.readTree(r.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("investigation_id").asText();
    }

    private void postStep(String invId, String body) throws Exception {
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invId)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk());
    }

    @Test
    @Order(1)
    void 领取列表_created按时间正序_参数与鉴权() throws Exception {
        seed();
        MvcResult r = mockMvc.perform(get("/api/v1/internal/investigations?status=created")
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.items.length()").value(2))
                .andReturn();
        JsonNode items = objectMapper.readTree(
                r.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("items");
        assertThat(items.get(0).path("id").asText()).isEqualTo(invA);   // FIFO:先建先领
        assertThat(items.get(1).path("id").asText()).isEqualTo(invB);
        assertThat(items.get(0).path("signal_id").asText()).isEqualTo(sigA);
        assertThat(items.get(0).has("started_at")).isFalse();          // 未领取,全局 non_null 省略
        mockMvc.perform(get("/api/v1/internal/investigations?limit=1").header("X-Internal-Key", "test-key"))
                .andExpect(jsonPath("$.data.items.length()").value(1));
        mockMvc.perform(get("/api/v1/internal/investigations?status=nope").header("X-Internal-Key", "test-key"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(get("/api/v1/internal/investigations?limit=0").header("X-Internal-Key", "test-key"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(get("/api/v1/internal/investigations"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
    }

    @Test
    @Order(2)
    void start_CAS领取_D14并发闸_investigation_started留痕() throws Exception {
        seed();
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/start", invA)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("investigating"))
                .andExpect(jsonPath("$.data.started_at").exists());
        // 再领 → 40901(CAS)
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/start", invA)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        // investigation_started 已作为步骤留痕(seq=1,可 SSE 补发)
        Map<String, Object> step = jdbcTemplate.queryForMap(
                "SELECT seq, type, content->>'event' AS event FROM investigation_steps WHERE investigation_id = ?",
                invA);
        assertThat(((Number) step.get("seq")).intValue()).isEqualTo(1);
        assertThat(step.get("type")).isEqualTo("status_change");
        assertThat(step.get("event")).isEqualTo("investigation_started");
        // 未 start 的调查不可写步骤(40901)
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invB)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"round_started\",\"round\":1}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        // D14:并发上限挡在 start —— 收紧到 1(invA 在查)后 invB 领取被拒
        String admin = login("admin@sense2act.local", "admin123");
        mockMvc.perform(put("/api/v1/admin/investigation-policies")
                        .header("Authorization", "Bearer " + admin).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"auto_investigate_threshold\":0.85,\"max_concurrent_investigations\":1,"
                                + "\"default_max_rounds\":8,\"default_token_budget\":60000}"))
                .andExpect(status().isOk());
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/start", invB)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        // created 状态不可 stop(40901),随后恢复策略
        mockMvc.perform(post("/api/v1/investigations/{id}/stop", invB)
                        .header("Authorization", "Bearer " + admin))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        mockMvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .put("/api/v1/admin/investigation-policies")
                        .header("Authorization", "Bearer " + admin).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"auto_investigate_threshold\":0.85,\"max_concurrent_investigations\":3,"
                                + "\"default_max_rounds\":8,\"default_token_budget\":60000}"))
                .andExpect(status().isOk());
    }

    @Test
    @Order(3)
    void context_一次给齐信号文档问题预算() throws Exception {
        seed();
        mockMvc.perform(get("/api/v1/internal/investigations/{id}/context", invA)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.investigation.id").value(invA))
                .andExpect(jsonPath("$.data.investigation.status").value("investigating"))
                .andExpect(jsonPath("$.data.signal.id").value(sigA))
                .andExpect(jsonPath("$.data.signal.hits[0].rule_type").value("amount_anomaly"))
                .andExpect(jsonPath("$.data.signal.score").value("0.600"))
                .andExpect(jsonPath("$.data.signal.document.title").exists())
                .andExpect(jsonPath("$.data.signal.document.content_text").exists())
                .andExpect(jsonPath("$.data.signal.document.org_name").exists())
                .andExpect(jsonPath("$.data.signal.document.amount").value("3250000.00"))
                .andExpect(jsonPath("$.data.open_questions.length()").value(0))
                .andExpect(jsonPath("$.data.evidences.length()").value(0))
                .andExpect(jsonPath("$.data.budget_remaining").value(60000));
        mockMvc.perform(get("/api/v1/internal/investigations/inv_NOPE/context")
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }

    @Test
    @Order(4)
    void steps留痕_seq单调_budget派生_用户侧游标分页() throws Exception {
        seed();
        postStep(invA, "{\"event\":\"round_started\",\"round\":1,\"payload\":{\"round\":1}}");
        postStep(invA, "{\"event\":\"questions_generated\",\"round\":1,\"payload\":{\"questions\":"
                + "[\"近半年该机构同类采购金额?\",\"预算是否显著高于基线?\"]}}");
        mockMvc.perform(post("/api/v1/internal/questions")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"investigation_id\":\"%s\",\"text\":\"近半年该机构同类采购金额?\",\"raised_in_round\":1}"
                                .formatted(invA)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("open"));
        mockMvc.perform(post("/api/v1/internal/questions")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"investigation_id\":\"%s\",\"text\":\"预算是否显著高于基线?\",\"raised_in_round\":1}"
                                .formatted(invA)))
                .andExpect(status().isOk());
        postStep(invA, "{\"event\":\"tool_selected\",\"round\":1,\"payload\":"
                + "{\"tool\":\"doc_search\",\"args\":{\"keyword\":\"采购\"}}}");
        postStep(invA, "{\"event\":\"tool_completed\",\"round\":1,\"payload\":"
                + "{\"tool\":\"doc_search\",\"ok\":true,\"latency_ms\":120,\"result_summary\":\"命中 3 篇\"},"
                + "\"token_usage\":1500}");
        // 步骤总数:start + 4 个上报 + 派生 budget_update = 6,seq 连续
        Integer count = jdbcTemplate.queryForObject(
                "SELECT count(*) FROM investigation_steps WHERE investigation_id = ?", Integer.class, invA);
        assertThat(count).isEqualTo(6);
        Map<String, Object> budget = jdbcTemplate.queryForMap(
                "SELECT seq, content->>'event' AS event, content->>'token_used' AS used "
                        + "FROM investigation_steps WHERE investigation_id = ? AND content->>'event' = 'budget_update'",
                invA);
        assertThat(((Number) budget.get("seq")).intValue()).isEqualTo(6);
        assertThat(budget.get("used")).isEqualTo("1500");
        Map<String, Object> inv = jdbcTemplate.queryForMap(
                "SELECT token_used, cost_estimate, current_round FROM investigations WHERE id = ?", invA);
        assertThat(((Number) inv.get("token_used")).intValue()).isEqualTo(1500);
        assertThat((BigDecimal) inv.get("cost_estimate")).isEqualByComparingTo(new BigDecimal("0.045"));   // 1500×0.00003
        assertThat(((Number) inv.get("current_round")).intValue()).isEqualTo(1);
        // 用户侧游标分页:limit=3 翻两页读完,next_cursor 归空
        mockMvc.perform(get("/api/v1/investigations/{id}/steps?limit=3", invA)
                        .header("Authorization", "Bearer " + login("viewer@sense2act.local", "viewer123")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.items.length()").value(3))
                .andExpect(jsonPath("$.data.items[0].seq").value(1))
                .andExpect(jsonPath("$.data.items[0].content.event").value("investigation_started"))
                .andExpect(jsonPath("$.data.items[0].content.investigation_id").value(invA))
                .andExpect(jsonPath("$.data.items[2].seq").value(3))
                .andExpect(jsonPath("$.data.next_cursor").value(3));
        mockMvc.perform(get("/api/v1/investigations/{id}/steps?cursor=3&limit=3", invA)
                        .header("Authorization", "Bearer " + login("viewer@sense2act.local", "viewer123")))
                .andExpect(jsonPath("$.data.items.length()").value(3))
                .andExpect(jsonPath("$.data.items[0].seq").value(4))
                .andExpect(jsonPath("$.data.items[2].content.event").value("budget_update"))
                .andExpect(jsonPath("$.data.next_cursor").doesNotExist());
        // 事件名与轮次闸门
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"nope_event\",\"round\":1}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"budget_update\",\"round\":1}"))   // 派生事件不可直报
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"round_started\",\"round\":9}"))   // > max_rounds 8
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value(42201));
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"round_started\",\"round\":0}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
    }

    @Test
    @Order(5)
    void 预算边界_恰等于可通过_超出42201不再累计() throws Exception {
        seed();
        // 1500 + 58500 = 60000,恰好用满,允许
        postStep(invA, "{\"event\":\"tool_completed\",\"round\":2,\"payload\":"
                + "{\"tool\":\"org_stats\",\"ok\":true},\"token_usage\":58500}");
        Map<String, Object> inv = jdbcTemplate.queryForMap(
                "SELECT token_used, cost_estimate FROM investigations WHERE id = ?", invA);
        assertThat(((Number) inv.get("token_used")).intValue()).isEqualTo(60000);
        assertThat((BigDecimal) inv.get("cost_estimate")).isEqualByComparingTo(new BigDecimal("1.8"));
        // 再写 1 个 token → 42201,token_used 不动
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"reflection_updated\",\"round\":2,\"payload\":{},\"token_usage\":1}"))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value(42201));
        assertThat(jdbcTemplate.queryForObject(
                "SELECT token_used FROM investigations WHERE id = ?", Integer.class, invA)).isEqualTo(60000);
    }

    @Test
    @Order(6)
    void questions_PATCH状态与证据引用() throws Exception {
        seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        MvcResult d = mockMvc.perform(get("/api/v1/investigations/{id}", invA)
                        .header("Authorization", token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.investigation.id").value(invA))
                .andExpect(jsonPath("$.data.questions.length()").value(2))
                .andExpect(jsonPath("$.data.questions[0].status").value("open"))
                .andReturn();
        String q1 = objectMapper.readTree(d.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("questions").get(0).path("id").asText();
        // 未登记证据 → 42201
        mockMvc.perform(patch("/api/v1/internal/questions/{id}", q1)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"clarified\",\"answer_summary\":\"近半年均值 82 万,本次 325 万显著偏高\","
                                + "\"evidence_ids\":[\"ev_NOPE\"]}"))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value(42201));
        // 登记一条证据(E4 前直接落库)后再引用 → 通过
        Evidence ev = new Evidence();
        ev.setId(IdGen.next("ev"));
        ev.setSourceType("announcement");
        ev.setTitle("历史采购公告");
        ev.setUrl("http://inv-flow.example/history");
        ev.setExcerpt("近半年同类项目均值 82 万");
        ev.setInvestigationId(invA);
        evidenceMapper.insert(ev);
        mockMvc.perform(patch("/api/v1/internal/questions/{id}", q1)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"clarified\",\"answer_summary\":\"显著高于基线\","
                                + "\"evidence_ids\":[\"%s\"]}".formatted(ev.getId())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("clarified"))
                .andExpect(jsonPath("$.data.evidence_ids[0]").value(ev.getId()));
        mockMvc.perform(patch("/api/v1/internal/questions/{id}", q1)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"bogus\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(patch("/api/v1/internal/questions/{id}", "q_NOPE")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"open\"}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
        // context 只剩 1 个 open,证据摘要可见
        mockMvc.perform(get("/api/v1/internal/investigations/{id}/context", invA)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(jsonPath("$.data.open_questions.length()").value(1))
                .andExpect(jsonPath("$.data.evidences.length()").value(1))
                .andExpect(jsonPath("$.data.evidences[0].id").value(ev.getId()))
                .andExpect(jsonPath("$.data.budget_remaining").value(0));
    }

    @Test
    @Order(7)
    void stop_状态机与信号回pending() throws Exception {
        seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(post("/api/v1/investigations/{id}/stop", invA)
                        .header("Authorization", token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("stopped"))
                .andExpect(jsonPath("$.data.finished_at").exists());
        String event = jdbcTemplate.queryForObject(
                "SELECT content->>'event' FROM investigation_steps WHERE investigation_id = ? "
                        + "AND content->>'event' = 'investigation_stopped'", String.class, invA);
        assertThat(event).isEqualTo("investigation_stopped");
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM signals WHERE id = ?", String.class, sigA)).isEqualTo("pending");
        // 终态:再 stop 40901,写步骤 40901
        mockMvc.perform(post("/api/v1/investigations/{id}/stop", invA)
                        .header("Authorization", token))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"round_started\",\"round\":3}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
    }

    @Test
    @Order(8)
    void TTL清理_created超时置failed_信号回pending() throws Exception {
        seed();
        jdbcTemplate.update("UPDATE investigations SET created_at = now() - interval '2 hours' WHERE id = ?", invB);
        investigationService.sweepExpiredCreated();
        Map<String, Object> inv = jdbcTemplate.queryForMap(
                "SELECT status, error, finished_at FROM investigations WHERE id = ?", invB);
        assertThat(inv.get("status")).isEqualTo("failed");
        assertThat(String.valueOf(inv.get("error"))).contains("TTL");
        assertThat(inv.get("finished_at")).isNotNull();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM signals WHERE id = ?", String.class, sigB)).isEqualTo("pending");
        String event = jdbcTemplate.queryForObject(
                "SELECT content->>'event' FROM investigation_steps WHERE investigation_id = ? "
                        + "AND content->>'event' = 'investigation_failed'", String.class, invB);
        assertThat(event).isEqualTo("investigation_failed");
        // 幂等:再扫一次状态不变,也不重复留痕
        investigationService.sweepExpiredCreated();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM investigation_steps WHERE investigation_id = ? "
                        + "AND content->>'event' = 'investigation_failed'", Integer.class, invB)).isEqualTo(1);
    }

    @Test
    @Order(9)
    void 用户侧列表筛选与权限() throws Exception {
        seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(get("/api/v1/investigations?status=stopped").header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.items[0].id").value(invA))
                .andExpect(jsonPath("$.data.items[0].signal_id").value(sigA))
                .andExpect(jsonPath("$.data.items[0].cost_estimate").value("1.8000"));
        mockMvc.perform(get("/api/v1/investigations?signal_id=" + sigB).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.items[0].status").value("failed"));
        mockMvc.perform(get("/api/v1/investigations?status=bogus").header("Authorization", token))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(get("/api/v1/investigations"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
        // viewer 可读、不可 stop
        String viewer = "Bearer " + login("viewer@sense2act.local", "viewer123");
        mockMvc.perform(get("/api/v1/investigations/" + invA + "/steps?cursor=-1").header("Authorization", viewer))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(get("/api/v1/investigations/" + invA + "/steps?limit=999").header("Authorization", viewer))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(post("/api/v1/investigations/{id}/stop", invB).header("Authorization", viewer))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value(40301));
        mockMvc.perform(get("/api/v1/investigations/inv_NOPE").header("Authorization", viewer))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
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
