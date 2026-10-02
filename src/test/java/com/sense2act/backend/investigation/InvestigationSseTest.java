package com.sense2act.backend.investigation;

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
 * E3-3 SSE 验收(契约 §7):连接即补发全部留痕步骤、id=seq、事件名按 content.event 还原、
 * 提交后实时推送(含派生 budget_update)、Last-Event-ID 断线续传、?token= 承载 JWT、25s 心跳机制存在。
 * 用真实端口流式读(SseEmitter 在 MockMvc 下不可靠)。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class InvestigationSseTest {

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

    String invId;
    String jwt;

    void seed() throws Exception {
        if (invId != null) {
            return;
        }
        String suffix = String.valueOf(System.nanoTime());
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType("announcement");
        d.setTitle("SSE 文档-" + suffix);
        d.setContentText("正文-" + suffix);
        d.setOrgId(null);
        d.setAmount(new BigDecimal("500000.00"));
        d.setPublishDate(LocalDate.parse("2026-09-25"));
        d.setRegion("华东/某省某市");
        d.setCategory("医疗信息化");
        d.setUrl("http://sse.example/" + suffix);
        d.setRaw(Map.of("k", "v"));
        d.setContentHash("hash-" + suffix);
        d.setSignalScanned(true);
        documentMapper.insert(d);

        MvcResult push = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"document\":{\"id\":\"%s\"},\"score\":0.6,\"detector\":\"det\","
                                + "\"hits\":[{\"rule_type\":\"semantic_match\",\"weight\":1.0,\"detail\":{\"similarity\":0.9}}]}")
                                .formatted(d.getId())))
                .andExpect(status().isOk())
                .andReturn();
        String sig = objectMapper.readTree(push.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("signal_id").asText();
        jwt = login("analyst@sense2act.local", "analyst123");
        MvcResult inv = mockMvc.perform(post("/api/v1/signals/{id}/investigate", sig)
                        .header("Authorization", "Bearer " + jwt))
                .andExpect(status().isOk())
                .andReturn();
        invId = objectMapper.readTree(inv.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data").path("investigation_id").asText();
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/start", invId)
                        .header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk());
        // 开流前先留两步:seq1 investigation_started(领取时自动)+ seq2 round_started
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invId)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"round_started\",\"round\":1,\"payload\":{\"round\":1}}"))
                .andExpect(status().isOk());
    }

    @Test
    @Order(1)
    void 连接即补发_实时推送_token参数认证() throws Exception {
        seed();
        HttpClient client = HttpClient.newHttpClient();
        // 无 token → 40101
        HttpResponse<String> denied = client.send(
                HttpRequest.newBuilder(URI.create(base() + "/stream")).GET().build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(denied.statusCode()).isEqualTo(401);
        assertThat(denied.body()).contains("40101");

        // ?token= 连接:补发 seq1/seq2
        HttpResponse<InputStream> stream = client.send(
                HttpRequest.newBuilder(URI.create(base() + "/stream?token=" + jwt)).GET().build(),
                HttpResponse.BodyHandlers.ofInputStream());
        assertThat(stream.statusCode()).isEqualTo(200);
        SseReader reader = new SseReader(stream.body());
        List<Map<String, String>> replay = reader.readBlocks(2, 10_000);
        assertThat(replay).hasSize(2);
        assertThat(replay.get(0)).containsEntry("id", "1").containsEntry("event", "investigation_started");
        assertThat(replay.get(0).get("data")).contains("\"max_rounds\":8");
        assertThat(replay.get(1)).containsEntry("id", "2").containsEntry("event", "round_started");

        // 开着流再写步骤 → 实时收到该步骤 + 派生 budget_update
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invId)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"tool_completed\",\"round\":1,\"payload\":"
                                + "{\"tool\":\"doc_search\",\"ok\":true,\"latency_ms\":80},\"token_usage\":800}"))
                .andExpect(status().isOk());
        List<Map<String, String>> live = reader.readBlocks(2, 10_000);
        assertThat(live).hasSize(2);
        assertThat(live.get(0)).containsEntry("id", "3").containsEntry("event", "tool_completed");
        assertThat(live.get(0).get("data")).contains("doc_search");
        assertThat(live.get(1)).containsEntry("id", "4").containsEntry("event", "budget_update");
        assertThat(live.get(1).get("data")).contains("\"token_used\":800");
        reader.close();
    }

    @Test
    @Order(2)
    void 断线续传_LastEventID只补漏() throws Exception {
        seed();
        HttpClient client = HttpClient.newHttpClient();
        // 上次读到 seq3:续传只补 seq4(budget_update),不重放 1..3
        HttpResponse<InputStream> stream = client.send(
                HttpRequest.newBuilder(URI.create(base() + "/stream?token=" + jwt))
                        .header("Last-Event-ID", "3").GET().build(),
                HttpResponse.BodyHandlers.ofInputStream());
        SseReader reader = new SseReader(stream.body());
        List<Map<String, String>> resumed = reader.readBlocks(1, 10_000);
        assertThat(resumed).hasSize(1);
        assertThat(resumed.get(0)).containsEntry("id", "4").containsEntry("event", "budget_update");
        // 续传流上继续实时收新步骤
        mockMvc.perform(post("/api/v1/internal/investigations/{id}/steps", invId)
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"event\":\"reflection_updated\",\"round\":1,\"payload\":"
                                + "{\"judgment\":\"证据偏向高价值信号\"}}"))
                .andExpect(status().isOk());
        List<Map<String, String>> tail = reader.readBlocks(1, 10_000);
        assertThat(tail).hasSize(1);
        assertThat(tail.get(0)).containsEntry("id", "5").containsEntry("event", "reflection_updated");
        reader.close();
        // 调查不存在 → 40401,不建立流
        HttpResponse<String> missing = client.send(
                HttpRequest.newBuilder(URI.create(base().replace(invId, "inv_NOPE") + "/stream?token=" + jwt))
                        .GET().build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(missing.statusCode()).isEqualTo(404);
        assertThat(missing.body()).contains("40401");
    }

    private String base() {
        return "http://127.0.0.1:" + port + "/api/v1/investigations/" + invId;
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

    /**
     * SSE 流式读:按空行切块,块内收集 id/event/data 行;
     * ready() 轮询等待,避免阻塞 read() 导致测试悬挂。心跳是注释行(":hb"),会被跳过。
     */
    static final class SseReader implements AutoCloseable {

        private final BufferedReader reader;
        private final List<String> pending = new ArrayList<>();

        SseReader(InputStream in) {
            this.reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        }

        List<Map<String, String>> readBlocks(int wanted, long timeoutMs) throws IOException, InterruptedException {
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
                if (line.isEmpty() || line.startsWith(":")) {   // 心跳注释行
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
