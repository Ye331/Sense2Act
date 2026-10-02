package com.sense2act.backend.signal;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
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
import org.springframework.web.util.UriComponentsBuilder;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.math.BigDecimal;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E2-2 查询 / E2-5 人工决策与手动调查验收:§4 全部筛选与响应形状、
 * dismiss 必填 reason、决策写 feedback_events 留 decided_by/at、
 * 手动开调查的三种拒因(40901 在调查中 / 42201 已忽略)。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class SignalApiTest {

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

    String orgA;
    String titleA;
    String sigA;   // docA(有机构) 0.80,双命中,pending → 后确认
    String sigB;   // docB(无机构) 0.42,pending → 后忽略
    String sigC;   // docC(有机构) 0.30,pending → 后手动开调查

    void seed() throws Exception {
        if (orgA != null) {
            return;
        }
        String suffix = String.valueOf(System.nanoTime());
        Organization org = new Organization();
        org.setId(IdGen.next("org"));
        org.setName("信号查询医院" + suffix);
        org.setAliases(List.of());
        organizationMapper.insert(org);
        orgA = org.getId();

        String docA = insertDoc("qa-" + suffix, orgA, "3250000.00");
        titleA = "信号查询文档-qa-" + suffix;
        String docB = insertDoc("qb-" + suffix, null, null);
        String docC = insertDoc("qc-" + suffix, orgA, "100.00");

        // 三条都低于默认阈值 0.85,保持 pending,状态变化由测试自己驱动
        sigA = push(docA, """
                [{"rule_type":"amount_anomaly","weight":0.6,"detail":{"z":5.9}},
                 {"rule_type":"semantic_match","weight":0.4,"detail":{"similarity":0.86,"profile":"医疗AI"}}]""", "0.80");
        sigB = push(docB, """
                [{"rule_type":"frequency_burst","weight":1.0,"detail":{"freq":2.5}}]""", "0.42");
        sigC = push(docC, """
                [{"rule_type":"semantic_match","weight":0.9,"detail":{"similarity":0.7,"profile":"数据平台"}}]""", "0.30");
    }

    private String insertDoc(String key, String orgId, String amount) {
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType("announcement");
        d.setTitle("信号查询文档-" + key);
        d.setContentText("正文-" + key);
        d.setOrgId(orgId);
        d.setAmount(amount == null ? null : new BigDecimal(amount));
        d.setPublishDate(LocalDate.parse("2026-09-24"));
        d.setRegion("华东/某省某市");
        d.setCategory("医疗信息化");
        d.setUrl("http://signal-api.example/" + key);
        d.setRaw(Map.of("k", "v"));
        d.setContentHash("hash-" + key);
        d.setSignalScanned(true);
        documentMapper.insert(d);
        return d.getId();
    }

    private String push(String docId, String hitsJson, String score) throws Exception {
        MvcResult r = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"document\":{\"id\":\"%s\"},\"hits\":%s,\"score\":%s,\"detector\":\"det\"}"
                                .formatted(docId, hitsJson, score)))
                .andExpect(status().isOk())
                .andReturn();
        return objectMapper.readTree(r.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("signal_id").asText();
    }

    @Test
    @Order(1)
    void 列表_默认按创建倒序_分页形状() throws Exception {
        seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(get("/api/v1/signals").header("Authorization", token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(3))
                .andExpect(jsonPath("$.data.page").value(1))
                .andExpect(jsonPath("$.data.page_size").value(20))
                .andExpect(jsonPath("$.data.items.length()").value(3))
                .andExpect(jsonPath("$.data.items[0].id").value(sigC))   // 最后推的最前
                .andExpect(jsonPath("$.data.items[2].id").value(sigA));
    }

    @Test
    @Order(2)
    void 筛选_status_rule_type_score_gte_org_id_日期() throws Exception {
        seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(get(sigUri("status", "pending")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(3));
        mockMvc.perform(get(sigUri("score_gte", "0.5")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.items[0].id").value(sigA));
        mockMvc.perform(get(sigUri("rule_type", "semantic_match")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(2));
        mockMvc.perform(get(sigUri("org_id", orgA)).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(2));
        mockMvc.perform(get(sigUri("date_from", LocalDate.now().toString())).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(3));
        mockMvc.perform(get(sigUri("date_from", LocalDate.now().plusDays(1).toString()))
                        .header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(0));
        for (String[] bad : new String[][]{{"status", "nope"}, {"score_gte", "abc"}, {"date_from", "not-a-date"}}) {
            mockMvc.perform(get(sigUri(bad)).header("Authorization", token))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value(40001));
        }
    }

    @Test
    @Order(3)
    void 详情_契约形状_字符串小数_无机构为空() throws Exception {
        seed();
        String token = "Bearer " + login("viewer@sense2act.local", "viewer123");
        mockMvc.perform(get("/api/v1/signals/{id}", sigA).header("Authorization", token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").value(sigA))
                .andExpect(jsonPath("$.data.document.id").exists())
                .andExpect(jsonPath("$.data.document.title").value(titleA))
                .andExpect(jsonPath("$.data.document.amount").value("3250000.00"))   // 字符串小数
                .andExpect(jsonPath("$.data.document.publish_date").value("2026-09-24"))
                .andExpect(jsonPath("$.data.org.id").value(orgA))
                .andExpect(jsonPath("$.data.org.name").exists())
                .andExpect(jsonPath("$.data.hits.length()").value(2))
                .andExpect(jsonPath("$.data.hits[0].rule_type").value("amount_anomaly"))
                .andExpect(jsonPath("$.data.hits[1].detail.profile").value("医疗AI"))
                .andExpect(jsonPath("$.data.score").value("0.800"))                  // 字符串小数,NUMERIC(4,3) 读回补足三位
                .andExpect(jsonPath("$.data.status").value("pending"))
                .andExpect(jsonPath("$.data.investigation_id").doesNotExist());      // null 序列化省略或 null
        mockMvc.perform(get("/api/v1/signals/sig_NOPE").header("Authorization", token))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
        // 无机构信号的 org 为 null,不报错
        mockMvc.perform(get("/api/v1/signals/{id}", sigB).header("Authorization", token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.org").doesNotExist());
    }

    @Test
    @Order(4)
    void 认证边界_信号接口不收内部key_viewer不可写() throws Exception {
        seed();
        mockMvc.perform(get("/api/v1/signals"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
        mockMvc.perform(get("/api/v1/signals").header("X-Internal-Key", "test-key"))
                .andExpect(status().isUnauthorized());   // 双认证只覆盖 documents/organizations(§9.3)
        mockMvc.perform(get("/api/v1/signals").header("Authorization",
                        "Bearer " + login("viewer@sense2act.local", "viewer123")))
                .andExpect(status().isOk());
        mockMvc.perform(patch("/api/v1/signals/{id}/status", sigA)
                        .header("Authorization", "Bearer " + login("viewer@sense2act.local", "viewer123"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"confirmed\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value(40301));
    }

    @Test
    @Order(5)
    void 忽略信号_必填reason_决策留痕feedback() throws Exception {
        seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(patch("/api/v1/signals/{id}/status", sigB)
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"dismissed\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(patch("/api/v1/signals/{id}/status", sigB)
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"dismissed\",\"reason\":\"金额正常波动\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("dismissed"));
        Map<String, Object> row = jdbcTemplate.queryForMap(
                "SELECT decided_by, decided_at, decision_reason FROM signals WHERE id = ?", sigB);
        assertThat(row.get("decision_reason")).isEqualTo("金额正常波动");
        assertThat(row.get("decided_at")).isNotNull();
        String analystId = jdbcTemplate.queryForObject(
                "SELECT id FROM users WHERE email = 'analyst@sense2act.local'", String.class);
        assertThat(row.get("decided_by")).isEqualTo(analystId);
        Map<String, Object> fb = jdbcTemplate.queryForMap(
                "SELECT target_type, target_id, action, reason FROM feedback_events WHERE target_id = ?", sigB);
        assertThat(fb.get("target_type")).isEqualTo("signal");
        assertThat(fb.get("action")).isEqualTo("dismiss");
        assertThat(fb.get("reason")).isEqualTo("金额正常波动");
        // 已忽略 → 开调查 42201(契约 §4)
        mockMvc.perform(post("/api/v1/signals/{id}/investigate", sigB).header("Authorization", token))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value(42201));
    }

    @Test
    @Order(6)
    void 手动开调查_pending可开_在调查中40901_调查中不可决策() throws Exception {
        seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        MvcResult r = mockMvc.perform(post("/api/v1/signals/{id}/investigate", sigC)
                        .header("Authorization", token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.investigation_id").exists())
                .andExpect(jsonPath("$.data.status").value("investigating"))
                .andReturn();
        String invId = objectMapper.readTree(
                r.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("investigation_id").asText();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM investigations WHERE id = ?", String.class, invId)).isEqualTo("created");
        // 再开 → 40901
        mockMvc.perform(post("/api/v1/signals/{id}/investigate", sigC).header("Authorization", token))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        // 调查中不可人工决策(仅 pending 可,40901)
        mockMvc.perform(patch("/api/v1/signals/{id}/status", sigC)
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"confirmed\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
    }

    @Test
    @Order(7)
    void 确认信号_feedback留痕_再决策40901() throws Exception {
        seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(patch("/api/v1/signals/{id}/status", sigA)
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"confirmed\",\"reason\":\"金额显著超基线\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("confirmed"));
        Map<String, Object> fb = jdbcTemplate.queryForMap(
                "SELECT action FROM feedback_events WHERE target_id = ?", sigA);
        assertThat(fb.get("action")).isEqualTo("confirm");
        // 终态不可再决策
        mockMvc.perform(patch("/api/v1/signals/{id}/status", sigA)
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"dismissed\",\"reason\":\"反悔\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        // 非法目标状态
        mockMvc.perform(patch("/api/v1/signals/{id}/status", "sig_NOPE")
                        .header("Authorization", token).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"pending\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
    }

    // ---------- 工具 ----------

    private static URI sigUri(String... pairs) {
        UriComponentsBuilder b = UriComponentsBuilder.fromUriString("/api/v1/signals");
        for (int i = 0; i < pairs.length; i += 2) {
            b.queryParam(pairs[i], pairs[i + 1]);
        }
        return b.build().encode().toUri();
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
