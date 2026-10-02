package com.sense2act.backend.report;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
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
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.math.BigDecimal;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDate;
import java.util.HexFormat;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E4 验收:证据登记两变体(本库回填 / 外部抓快照算 hash,D4 失败整条拒收)、
 * ReportDraft 校验落库(claims 证据引用 42201 整份拒收、meta 后端计算 D5、图谱四表)、
 * complete/fail 收尾(D6 信号 confirmed / 回退 pending)、报告页与证据详情/快照、md/pdf 导出。
 * 外部抓取用测试内 HttpServer 假源(成功 / 500 / 死端口三种)。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ReportFlowTest {

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

    private static final String SNAPSHOT = "<html>外部报道正文 SNAPSHOT-42</html>";

    static HttpServer server;
    static int okPort;
    static int deadPort;

    String sigA;
    String sigB;
    String invA;   // 走全链路:证据 → 报告 → complete
    String invB;   // fail 收尾
    String docA;   // 信号源文档
    String docB;   // 本库证据文档
    String docKeyB;
    String evDoc;  // 登记出的证据 id
    String evExt;
    String repId;

    void seed() throws Exception {
        if (invA != null) {
            return;
        }
        startFakeSources();
        String suffix = String.valueOf(System.nanoTime());
        docA = insertDoc("rep-a-" + suffix, "3250000.00");
        docKeyB = "rep-b-" + suffix;
        docB = insertDoc(docKeyB, "820000.00");
        sigA = push(docA, "0.60");
        sigB = push(docB, "0.55");
        invA = investigate(sigA);
        invB = investigate(sigB);
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/start", invA)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk());
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/start", invB)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk());
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"round_started\",\"round\":1,\"payload\":{\"round\":1}}"))
                .andExpect(status().isOk());
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"tool_completed\",\"round\":1,\"payload\":"
                                + "{\"tool\":\"doc_search\",\"ok\":true},\"token_usage\":1500}"))
                .andExpect(status().isOk());
    }

    /** 假外部源:/ok 200 回正文;/err 永远 500;死端口用于连接拒绝。 */
    void startFakeSources() throws Exception {
        if (server != null) {
            return;
        }
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/ok", ex -> {
            byte[] body = SNAPSHOT.getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200, body.length);
            ex.getResponseBody().write(body);
            ex.close();
        });
        server.createContext("/err", ex -> {
            ex.sendResponseHeaders(500, -1);
            ex.close();
        });
        server.start();
        okPort = server.getAddress().getPort();
        HttpServer dead = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        deadPort = dead.getAddress().getPort();
        dead.stop(0);
    }

    @AfterAll
    static void stopFakeSources() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    @Order(1)
    void 证据登记_本库回填_外部抓快照_失败整条拒收() throws Exception {
        seed();
        // 变体一:doc_id —— url/title/published_at/hash 全部回填自本库文档
        // 括号:.formatted 必须作用于整个拼接串(坑位,%s 在前段字面量会原样进请求体)
        MvcResult r1 = mockMvc.perform(post("/api/v1/internal/investigations/{id}/evidences", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"source_type\":\"announcement\",\"doc_id\":\"%s\","
                                + "\"excerpt\":\"历史公告:近半年同类项目均值 82 万\"}").formatted(docB)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.doc_id").value(docB))
                .andExpect(jsonPath("$.data.url").value("http://rep-flow.example/" + docKeyB))
                .andExpect(jsonPath("$.data.title").exists())
                .andExpect(jsonPath("$.data.published_at").exists())
                .andExpect(jsonPath("$.data.content_hash").exists())
                .andReturn();
        evDoc = read(r1, "data.id");

        // 变体二:外部内容 —— 后端抓快照,SHA-256 入库,快照文件落盘
        String expectedHash = sha256(SNAPSHOT.getBytes(StandardCharsets.UTF_8));
        MvcResult r2 = mockMvc.perform(post("/api/v1/internal/investigations/{id}/evidences", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"source_type\":\"web\",\"url\":\"http://127.0.0.1:%d/ok\","
                                        + "\"title\":\"外部报道:该市医疗信息化扩建\",\"excerpt\":\"两家医院同期立项\","
                                        + "\"published_at\":\"2026-09-01\"}")
                                .formatted(okPort)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.content_hash").value(expectedHash))
                .andExpect(jsonPath("$.data.snapshot_key").value("ev_" + expectedHash))
                .andExpect(jsonPath("$.data.fetched_at").exists())
                .andReturn();
        evExt = read(r2, "data.id");

        // 快照字节原样可读(E4-5 顺带验证)
        MvcResult snap = mockMvc.perform(get("/api/v1/evidences/{id}/snapshot", evExt)
                        .header("Authorization", "Bearer " + login("viewer@sense2act.local", "viewer123")))
                .andExpect(status().isOk())
                .andReturn();
        assertThat(new String(snap.getResponse().getContentAsByteArray(), StandardCharsets.UTF_8))
                .isEqualTo(SNAPSHOT);

        // 抓取失败(死端口 / 永远 500)→ 重试耗尽整条拒收,不落库(D4)
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/evidences", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"source_type\":\"web\",\"url\":\"http://127.0.0.1:%d/ok\","
                                        + "\"title\":\"死端口\",\"excerpt\":\"x\",\"published_at\":\"2026-09-01\"}")
                                .formatted(deadPort)))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value(42201));
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/evidences", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"source_type\":\"web\",\"url\":\"http://127.0.0.1:%d/err\","
                                        + "\"title\":\"永 500\",\"excerpt\":\"x\",\"published_at\":\"2026-09-01\"}")
                                .formatted(okPort)))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value(42201));
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM evidences WHERE investigation_id = ?", Integer.class, invA)).isEqualTo(2);

        // 参数与鉴权闸门
        postEvidence(invA, "{\"source_type\":\"bogus\",\"excerpt\":\"x\",\"doc_id\":\"%s\"}".formatted(docB), 40001);
        postEvidence(invA, "{\"source_type\":\"web\",\"url\":\"http://127.0.0.1:%d/ok\"".formatted(okPort)
                + ",\"title\":\"缺摘录\",\"published_at\":\"2026-09-01\"}", 40001);
        postEvidence(invA, "{\"source_type\":\"web\",\"url\":\"http://127.0.0.1:%d/ok\"".formatted(okPort)
                + ",\"title\":\"缺发布时间\",\"excerpt\":\"x\"}", 40001);
        postEvidence(invA, "{\"source_type\":\"web\",\"url\":\"http://127.0.0.1:%d/ok\"".formatted(okPort)
                + ",\"excerpt\":\"缺标题\",\"published_at\":\"2026-09-01\"}", 40001);
        postEvidence(invA, "{\"source_type\":\"announcement\",\"doc_id\":\"doc_NOPE\",\"excerpt\":\"x\"}", 42201);
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/evidences", invA)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"source_type\":\"web\",\"excerpt\":\"x\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
    }

    @Test
    @Order(2)
    void 报告提交_校验整份拒收_图谱四表_meta后端计算() throws Exception {
        seed();
        // 非法草稿先打(状态还在 investigating,全部整份拒收且不留行)
        postReport(invA, "{\"title\":\"空结论\",\"claims\":[]}", 40001);
        postReport(invA, "{\"title\":\"坏 nature\",\"claims\":[{\"text\":\"x\",\"nature\":\"guess\","
                + "\"confidence\":0.5}]}", 40001);
        postReport(invA, "{\"title\":\"置信度越界\",\"claims\":[{\"text\":\"x\",\"nature\":\"fact\","
                + "\"confidence\":1.5}]}", 40001);
        postReport(invA, "{\"title\":\"引用未登记证据\",\"claims\":[{\"text\":\"x\",\"nature\":\"fact\","
                + "\"confidence\":0.5,\"evidence_ids\":[\"ev_NOPE\"]}]}", 42201);
        postReport(invA, "{\"title\":\"关系节点越界\",\"claims\":[{\"text\":\"x\",\"nature\":\"fact\","
                        + "\"confidence\":0.5}],\"event_extraction\":{\"event\":{\"title\":\"e\",\"type\":\"policy\"},"
                        + "\"entities\":[{\"name\":\"n\",\"type\":\"org\"}],"
                        + "\"relations\":[{\"source\":\"entity:9\",\"target\":\"event\",\"relation\":\"参与\"}]}}",
                40001);
        postReport(invA, "{\"title\":\"主体类型非法\",\"claims\":[{\"text\":\"x\",\"nature\":\"fact\","
                        + "\"confidence\":0.5}],\"event_extraction\":{\"event\":{\"title\":\"e\",\"type\":\"policy\"},"
                        + "\"entities\":[{\"name\":\"n\",\"type\":\"vendor\"}]}}", 40001);
        postReport(invA, "{\"title\":\"引用不存在事件\",\"event_id\":\"evt_NOPE\",\"claims\":[{\"text\":\"x\","
                + "\"nature\":\"fact\",\"confidence\":0.5}]}", 42201);
        postReport(invA, "{\"title\":\"优先级非法\",\"claims\":[{\"text\":\"x\",\"nature\":\"fact\","
                + "\"confidence\":0.5}],\"action_suggestions\":[{\"text\":\"s\",\"priority\":\"urgent\"}]}", 40001);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM reports WHERE investigation_id = ?", Integer.class, invA)).isEqualTo(0);

        // 合法草稿:3 结论覆盖三种 nature、2 建议、图谱 1 事件 3 主体 2 关系
        String draft = ("{\"title\":\"某市人民医院大额AI辅助诊断招标:规划三期建设而非孤立采购\","
                + "\"summary\":\"该招标并非孤立事件:属于医院两年前规划的第三期建设;同市另有2家医院启动类似项目。\","
                + "\"claims\":["
                + "{\"text\":\"本次采购属于《医院信息化建设规划(2024-2027)》第三期建设计划\",\"nature\":\"fact\","
                + "\"confidence\":0.95,\"evidence_ids\":[\"%s\",\"%s\"]},"
                + "{\"text\":\"本次预算约为近半年同类项目均值的 4 倍\",\"nature\":\"inference\","
                + "\"confidence\":0.8,\"evidence_ids\":[\"%s\"]},"
                + "{\"text\":\"同市另有2家医院启动类似项目,可能是一轮建设潮的开始\",\"nature\":\"speculation\","
                + "\"confidence\":0.6}],"
                + "\"action_suggestions\":["
                + "{\"text\":\"评估参与方式:独立投标或联合既往合作方\",\"priority\":\"high\"},"
                + "{\"text\":\"跟踪同市另外两家医院的立项进展\",\"priority\":\"medium\"}],"
                + "\"event_extraction\":{\"event\":{\"title\":\"某市医疗信息化三期建设潮\",\"type\":\"construction_wave\","
                + "\"start_date\":\"2024-03-15\"},"
                + "\"entities\":["
                + "{\"name\":\"某市人民医院\",\"type\":\"org\",\"role\":\"participant\"},"
                + "{\"name\":\"医院信息化建设规划(2024-2027)\",\"type\":\"policy\",\"role\":\"scope\"},"
                + "{\"name\":\"华东/某省某市\",\"type\":\"region\",\"role\":\"scope\"}],"
                + "\"relations\":["
                + "{\"source\":\"entity:0\",\"target\":\"event\",\"relation\":\"参与\"},"
                + "{\"source\":\"entity:1\",\"target\":\"event\",\"relation\":\"佐证\"}]},"
                + "\"token_usage\":41200}")
                .formatted(evDoc, evExt, evExt);
        MvcResult r = mockMvc.perform(post("/api/v1/internal/investigations/{id}/report", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(draft))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("draft"))
                .andExpect(jsonPath("$.data.claims.length()").value(3))
                .andExpect(jsonPath("$.data.claims[0].nature").value("fact"))
                .andExpect(jsonPath("$.data.claims[0].confidence").value(0.95))
                .andExpect(jsonPath("$.data.claims[0].evidences[0].id").value(evDoc))
                .andExpect(jsonPath("$.data.claims[0].evidences[0].source_type").value("announcement"))
                .andExpect(jsonPath("$.data.claims[1].evidences.length()").value(1))
                .andExpect(jsonPath("$.data.claims[2].evidences").isEmpty())
                .andExpect(jsonPath("$.data.action_suggestions.length()").value(2))
                .andExpect(jsonPath("$.data.meta.rounds").value(1))
                .andExpect(jsonPath("$.data.meta.tool_calls").value(1))
                .andExpect(jsonPath("$.data.meta.token_used").value(1500))
                .andExpect(jsonPath("$.data.meta.cost_estimate").value("0.0450"))   // D5:steps 口径,不吃自报 41200
                .andExpect(jsonPath("$.data.disclaimer").value("本报告由AI生成，结论不替代人的自主判断"))
                .andReturn();
        repId = read(r, "data.id");
        String eventId = read(r, "data.event_id");
        assertThat(eventId).isNotBlank();

        // 落库核查:调查转 reporting、report_ready 留痕、图谱四表
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM investigations WHERE id = ?", String.class, invA)).isEqualTo("reporting");
        assertThat(jdbcTemplate.queryForObject(
                "SELECT content->>'report_id' FROM investigation_steps WHERE investigation_id = ? "
                        + "AND content->>'event' = 'report_ready'", String.class, invA)).isEqualTo(repId);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM entities", Integer.class)).isEqualTo(3);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM events WHERE created_by_report = ?", Integer.class, repId)).isEqualTo(1);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM event_entities WHERE event_id = ?", Integer.class, eventId)).isEqualTo(3);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM event_relations", Integer.class)).isEqualTo(2);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM evidence_links", Integer.class)).isEqualTo(3);
        // 主体幂等:同名再提交(被 40901 挡)之外,重复调查引用同名主体也按名复用 —— 这里核查 entities 无重复行
        assertThat(jdbcTemplate.queryForObject(
                "SELECT count(*) FROM entities WHERE name = '某市人民医院'", Integer.class)).isEqualTo(1);

        // 一调查一报告 + reporting 后不可再登记证据
        postReport(invA, "{\"title\":\"重复提交\",\"claims\":[{\"text\":\"x\",\"nature\":\"fact\",\"confidence\":0.5}]}",
                40901);
        postEvidence(invA, "{\"source_type\":\"web\",\"url\":\"http://127.0.0.1:%d/ok\"".formatted(okPort)
                + ",\"title\":\"晚了\",\"excerpt\":\"x\",\"published_at\":\"2026-09-01\"}", 40901);
    }

    @Test
    @Order(3)
    void 收尾_complete无报告42201_信号confirmed_fail回pending() throws Exception {
        seed();
        // invB:investigating 无报告 → complete 42201;error 缺失 → 40001;fail 收尾 → 信号回 pending
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/complete", invB)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value(42201));
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/fail", invB)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/fail", invB)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"error\":\"证据不足以支撑结论,中止\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("failed"))
                .andExpect(jsonPath("$.data.finished_at").exists());
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM signals WHERE id = ?", String.class, sigB)).isEqualTo("pending");
        assertThat(jdbcTemplate.queryForObject(
                "SELECT content->>'error' FROM investigation_steps WHERE investigation_id = ? "
                        + "AND content->>'event' = 'investigation_failed'", String.class, invB))
                .contains("证据不足");

        // invA:有报告 → complete → completed + 报告转 published + 信号 confirmed(D6)
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/complete", invA)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("completed"))
                .andExpect(jsonPath("$.data.finished_at").exists());
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM reports WHERE id = ?", String.class, repId)).isEqualTo("published");
        assertThat(jdbcTemplate.queryForObject(
                "SELECT status FROM signals WHERE id = ?", String.class, sigA)).isEqualTo("confirmed");
        assertThat(jdbcTemplate.queryForObject(
                "SELECT content->>'event' FROM investigation_steps WHERE investigation_id = ? "
                        + "AND content->>'event' = 'investigation_completed'", String.class, invA))
                .isEqualTo("investigation_completed");
        // 终态:再 complete / fail 均 40901
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/complete", invA)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/fail", invA)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"error\":\"x\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
    }

    @Test
    @Order(4)
    void 报告页与证据详情_权限与40401() throws Exception {
        seed();
        String analyst = "Bearer " + login("analyst@sense2act.local", "analyst123");
        String viewer = "Bearer " + login("viewer@sense2act.local", "viewer123");
        mockMvc.perform(get("/api/v1/reports").header("Authorization", analyst))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.items[0].id").value(repId))
                .andExpect(jsonPath("$.data.items[0].title").exists());
        mockMvc.perform(get("/api/v1/reports?investigation_id=" + invA).header("Authorization", analyst))
                .andExpect(jsonPath("$.data.total").value(1));
        mockMvc.perform(get("/api/v1/reports?investigation_id=inv_NOPE").header("Authorization", analyst))
                .andExpect(jsonPath("$.data.total").value(0));
        mockMvc.perform(get("/api/v1/reports?date_from=not-a-date").header("Authorization", analyst))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(get("/api/v1/reports"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
        // viewer 可读详情
        mockMvc.perform(get("/api/v1/reports/{id}", repId).header("Authorization", viewer))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").value(repId))
                .andExpect(jsonPath("$.data.status").value("published"))
                .andExpect(jsonPath("$.data.claims[0].evidences[0].title").exists());
        mockMvc.perform(get("/api/v1/reports/rep_NOPE").header("Authorization", viewer))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
        // 证据详情与快照缺位
        mockMvc.perform(get("/api/v1/evidences/{id}", evDoc).header("Authorization", viewer))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.doc_id").value(docB))
                .andExpect(jsonPath("$.data.investigation_id").value(invA));
        mockMvc.perform(get("/api/v1/evidences/ev_NOPE").header("Authorization", viewer))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
        mockMvc.perform(get("/api/v1/evidences/{id}/snapshot", evDoc).header("Authorization", viewer))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));   // 本库文档未存快照 → 无快照可回
    }

    @Test
    @Order(5)
    void 导出_md同步_pdf占位202() throws Exception {
        seed();
        String analyst = "Bearer " + login("analyst@sense2act.local", "analyst123");
        MvcResult md = mockMvc.perform(get("/api/v1/reports/{id}/export", repId)
                        .header("Authorization", analyst))
                .andExpect(status().isOk())
                .andReturn();
        assertThat(md.getResponse().getContentType()).contains("text/markdown");
        String body = new String(md.getResponse().getContentAsByteArray(), StandardCharsets.UTF_8);
        assertThat(body).contains("某市人民医院大额AI辅助诊断招标");
        assertThat(body).contains("本报告由AI生成，结论不替代人的自主判断");
        assertThat(body).contains("事实结论（fact）");
        assertThat(body).contains("推断（inference）");
        assertThat(body).contains("推测（speculation）");
        assertThat(body).contains("评估参与方式");
        assertThat(body).contains("外部报道:该市医疗信息化扩建");
        assertThat(body).contains("费用估算(元): 0.0450");

        mockMvc.perform(get("/api/v1/reports/{id}/export?format=pdf", repId).header("Authorization", analyst))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.data.task_id").exists())
                .andExpect(jsonPath("$.data.status").value("pending"));
        mockMvc.perform(get("/api/v1/reports/{id}/export?format=docx", repId).header("Authorization", analyst))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(get("/api/v1/reports/rep_NOPE/export").header("Authorization", analyst))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }

    // ---------- 工具 ----------

    private String insertDoc(String key, String amount) {
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType("announcement");
        d.setTitle("报告链路文档-" + key);
        d.setContentText("某医院拟采购 AI 辅助诊断系统,预算 " + amount + " 元,正文-" + key);
        d.setOrgId(null);
        d.setAmount(new BigDecimal(amount));
        d.setPublishDate(LocalDate.parse("2026-09-25"));
        d.setRegion("华东/某省某市");
        d.setCategory("医疗信息化");
        d.setUrl("http://rep-flow.example/" + key);
        d.setRaw(Map.of("k", "v"));
        d.setContentHash("hash-" + key);
        d.setSignalScanned(true);
        documentMapper.insert(d);
        return d.getId();
    }

    private String push(String docId, String score) throws Exception {
        // 括号:.formatted 必须作用于整个拼接串(坑位)
        MvcResult r = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"document\":{\"id\":\"%s\"},\"score\":%s,\"detector\":\"det\","
                                + "\"hits\":[{\"rule_type\":\"amount_anomaly\",\"weight\":0.6,\"detail\":{\"z\":5.1}}]}")
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

    private void postEvidence(String invId, String body, int expectedCode) throws Exception {
        int httpStatus = switch (expectedCode) {
            case 40001 -> 400;
            case 42201 -> 422;
            case 40901 -> 409;
            default -> expectedCode;
        };
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/evidences", invId)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().is(httpStatus))
                .andExpect(jsonPath("$.code").value(expectedCode));
    }

    private void postReport(String invId, String body, int expectedCode) throws Exception {
        int httpStatus = switch (expectedCode) {
            case 40001 -> 400;
            case 42201 -> 422;
            case 40901 -> 409;
            default -> expectedCode;
        };
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/report", invId)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().is(httpStatus))
                .andExpect(jsonPath("$.code").value(expectedCode));
    }

    private String read(MvcResult r, String path) throws Exception {
        JsonNode node = objectMapper.readTree(r.getResponse().getContentAsString(StandardCharsets.UTF_8));
        String[] parts = path.split("\\.");
        for (String p : parts) {
            node = node.path(p);
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

    private static String sha256(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
}
