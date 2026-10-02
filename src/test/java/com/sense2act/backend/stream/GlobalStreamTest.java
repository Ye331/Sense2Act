package com.sense2act.backend.stream;

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
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.math.BigDecimal;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E2-7 全局通知流验收(契约 §7):四类事件落 global_events(SSE id 即主键),
 * 连接即补发、开着流实时收、Last-Event-ID 断线续传只补漏、?token= 认证、无效 token 40101。
 * 真实端口流式读(SseEmitter 在 MockMvc 下不可靠)。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class GlobalStreamTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    @LocalServerPort
    int port;

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    DocumentMapper documentMapper;

    String jwt;
    String invId;
    String docB;

    /** 全局事件自增 id 跨用例推进,断言续传只补漏。 */
    long lastId;

    void seed() throws Exception {
        if (jwt != null) {
            return;
        }
        String docA = insertDoc("gs-a");
        docB = insertDoc("gs-b");
        jwt = login("analyst@sense2act.local", "analyst123");
        // 推一条信号 → signal_created 落 global_events(事件 1)
        String sig = push(docA);
        MvcResult inv = mockMvc.perform(post("/api/v1/signals/{id}/investigate", sig)
                        .header("Authorization", "Bearer " + jwt))
                .andExpect(status().isOk())
                .andReturn();
        invId = objectMapper.readTree(inv.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("investigation_id").asText();
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/start", invId)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk());
    }

    @Test
    @Order(1)
    void 连接即补发_实时推送_token参数认证() throws Exception {
        seed();
        HttpClient client = HttpClient.newHttpClient();
        // 无 token → 40101(信封由认证链回)
        HttpResponse<String> denied = client.send(
                HttpRequest.newBuilder(URI.create(base() + "?token=junk")).GET().build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(denied.statusCode()).isEqualTo(401);
        assertThat(denied.body()).contains("40101");

        // ?token= 连接:补发已有的 signal_created
        HttpResponse<InputStream> stream = client.send(
                HttpRequest.newBuilder(URI.create(base() + "?token=" + jwt)).GET().build(),
                HttpResponse.BodyHandlers.ofInputStream());
        assertThat(stream.statusCode()).isEqualTo(200);
        SseReader reader = new SseReader(stream.body());
        List<Map<String, String>> replay = reader.readBlocks(1, 10_000);
        assertThat(replay).hasSize(1);
        assertThat(replay.get(0)).containsEntry("event", "signal_created");
        assertThat(replay.get(0).get("id")).matches("\\d+");
        assertThat(replay.get(0).get("data")).contains("\"signal_id\":\"sig_");
        lastId = Long.parseLong(replay.get(0).get("id"));

        // 开着流交报告并 complete → report_ready + investigation_completed 实时到达,id 递增
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/report", invId)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"title\":\"全局流验证报告\",\"claims\":[{\"text\":\"x\","
                                + "\"nature\":\"fact\",\"confidence\":0.9}]}"))
                .andExpect(status().isOk());
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/complete", invId)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk());
        List<Map<String, String>> live = reader.readBlocks(2, 10_000);
        assertThat(live).hasSize(2);
        assertThat(live.get(0)).containsEntry("event", "report_ready");
        assertThat(live.get(0).get("data")).contains("\"investigation_id\":\"" + invId + "\"")
                .contains("\"report_id\":\"rep_");
        assertThat(live.get(1)).containsEntry("event", "investigation_completed");
        assertThat(live.get(1).get("data")).contains("\"status\":\"completed\"");
        long id1 = Long.parseLong(live.get(0).get("id"));
        long id2 = Long.parseLong(live.get(1).get("id"));
        assertThat(id1).isGreaterThan(lastId);
        assertThat(id2).isGreaterThan(id1);
        lastId = id2;
        // global_events 留痕:可追溯(重连可补发)
        assertThat(jdbcCount("SELECT count(*) FROM global_events")).isEqualTo(3);
        reader.close();
    }

    @Test
    @Order(2)
    void 断线续传_LastEventID只补漏() throws Exception {
        seed();
        HttpClient client = HttpClient.newHttpClient();
        // 又来一条新信号 → 事件 4
        push(docB);
        // 上次读到事件 3:续传只补事件 4,不重放 1..3
        HttpResponse<InputStream> stream = client.send(
                HttpRequest.newBuilder(URI.create(base() + "?token=" + jwt))
                        .header("Last-Event-ID", String.valueOf(lastId)).GET().build(),
                HttpResponse.BodyHandlers.ofInputStream());
        SseReader reader = new SseReader(stream.body());
        List<Map<String, String>> resumed = reader.readBlocks(1, 10_000);
        assertThat(resumed).hasSize(1);
        assertThat(resumed.get(0)).containsEntry("event", "signal_created");
        assertThat(Long.parseLong(resumed.get(0).get("id"))).isGreaterThan(lastId);
        reader.close();
    }

    @Autowired
    org.springframework.jdbc.core.JdbcTemplate jdbcTemplate;

    private int jdbcCount(String sql) {
        Integer n = jdbcTemplate.queryForObject(sql, Integer.class);
        return n == null ? 0 : n;
    }

    private String base() {
        return "http://127.0.0.1:" + port + "/api/v1/stream";
    }

    private String insertDoc(String key) {
        String suffix = String.valueOf(System.nanoTime());
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType("announcement");
        d.setTitle("全局流文档-" + key);
        d.setContentText("正文-" + suffix);
        d.setOrgId(null);
        d.setAmount(new BigDecimal("750000.00"));
        d.setPublishDate(LocalDate.parse("2026-09-25"));
        d.setRegion("华东/某省某市");
        d.setCategory("医疗信息化");
        d.setUrl("http://gs.example/" + key);
        d.setRaw(Map.of("k", "v"));
        d.setContentHash("hash-" + key);
        d.setSignalScanned(true);
        documentMapper.insert(d);
        return d.getId();
    }

    private String push(String docId) throws Exception {
        MvcResult r = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"document\":{\"id\":\"%s\"},\"score\":0.7,\"detector\":\"det\","
                                + "\"hits\":[{\"rule_type\":\"semantic_match\",\"weight\":0.7,\"detail\":{\"similarity\":0.85}}]}")
                                .formatted(docId)))
                .andExpect(status().isOk())
                .andReturn();
        return read(r, "data.signal_id");
    }

    private String read(MvcResult r, String path) throws Exception {
        JsonNode node = objectMapper.readTree(r.getResponse().getContentAsString(StandardCharsets.UTF_8));
        for (String p : path.split("\\.")) {
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

    /** SSE 流式读:按空行切块,ready() 轮询;心跳注释行(":hb")跳过。同 InvestigationSseTest。 */
    static final class SseReader implements AutoCloseable {

        private final BufferedReader reader;

        SseReader(InputStream in) {
            this.reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        }

        List<Map<String, String>> readBlocks(int wanted, long timeoutMs)
                throws IOException, InterruptedException {
            List<Map<String, String>> blocks = new ArrayList<>();
            StringBuilder current = new StringBuilder();
            long deadline = System.currentTimeMillis() + timeoutMs;
            while (blocks.size() < wanted && System.currentTimeMillis() < deadline) {
                while (reader.ready()) {
                    String line = reader.readLine();
                    if (line == null) {
                        return blocks;
                    }
                    if (line.isEmpty()) {
                        Map<String, String> block = parse(current.toString());
                        current.setLength(0);
                        if (block != null) {
                            blocks.add(block);
                        }
                        continue;
                    }
                    current.append(line).append('\n');
                }
                Thread.sleep(30);
            }
            return blocks;
        }

        private static Map<String, String> parse(String raw) {
            Map<String, String> block = new LinkedHashMap<>();
            for (String line : raw.split("\n")) {
                if (line.isEmpty() || line.startsWith(":")) {
                    continue;
                }
                int colon = line.indexOf(':');
                if (colon <= 0) {
                    continue;
                }
                String field = line.substring(0, colon);
                String value = line.substring(colon + 1);
                if (value.startsWith(" ")) {
                    value = value.substring(1);
                }
                block.put(field, value);
            }
            return block.isEmpty() ? null : block;
        }

        @Override
        public void close() throws IOException {
            reader.close();
        }
    }
}
