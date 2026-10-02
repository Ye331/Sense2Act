package com.sense2act.backend.feedback;

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

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E5 验收:报告反馈(采纳回填 adopted_at、comment/flag 落 feedback_events,E5-1)、
 * 看板计数与库内一致(E5-2)、事件图谱三端点(E5-3)、规则版本管理(E2-6)。
 * 种子走完整链路:文档 → 信号 → 调查 → 证据 → 报告 → complete(invA 发布/invB 草稿)。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class FeedbackBoardTest {

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

    String docA;
    String docB;
    String invA;    // 全链路 → published 报告
    String invB;    // 只交报告不 complete → draft 报告
    String evDoc;   // 登记的证据
    String repA;    // published 报告
    String repB;    // draft 报告
    String eventId;
    String[] suggestionIds = new String[2];

    void seed() throws Exception {
        if (invA != null) {
            return;
        }
        String suffix = String.valueOf(System.nanoTime());
        docA = insertDoc("fb-a-" + suffix, "3100000.00");
        docB = insertDoc("fb-b-" + suffix, "900000.00");
        String sigA = push(docA, "0.60");
        String sigB = push(docB, "0.55");
        invA = investigate(sigA);
        invB = investigate(sigB);
        start(invA);
        start(invB);
        step(invA, "{\"event\":\"round_started\",\"round\":1,\"payload\":{\"round\":1}}");
        step(invA, "{\"event\":\"tool_completed\",\"round\":1,\"payload\":"
                + "{\"tool\":\"doc_search\",\"ok\":true},\"token_usage\":1200}");
        // 证据:引本库文档
        MvcResult ev = mockMvc.perform(post("/api/v1/internal/investigations/{id}/evidences", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"source_type\":\"announcement\",\"doc_id\":\"%s\","
                                + "\"excerpt\":\"历史公告:近半年同类项目均值 90 万\"}").formatted(docB)))
                .andExpect(status().isOk())
                .andReturn();
        evDoc = read(ev, "data.id");
        // invA 报告:2 结论 2 建议 1 事件 2 主体 1 关系
        String draftA = ("{\"title\":\"某市医保局数据平台大额采购:系三年规划的续建工程\","
                + "\"summary\":\"非孤立事件,属既有规划的二期续建。\","
                + "\"claims\":["
                + "{\"text\":\"本次采购为《数据平台建设三年规划》二期续建\",\"nature\":\"fact\","
                + "\"confidence\":0.9,\"evidence_ids\":[\"%s\"]},"
                + "{\"text\":\"预算约为近期同类均值 3 倍以上\",\"nature\":\"inference\",\"confidence\":0.7}],"
                + "\"action_suggestions\":["
                + "{\"text\":\"评估以联合体形式投标\",\"priority\":\"high\"},"
                + "{\"text\":\"跟踪该规划三期立项动态\",\"priority\":\"medium\"}],"
                + "\"event_extraction\":{\"event\":{\"title\":\"某市数据平台三年建设\",\"type\":\"construction_wave\"},"
                + "\"entities\":["
                + "{\"name\":\"某市医保局\",\"type\":\"org\",\"role\":\"participant\"},"
                + "{\"name\":\"数据平台建设三年规划\",\"type\":\"policy\",\"role\":\"scope\"}],"
                + "\"relations\":[{\"source\":\"entity:0\",\"target\":\"event\",\"relation\":\"参与\"}]}}")
                .formatted(evDoc);
        MvcResult ra = mockMvc.perform(post("/api/v1/internal/investigations/{id}/report", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(draftA))
                .andExpect(status().isOk())
                .andReturn();
        repA = read(ra, "data.id");
        eventId = read(ra, "data.event_id");
        JsonNode suggestions = objectMapper.readTree(ra.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("action_suggestions");
        for (int i = 0; i < suggestions.size(); i++) {
            suggestionIds[i] = suggestions.get(i).path("id").asText();
        }
        // invB 草稿报告(不 complete → draft,用于 40901 闸门)
        String draftB = "{\"title\":\"草稿报告:频率突增待证\",\"claims\":[{\"text\":\"x\",\"nature\":\"fact\","
                + "\"confidence\":0.5}]}";
        MvcResult rb = mockMvc.perform(post("/api/v1/internal/investigations/{id}/report", invB)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(draftB))
                .andExpect(status().isOk())
                .andReturn();
        repB = read(rb, "data.id");
        // invA 收尾 → 报告 published + 信号 confirmed
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/complete", invA)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk());
    }

    @Test
    @Order(1)
    void 报告反馈_采纳回填_评语误报落库_权限与闸门() throws Exception {
        seed();
        String analyst = "Bearer " + login("analyst@sense2act.local", "analyst123");
        String viewer = "Bearer " + login("viewer@sense2act.local", "viewer123");
        // viewer 写操作被方法级规则挡
        mockMvc.perform(post("/api/v1/reports/{id}/feedback", repA)
                        .header("Authorization", viewer).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"comment\":\"x\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value(40301));
        // 闸门:不存在 40401 / 空反馈 40001 / 非法 flag 40001 / 建议不属于本报告 42201 / 未发布 40901
        feedback(analyst, "rep_NOPE", "{\"comment\":\"x\"}", 40401);
        feedback(analyst, repA, "{}", 40001);
        feedback(analyst, repA, "{\"flag\":\"maybe\"}", 40001);
        feedback(analyst, repA, "{\"adopted_suggestion_ids\":[\"as_NOPE\"]}", 42201);
        feedback(analyst, repB, "{\"comment\":\"草稿也想反馈\"}", 40901);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM feedback_events", Integer.class)).isEqualTo(0);

        // 合法反馈:采纳两条建议 + 评语 + 标误报
        MvcResult ok = mockMvc.perform(post("/api/v1/reports/{id}/feedback", repA)
                        .header("Authorization", analyst).contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"adopted_suggestion_ids\":[\"%s\",\"%s\"],"
                                + "\"comment\":\"结论可信,建议已转商务跟进\",\"flag\":\"false_positive\"}")
                                .formatted(suggestionIds[0], suggestionIds[1])))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.report_id").value(repA))
                .andExpect(jsonPath("$.data.newly_adopted.length()").value(2))
                .andExpect(jsonPath("$.data.comment_recorded").value(true))
                .andExpect(jsonPath("$.data.flagged").value(true))
                .andReturn();
        assertThat(read(ok, "data.newly_adopted.0")).isIn((Object[]) suggestionIds);
        // adopted_at 全部回填
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM action_suggestions WHERE report_id = ? AND adopted_at IS NULL",
                Integer.class, repA)).isEqualTo(0);
        // feedback_events:2 adopt(suggestion)+1 comment(report)+1 flag_false_positive(report)
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM feedback_events WHERE target_type = 'suggestion' AND action = 'adopt'",
                Integer.class)).isEqualTo(2);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM feedback_events WHERE target_type = 'report' AND action = 'comment'",
                Integer.class)).isEqualTo(1);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT reason FROM feedback_events WHERE action = 'comment'", String.class))
                .contains("商务跟进");
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM feedback_events WHERE action = 'flag_false_positive'",
                Integer.class)).isEqualTo(1);
        // user 取自 JWT:feedback_events.user_id 指向 analyst
        String analystId = jdbcTemplate.queryForObject(
                "SELECT id FROM users WHERE email = 'analyst@sense2act.local'", String.class);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM feedback_events WHERE user_id = ?", Integer.class, analystId))
                .isEqualTo(4);

        // 重复采纳幂等:不再新增事件,newly_adopted 为空
        mockMvc.perform(post("/api/v1/reports/{id}/feedback", repA)
                        .header("Authorization", analyst).contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"adopted_suggestion_ids\":[\"%s\"]}").formatted(suggestionIds[0])))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.newly_adopted.length()").value(0));
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM feedback_events WHERE action = 'adopt'", Integer.class)).isEqualTo(2);
    }

    @Test
    @Order(2)
    void 看板_计数与库内一致() throws Exception {
        seed();
        String analyst = "Bearer " + login("analyst@sense2act.local", "analyst123");
        MvcResult r = mockMvc.perform(get("/api/v1/dashboard/summary").header("Authorization", analyst))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.documents.total").value(2))
                .andExpect(jsonPath("$.data.signals.total").value(2))
                .andExpect(jsonPath("$.data.investigations.total").value(2))
                .andExpect(jsonPath("$.data.reports.total").value(2))
                .andReturn();
        JsonNode data = objectMapper.readTree(r.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data");
        // 今日增量:种子都是刚写入的
        assertThat(data.path("documents").path("today").asInt()).isEqualTo(2);
        assertThat(data.path("signals").path("today").asInt()).isEqualTo(2);
        // by_status 与库内一致
        assertThat(data.path("signals").path("by_status").path("confirmed").asInt()).isEqualTo(1);
        assertThat(data.path("signals").path("by_status").path("investigating").asInt()).isEqualTo(1);
        assertThat(data.path("investigations").path("by_status").path("completed").asInt()).isEqualTo(1);
        assertThat(data.path("investigations").path("by_status").path("reporting").asInt()).isEqualTo(1);
        assertThat(data.path("reports").path("by_status").path("published").asInt()).isEqualTo(1);
        assertThat(data.path("reports").path("by_status").path("draft").asInt()).isEqualTo(1);
        assertThat(data.path("signals").path("by_status").path("pending").asInt()).isEqualTo(0);
        assertThat(data.path("investigations").path("by_status").path("failed").asInt()).isEqualTo(0);
        // viewer 可读,未认证 40101
        mockMvc.perform(get("/api/v1/dashboard/summary")
                        .header("Authorization", "Bearer " + login("viewer@sense2act.local", "viewer123")))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/v1/dashboard/summary"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
    }

    @Test
    @Order(3)
    void 事件图谱_列表_详情_图() throws Exception {
        seed();
        String analyst = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(get("/api/v1/events").header("Authorization", analyst))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.items[0].id").value(eventId))
                .andExpect(jsonPath("$.data.items[0].title").value("某市数据平台三年建设"))
                .andExpect(jsonPath("$.data.items[0].status").value("ongoing"));
        mockMvc.perform(get("/api/v1/events/{id}", eventId).header("Authorization", analyst))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.entities.length()").value(2))
                .andExpect(jsonPath("$.data.entities[0].role").exists())
                .andExpect(jsonPath("$.data.entities[0].name").exists());
        mockMvc.perform(get("/api/v1/events/{id}/graph", eventId).header("Authorization", analyst))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.nodes.length()").value(3))   // 1 事件 + 2 主体
                .andExpect(jsonPath("$.data.edges.length()").value(3))   // 2 参与边 + 1 关系
                .andExpect(jsonPath("$.data.reports.length()").value(1))
                .andExpect(jsonPath("$.data.reports[0].id").value(repA))
                .andExpect(jsonPath("$.data.reports[0].status").value("published"));
        mockMvc.perform(get("/api/v1/events/evt_NOPE").header("Authorization", analyst))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
        mockMvc.perform(get("/api/v1/events/evt_NOPE/graph").header("Authorization", analyst))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }

    @Test
    @Order(4)
    void 规则版本管理_种子三条_PATCH出新版本() throws Exception {
        seed();
        String admin = "Bearer " + login("admin@sense2act.local", "admin123");
        String analyst = "Bearer " + login("analyst@sense2act.local", "analyst123");
        // /admin/** 整组仅 admin
        mockMvc.perform(get("/api/v1/admin/signal-rules").header("Authorization", analyst))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value(40301));
        // 种子:3 条默认规则 version=1 全部 enabled
        MvcResult list = mockMvc.perform(get("/api/v1/admin/signal-rules").header("Authorization", admin))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(3))
                .andReturn();
        JsonNode rules = objectMapper.readTree(list.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data");
        assertThat(rules.toString()).contains("rule_seed01").contains("采购金额异常");
        for (JsonNode rule : rules) {
            assertThat(rule.path("version").asInt()).isEqualTo(1);
            assertThat(rule.path("enabled").asBoolean()).isTrue();
        }

        // 新建:type 非法 40001,重名 40901,合法 → version=1 enabled
        mockMvc.perform(post("/api/v1/admin/signal-rules").header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"x\",\"type\":\"bogus\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        String created = "{\"name\":\"组合:大额且高频\",\"type\":\"composite\","
                + "\"params\":{\"rules\":[\"rule_seed01\",\"rule_seed02\"]},\"weight\":0.9}";
        MvcResult create = mockMvc.perform(post("/api/v1/admin/signal-rules")
                        .header("Authorization", admin).contentType(MediaType.APPLICATION_JSON)
                        .content(created))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.version").value(1))
                .andExpect(jsonPath("$.data.enabled").value(true))
                .andExpect(jsonPath("$.data.weight").value("0.900"))
                .andReturn();
        String newId = read(create, "data.id");
        mockMvc.perform(post("/api/v1/admin/signal-rules").header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"组合:大额且高频\",\"type\":\"composite\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));

        // PATCH:空体 40001 / 不存在 40401 / 合法 → version=2,旧版停用,同 id 只一个 enabled
        mockMvc.perform(patch("/api/v1/admin/signal-rules/{id}", newId)
                        .header("Authorization", admin).contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(patch("/api/v1/admin/signal-rules/rule_NOPE")
                        .header("Authorization", admin).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"weight\":0.5}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
        mockMvc.perform(patch("/api/v1/admin/signal-rules/rule_seed02")
                        .header("Authorization", admin).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"params\":{\"burst_ratio\":4.0,\"window_days\":60},\"weight\":0.65}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").value("rule_seed02"))
                .andExpect(jsonPath("$.data.version").value(2))
                .andExpect(jsonPath("$.data.enabled").value(true))
                .andExpect(jsonPath("$.data.weight").value("0.650"))
                .andExpect(jsonPath("$.data.params.burst_ratio").value(4.0));
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM signal_rules WHERE id = 'rule_seed02' AND enabled", Integer.class))
                .isEqualTo(1);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT enabled FROM signal_rules WHERE id = 'rule_seed02' AND version = 1",
                Boolean.class)).isEqualTo(false);
        // 未指定 enabled 的 PATCH 语义:停旧启新(上面已验);显式 enabled=false → 两版都停
        mockMvc.perform(patch("/api/v1/admin/signal-rules/{id}", newId)
                        .header("Authorization", admin).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":false}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.version").value(2))
                .andExpect(jsonPath("$.data.enabled").value(false));
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM signal_rules WHERE id = ? AND enabled", Integer.class, newId))
                .isEqualTo(0);
    }

    // ---------- 工具 ----------

    private void feedback(String bearer, String reportId, String body, int expectedCode) throws Exception {
        int httpStatus = switch (expectedCode) {
            case 40001 -> 400;
            case 40401 -> 404;
            case 40901 -> 409;
            case 42201 -> 422;
            default -> expectedCode;
        };
        mockMvc.perform(post("/api/v1/reports/{id}/feedback", reportId)
                        .header("Authorization", bearer).contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().is(httpStatus))
                .andExpect(jsonPath("$.code").value(expectedCode));
    }

    private String insertDoc(String key, String amount) {
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType("announcement");
        d.setTitle("反馈链路文档-" + key);
        d.setContentText("某局拟采购数据平台,预算 " + amount + " 元,正文-" + key);
        d.setOrgId(null);
        d.setAmount(new BigDecimal(amount));
        d.setPublishDate(LocalDate.parse("2026-09-25"));
        d.setRegion("华东/某省某市");
        d.setCategory("医疗信息化");
        d.setUrl("http://fb.example/" + key);
        d.setRaw(Map.of("k", "v"));
        d.setContentHash("hash-" + key);
        d.setSignalScanned(true);
        documentMapper.insert(d);
        return d.getId();
    }

    private String push(String docId, String score) throws Exception {
        MvcResult r = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"document\":{\"id\":\"%s\"},\"score\":%s,\"detector\":\"det\","
                                + "\"hits\":[{\"rule_type\":\"amount_anomaly\",\"weight\":0.6,\"detail\":{\"z\":4.2}}]}")
                                .formatted(docId, score)))
                .andExpect(status().isOk())
                .andReturn();
        return read(r, "data.signal_id");
    }

    private String investigate(String signalId) throws Exception {
        MvcResult r = mockMvc.perform(post("/api/v1/signals/{id}/investigate", signalId)
                        .header("Authorization", "Bearer " + login("analyst@sense2act.local", "analyst123")))
                .andExpect(status().isOk())
                .andReturn();
        return read(r, "data.investigation_id");
    }

    private void start(String invId) throws Exception {
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/start", invId)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk());
    }

    private void step(String invId, String body) throws Exception {
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invId)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk());
    }

    private String read(MvcResult r, String path) throws Exception {
        JsonNode node = objectMapper.readTree(r.getResponse().getContentAsString(StandardCharsets.UTF_8));
        for (String p : path.split("\\.")) {
            if (p.matches("\\d+")) {
                node = node.path(Integer.parseInt(p));
            } else {
                node = node.path(p);
            }
        }
        return node.asText();
    }

    private String login(String email, String password) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/auth/token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"%s\",\"password\":\"%s\"}".formatted(email, password)))
                .andExpect(status().isOk())
                .andReturn();
        return read(result, "data.token");
    }
}
