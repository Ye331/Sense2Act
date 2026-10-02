package com.sense2act.backend.ingest;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.common.events.SourceDegradedEvent;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.ingesterror.IngestError;
import com.sense2act.backend.domain.ingesterror.IngestErrorMapper;
import com.sense2act.backend.domain.org.Organization;
import com.sense2act.backend.domain.org.OrganizationMapper;
import com.sense2act.backend.domain.source.Source;
import com.sense2act.backend.domain.source.SourceMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.event.RecordApplicationEvents;
import org.springframework.test.context.event.ApplicationEvents;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E1-2/E1-3/E1-4 全链路验收(内部 key 已配置):
 * 领取到期源 → 批量推文档(去重/机构归一/快照/org_stats)→ 回报运行结果(D3 降级 + source_degraded 事件)。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@RecordApplicationEvents
class IngestFlowTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    static Path snapshotDir;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        try {
            snapshotDir = Files.createTempDirectory("snapshots-test");
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        registry.add("app.snapshot-dir", () -> snapshotDir.toString());
    }

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    SourceMapper sourceMapper;

    @Autowired
    DocumentMapper documentMapper;

    @Autowired
    OrganizationMapper organizationMapper;

    @Autowired
    IngestErrorMapper ingestErrorMapper;

    @Autowired
    JdbcTemplate jdbcTemplate;

    @Autowired
    ApplicationEvents applicationEvents;

    // ---------- E1-2 领取队列 ----------

    @Test
    void 从未跑过的源_队列立即可领_响应形状按契约() throws Exception {
        Source s = insertSource("队列形状源", "0 0 6 * * *", null);
        MvcResult result = mockMvc.perform(get("/api/v1/internal/ingest-queue")
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andReturn();
        JsonNode items = objectMapper.readTree(
                result.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("items");
        JsonNode mine = findItem(items, s.getId());
        assertThat(mine).isNotNull();
        assertThat(mine.path("name").asText()).isEqualTo("队列形状源");
        assertThat(mine.path("adapter").asText()).isEqualTo("ccgp");
        assertThat(mine.path("url").asText()).isEqualTo("http://shape-test.example/");
        assertThat(mine.path("config").path("list_selector").asText()).isEqualTo("li");
        assertThat(mine.has("last_run_at")).isFalse();   // 从未跑过,无该字段
    }

    @Test
    void 重复领取无副作用_last_run_at不被队列改动() throws Exception {
        Source s = insertSource("副作用源", "0 0 6 * * *", OffsetDateTime.now().minusDays(2));
        for (int i = 0; i < 2; i++) {
            mockMvc.perform(get("/api/v1/internal/ingest-queue").header("X-Internal-Key", "test-key"))
                    .andExpect(status().isOk());
        }
        Source after = sourceMapper.selectById(s.getId());
        // TIMESTAMPTZ 微秒精度回读有舍入,容差 5ms 内即视为未被改动
        assertThat(java.time.Duration.between(s.getLastRunAt().toInstant(), after.getLastRunAt().toInstant()).abs())
                .isLessThan(java.time.Duration.ofMillis(5));
    }

    @Test
    void 刚跑过与停用的源_不在队列() throws Exception {
        Source fresh = insertSource("刚跑过", "0 0 6 * * *", OffsetDateTime.now());        // next 恒在未来 → 不到期
        Source disabled = insertSource("停用", "0 0 6 * * *", OffsetDateTime.now().minusDays(2));
        disabled.setEnabled(false);
        sourceMapper.updateById(disabled);
        Source manual = insertSource("无cron仅手动", null, OffsetDateTime.now().minusDays(2));

        JsonNode items = queueItems();
        assertThat(findItem(items, fresh.getId())).isNull();
        assertThat(findItem(items, disabled.getId())).isNull();
        assertThat(findItem(items, manual.getId())).isNull();
        Source overdue = insertSource("过期该跑", "0 0 6 * * *", OffsetDateTime.now().minusDays(2));
        assertThat(findItem(queueItems(), overdue.getId())).isNotNull();
    }

    @Test
    void 手动触发run_下轮队列立即可领_D2() throws Exception {
        Source s = insertSource("D2手动触发", "0 0 6 * * *", OffsetDateTime.now());
        assertThat(findItem(queueItems(), s.getId())).isNull();   // 刚回报过,不到期
        String token = login("admin@sense2act.local", "admin123");
        mockMvc.perform(post("/api/v1/sources/{id}/run", s.getId()).header("Authorization", "Bearer " + token))
                .andExpect(status().isOk());
        assertThat(findItem(queueItems(), s.getId())).isNotNull();
    }

    // ---------- E1-3 批量推文档 ----------

    @Test
    void 批量推文档_去重_机构归一_快照_org_stats() throws Exception {
        Source s = insertSource("接入源", "0 0 6 * * *", null);
        String body = """
                {"source_id":"%s","items":[
                  {"url":"http://ingest.example/notice/1","doc_type":"announcement",
                   "title":"某医院AI辅助诊断系统采购公告","org_name":"（某市）人民医院",
                   "amount":"100.00","publish_date":"%s","region":"华东","category":"医疗信息化",
                   "content_text":"内容甲","raw_html":"<html>页面甲</html>"},
                  {"url":"http://ingest.example/notice/2/","doc_type":"announcement",
                   "title":"某医院CT设备采购公告","org_name":"  (某市)人民医院  ",
                   "amount":"200.00","publish_date":"%s","category":"医疗信息化",
                   "content_text":"内容乙"},
                  {"url":"http://ingest.example/notice/1#frag","doc_type":"announcement",
                   "title":"某医院AI辅助诊断系统采购公告","content_text":"内容甲"},
                  {"url":"http://ingest.example/notice/3","doc_type":"announcement",
                   "title":"某医院AI辅助诊断系统采购公告","publish_date":"%s","content_text":"内容甲"},
                  {"url":"http://ingest.example/notice/4","doc_type":"tender",
                   "title":"坏类型","content_text":"内容丙"},
                  {"url":"http://ingest.example/notice/5","doc_type":"announcement",
                   "title":"坏金额","amount":"abc","content_text":"内容丁"}
                ]}
                """.formatted(s.getId(), LocalDate.now(), LocalDate.now(), LocalDate.now());

        mockMvc.perform(post("/api/v1/internal/documents")
                        .header("X-Internal-Key", "test-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.accepted").value(2))
                .andExpect(jsonPath("$.data.duplicates").value(2))     // 1 条 URL 重复 + 1 条内容重复
                .andExpect(jsonPath("$.data.failed").value(2))         // 坏 doc_type + 坏 amount
                .andExpect(jsonPath("$.data.document_ids").isArray())
                .andExpect(jsonPath("$.data.document_ids.length()").value(2));

        // 两条入库文档挂到同一个机构(全半角 + 空白归一)
        List<Document> docs = documentMapper.selectList(
                new LambdaQueryWrapper<Document>()
                        .eq(Document::getSourceId, s.getId()));
        assertThat(docs).hasSize(2);
        assertThat(docs.get(0).getOrgId()).isEqualTo(docs.get(1).getOrgId());
        assertThat(docs.stream().map(Document::getUrl))
                .containsExactlyInAnyOrder("http://ingest.example/notice/1", "http://ingest.example/notice/2");   // 尾斜杠已归一
        assertThat(docs.get(0).getSignalScanned()).isFalse();

        Organization org = organizationMapper.selectById(docs.get(0).getOrgId());
        assertThat(org.getName()).isEqualTo("(某市)人民医院");   // 全角括号已归一

        // org_stats 重算:样本 2、均值 150、样本标准差 √5000、P95 195、近 30 天日均 2/30
        var stats = jdbcTemplate.queryForMap(
                "SELECT sample_count, amount_mean, amount_std, amount_p95, freq_mean_30d, last_doc_at "
                        + "FROM org_stats WHERE org_id = ?", org.getId());
        assertThat(((Number) stats.get("sample_count")).intValue()).isEqualTo(2);
        assertThat(((java.math.BigDecimal) stats.get("amount_mean")).compareTo(new java.math.BigDecimal("150"))).isZero();
        // 样本标准差(除 n-1):√5000 ≈ 70.71(列 NUMERIC(18,2) 已舍入)
        assertThat(((java.math.BigDecimal) stats.get("amount_std")).compareTo(new java.math.BigDecimal("70.71"))).isZero();
        assertThat(((java.math.BigDecimal) stats.get("amount_p95")).compareTo(new java.math.BigDecimal("195"))).isZero();
        assertThat(((java.math.BigDecimal) stats.get("freq_mean_30d")).compareTo(new java.math.BigDecimal("0.0667"))).isZero();
        assertThat(((java.sql.Date) stats.get("last_doc_at")).toLocalDate()).isEqualTo(LocalDate.now());

        // raw_html 落快照,内容寻址一个文件
        try (var files = Files.list(snapshotDir)) {
            assertThat(files.filter(p -> p.toString().endsWith(".html")).count()).isEqualTo(1);
        }
        Document withHtml = docs.stream()
                .filter(d -> d.getSnapshotKey() != null).findFirst().orElseThrow();
        assertThat(Files.readString(snapshotDir.resolve(withHtml.getSnapshotKey()))).isEqualTo("<html>页面甲</html>");
        // raw 保留原始字段(除 raw_html)
        assertThat(withHtml.getRaw()).containsKey("org_name");
        assertThat(withHtml.getRaw()).doesNotContainKey("raw_html");
    }

    @Test
    void 推文档_源不存在40401_items空40001() throws Exception {
        mockMvc.perform(post("/api/v1/internal/documents")
                        .header("X-Internal-Key", "test-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"source_id\":\"src_NOPE\",\"items\":[{\"url\":\"http://x\",\"doc_type\":\"news\",\"title\":\"t\"}]}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
        Source s = insertSource("空批次源", null, null);
        mockMvc.perform(post("/api/v1/internal/documents")
                        .header("X-Internal-Key", "test-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"source_id\":\"%s\",\"items\":[]}".formatted(s.getId())))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
    }

    // ---------- E1-4 运行回报与降级 ----------

    @Test
    void 回报ok_状态与时间更新() throws Exception {
        Source s = insertSource("ok回报源", "0 0 6 * * *", null);
        mockMvc.perform(post("/api/v1/internal/ingest-runs")
                        .header("X-Internal-Key", "test-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"source_id\":\"%s\",\"ok\":true,"
                                + "\"stats\":{\"fetched\":30,\"accepted\":24,\"duplicates\":6,\"failed\":0},"
                                + "\"errors\":[{\"url\":\"http://x/1\",\"stage\":\"fetch\",\"error\":\"timeout\"},"
                                + "{\"url\":\"http://x/2\",\"stage\":\"normalize\",\"error\":\"parse\"}]}")
                                .formatted(s.getId())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.health").value("ok"))
                .andExpect(jsonPath("$.data.last_run_status").value("ok"));

        Source after = sourceMapper.selectById(s.getId());
        assertThat(after.getLastRunAt()).isNotNull();
        assertThat(after.getConsecutiveFailures()).isZero();
        assertThat(ingestErrorMapper.selectCount(
                new LambdaQueryWrapper<IngestError>()
                        .eq(IngestError::getSourceId, s.getId()))).isEqualTo(2);
    }

    @Test
    void 连续失败降级_degraded与down_成功即恢复() throws Exception {
        Source s = insertSource("降级源", "0 0 6 * * *", null);
        report(s.getId(), false);
        report(s.getId(), false);
        assertThat(sourceMapper.selectById(s.getId()).getHealth()).isEqualTo("ok");
        report(s.getId(), false);
        assertThat(sourceMapper.selectById(s.getId()).getHealth()).isEqualTo("degraded");   // D3:3 次

        report(s.getId(), true);
        Source recovered = sourceMapper.selectById(s.getId());
        assertThat(recovered.getHealth()).isEqualTo("ok");            // D3:成功即恢复
        assertThat(recovered.getConsecutiveFailures()).isZero();

        for (int i = 0; i < 10; i++) {
            report(s.getId(), false);
        }
        assertThat(sourceMapper.selectById(s.getId()).getHealth()).isEqualTo("down");        // D3:10 次

        long events = applicationEvents.stream(SourceDegradedEvent.class)
                .filter(e -> e.sourceId().equals(s.getId())).count();
        assertThat(events).isEqualTo(3);   // 首次 degraded + 恢复后再降级 + down;恢复本身不发事件
    }

    @Test
    void 回报stage非法_40001_且不留半截写入() throws Exception {
        Source s = insertSource("非法stage源", "0 0 6 * * *", null);
        report(s.getId(), true);   // 先留一个已知状态
        Source before = sourceMapper.selectById(s.getId());
        mockMvc.perform(post("/api/v1/internal/ingest-runs")
                        .header("X-Internal-Key", "test-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"source_id\":\"%s\",\"ok\":false,\"errors\":[{\"stage\":\"bad-stage\"}]}"
                                .formatted(s.getId())))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        Source after = sourceMapper.selectById(s.getId());
        assertThat(after.getLastRunStatus()).isEqualTo(before.getLastRunStatus());   // 整批拒绝
        assertThat(ingestErrorMapper.selectCount(
                new LambdaQueryWrapper<IngestError>()
                        .eq(IngestError::getSourceId, s.getId()))).isZero();
    }

    // ---------- 工具 ----------

    private void report(String sourceId, boolean ok) throws Exception {
        mockMvc.perform(post("/api/v1/internal/ingest-runs")
                        .header("X-Internal-Key", "test-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"source_id\":\"%s\",\"ok\":%s}".formatted(sourceId, ok)))
                .andExpect(status().isOk());
    }

    private JsonNode queueItems() throws Exception {
        MvcResult result = mockMvc.perform(get("/api/v1/internal/ingest-queue")
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andReturn();
        return objectMapper.readTree(
                result.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("items");
    }

    private static JsonNode findItem(JsonNode items, String sourceId) {
        for (JsonNode item : items) {
            if (sourceId.equals(item.path("source_id").asText())) {
                return item;
            }
        }
        return null;
    }

    private Source insertSource(String name, String cron, OffsetDateTime lastRunAt) {
        Source s = new Source();
        s.setId(IdGen.next("src"));
        s.setName(name);
        s.setType("web_page");
        s.setUrl("http://shape-test.example/");
        s.setAdapter("ccgp");
        s.setScheduleCron(cron);
        s.setConfig(java.util.Map.of("list_selector", "li"));
        s.setEnabled(true);
        s.setHealth("ok");
        s.setLastRunAt(lastRunAt);
        s.setConsecutiveFailures(0);
        sourceMapper.insert(s);
        return s;
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
