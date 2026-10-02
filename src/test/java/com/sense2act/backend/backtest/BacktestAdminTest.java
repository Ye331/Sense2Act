package com.sense2act.backend.backtest;

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
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E5-4 回测验收:建任务(queued)→ 导数据集(区间内文档+信号+反馈标注,转 running)
 * → 外部写回 result(done/failed,终态 40901)。规则引用校验、日期闸门、/admin 权限。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class BacktestAdminTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    DocumentMapper documentMapper;

    String docId;
    String sigId;
    String runA;   // 走 done
    String runB;   // 走 failed

    void seed() throws Exception {
        if (docId != null) {
            return;
        }
        String suffix = String.valueOf(System.nanoTime());
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType("announcement");
        d.setTitle("回测文档-" + suffix);
        d.setContentText("正文-" + suffix);
        d.setOrgId(null);
        d.setAmount(new BigDecimal("2200000.00"));
        d.setPublishDate(LocalDate.now().minusDays(1));
        d.setRegion("华东/某省某市");
        d.setCategory("医疗信息化");
        d.setUrl("http://bt.example/" + suffix);
        d.setRaw(Map.of("k", "v"));
        d.setContentHash("hash-" + suffix);
        d.setSignalScanned(true);
        documentMapper.insert(d);
        docId = d.getId();
        MvcResult r = mockMvc.perform(post("/api/v1/internal/signals")
                        .header("X-Internal-Key", "test-key").contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"document\":{\"id\":\"%s\"},\"score\":0.66,\"detector\":\"det\","
                                + "\"hits\":[{\"rule_id\":\"rule_seed01\",\"rule_version\":1,"
                                + "\"rule_type\":\"amount_anomaly\",\"weight\":0.8,\"detail\":{\"z\":3.9}}]}")
                                .formatted(docId)))
                .andExpect(status().isOk())
                .andReturn();
        sigId = read(r, "data.signal_id");
        // 信号级反馈(confirm)→ 进数据集的 feedback_events 段
        mockMvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .patch("/api/v1/signals/{id}/status", sigId)
                        .header("Authorization", "Bearer " + login("analyst@sense2act.local", "analyst123"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"confirmed\"}"))
                .andExpect(status().isOk());
    }

    @Test
    @Order(1)
    void 建任务_闸门与权限() throws Exception {
        seed();
        String admin = "Bearer " + login("admin@sense2act.local", "admin123");
        mockMvc.perform(get("/api/v1/admin/backtests")
                        .header("Authorization", "Bearer " + login("analyst@sense2act.local", "analyst123")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value(40301));
        mockMvc.perform(post("/api/v1/admin/backtests").header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"rule_id\":\"rule_seed01\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(post("/api/v1/admin/backtests").header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"rule_id\":\"rule_seed01\",\"date_from\":\"2026-10-05\","
                                + "\"date_to\":\"2026-10-01\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(post("/api/v1/admin/backtests").header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"rule_id\":\"rule_NOPE\",\"date_from\":\"2026-01-01\",\"date_to\":\"2026-12-31\"}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
        MvcResult a = mockMvc.perform(post("/api/v1/admin/backtests").header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"rule_id\":\"rule_seed01\",\"param_grid\":{\"k\":[1.5,2.0,2.5]},"
                                + "\"date_from\":\"2026-01-01\",\"date_to\":\"2026-12-31\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("queued"))
                .andExpect(jsonPath("$.data.param_grid.k.length()").value(3))
                .andReturn();
        runA = read(a, "data.id");
        MvcResult b = mockMvc.perform(post("/api/v1/admin/backtests").header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"rule_id\":\"rule_seed02\",\"date_from\":\"2026-01-01\",\"date_to\":\"2026-12-31\"}"))
                .andExpect(status().isOk())
                .andReturn();
        runB = read(b, "data.id");
        mockMvc.perform(get("/api/v1/admin/backtests").header("Authorization", admin))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(2));
    }

    @Test
    @Order(2)
    void 导数据集_领取即running_三段齐全() throws Exception {
        seed();
        String admin = "Bearer " + login("admin@sense2act.local", "admin123");
        MvcResult r = mockMvc.perform(get("/api/v1/admin/backtests/{id}/dataset", runA)
                        .header("Authorization", admin))
                .andExpect(status().isOk())
                .andReturn();
        JsonNode data = objectMapper.readTree(r.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .path("data");
        assertThat(data.path("rule_id").asText()).isEqualTo("rule_seed01");
        assertThat(data.path("documents").toString()).contains(docId);
        assertThat(data.path("signals").toString()).contains(sigId).contains("rule_seed01");
        assertThat(data.path("feedback_events").toString()).contains(sigId).contains("confirm");
        mockMvc.perform(get("/api/v1/admin/backtests/{id}", runA).header("Authorization", admin))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("running"));
        mockMvc.perform(get("/api/v1/admin/backtests/bt_NOPE/dataset").header("Authorization", admin))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }

    @Test
    @Order(3)
    void 结果写回_done带result_failed终态40901() throws Exception {
        seed();
        String admin = "Bearer " + login("admin@sense2act.local", "admin123");
        mockMvc.perform(put("/api/v1/admin/backtests/{id}/result", runB).header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"bogus\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(put("/api/v1/admin/backtests/{id}/result", runB).header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"done\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        // runA:done + 指标 → 详情可见
        mockMvc.perform(put("/api/v1/admin/backtests/{id}/result", runA).header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"done\",\"result\":{\"precision\":0.72,\"recall\":0.61,"
                                + "\"evaluated_at\":\"2026-10-02\"}}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("done"))
                .andExpect(jsonPath("$.data.result.precision").value(0.72));
        mockMvc.perform(put("/api/v1/admin/backtests/{id}/result", runA).header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"failed\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));
        // runB:failed(可带原因进 result)
        mockMvc.perform(put("/api/v1/admin/backtests/{id}/result", runB).header("Authorization", admin)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"failed\",\"result\":{\"error\":\"数据集为空\"}}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("failed"))
                .andExpect(jsonPath("$.data.result.error").value("数据集为空"));
    }

    // ---------- 工具 ----------

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
}
