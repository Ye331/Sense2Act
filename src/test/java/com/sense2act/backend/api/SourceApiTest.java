package com.sense2act.backend.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import org.junit.jupiter.api.Test;
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

import java.nio.charset.StandardCharsets;

import static org.hamcrest.Matchers.startsWith;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** E1-1 信息源 CRUD + 手动触发验收:字段、权限(admin 之外 40301)、分页约定、删除保护。 */
@SpringBootTest
@AutoConfigureMockMvc
@Testcontainers
class SourceApiTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    DocumentMapper documentMapper;

    @Test
    void admin建源_201_字段原样存取() throws Exception {
        String token = login("admin@sense2act.local", "admin123");
        mockMvc.perform(post("/api/v1/sources")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"中国政府采购网-医疗设备","type":"web_page","url":"http://www.ccgp.gov.cn/",
                                 "adapter":"ccgp","schedule_cron":"0 0 6 * * *",
                                 "config":{"list_selector":".list li","detail_selector":".content"},
                                 "enabled":true}
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.id").value(startsWith("src_")))
                .andExpect(jsonPath("$.data.name").value("中国政府采购网-医疗设备"))
                .andExpect(jsonPath("$.data.type").value("web_page"))
                .andExpect(jsonPath("$.data.adapter").value("ccgp"))
                .andExpect(jsonPath("$.data.schedule_cron").value("0 0 6 * * *"))
                .andExpect(jsonPath("$.data.config.list_selector").value(".list li"))
                .andExpect(jsonPath("$.data.enabled").value(true))
                .andExpect(jsonPath("$.data.health").value("ok"))
                .andExpect(jsonPath("$.data.last_run_at").doesNotExist());
    }

    @Test
    void 建源校验_type与cron非法_40001() throws Exception {
        String token = login("admin@sense2act.local", "admin123");
        mockMvc.perform(post("/api/v1/sources").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"x\",\"type\":\"ftp\",\"url\":\"http://a\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
        mockMvc.perform(post("/api/v1/sources").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"x\",\"type\":\"rss\",\"url\":\"http://a\",\"schedule_cron\":\"not-a-cron\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
    }

    @Test
    void 非admin建源_40301() throws Exception {
        for (String email : new String[]{"analyst@sense2act.local", "viewer@sense2act.local"}) {
            String token = login(email, email.startsWith("analyst") ? "analyst123" : "viewer123");
            mockMvc.perform(post("/api/v1/sources")
                            .header("Authorization", "Bearer " + token)
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"name\":\"x\",\"type\":\"rss\",\"url\":\"http://a\"}"))
                    .andExpect(status().isForbidden())
                    .andExpect(jsonPath("$.code").value(40301));
        }
    }

    @Test
    void 列表分页_shape与上限() throws Exception {
        String token = login("admin@sense2act.local", "admin123");
        mockMvc.perform(get("/api/v1/sources").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.items").isArray())
                .andExpect(jsonPath("$.data.total").isNumber())
                .andExpect(jsonPath("$.data.page").value(1))
                .andExpect(jsonPath("$.data.page_size").value(20));
        mockMvc.perform(get("/api/v1/sources").header("Authorization", "Bearer " + token)
                        .param("page_size", "101"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40001));
    }

    @Test
    void patch部分更新_不改字段不动() throws Exception {
        String token = login("admin@sense2act.local", "admin123");
        String id = createSource(token, "patch测试源");
        mockMvc.perform(patch("/api/v1/sources/{id}", id).header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"改名后\",\"config\":{\"b\":2}}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.name").value("改名后"))
                .andExpect(jsonPath("$.data.config.b").value(2))
                .andExpect(jsonPath("$.data.config.list_selector").doesNotExist())
                .andExpect(jsonPath("$.data.url").value("http://patch-test.example/"))
                .andExpect(jsonPath("$.data.adapter").value("test"));
    }

    @Test
    void 删除保护_有文档40901_无文档200() throws Exception {
        String token = login("admin@sense2act.local", "admin123");
        String withDocs = createSource(token, "有文档的源");
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(withDocs);
        d.setDocType("news");
        d.setTitle("占位文档");
        d.setUrl("http://patch-test.example/doc-" + System.nanoTime());
        d.setContentHash("hash-" + System.nanoTime());
        d.setSignalScanned(false);
        documentMapper.insert(d);

        mockMvc.perform(delete("/api/v1/sources/{id}", withDocs).header("Authorization", "Bearer " + token))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value(40901));

        String empty = createSource(token, "空源");
        mockMvc.perform(delete("/api/v1/sources/{id}", empty).header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
        mockMvc.perform(post("/api/v1/sources/{id}/run", empty).header("Authorization", "Bearer " + token))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }

    @Test
    void run越权_40301_viewer可读列表() throws Exception {
        String admin = login("admin@sense2act.local", "admin123");
        String id = createSource(admin, "run权限源");
        String viewer = login("viewer@sense2act.local", "viewer123");
        mockMvc.perform(post("/api/v1/sources/{id}/run", id).header("Authorization", "Bearer " + viewer))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value(40301));
        mockMvc.perform(get("/api/v1/sources").header("Authorization", "Bearer " + viewer))
                .andExpect(status().isOk());
    }

    @Test
    void 不存在40401() throws Exception {
        String token = login("admin@sense2act.local", "admin123");
        mockMvc.perform(patch("/api/v1/sources/src_NOPE").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"x\"}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }

    private String createSource(String token, String name) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/sources")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(("{\"name\":\"%s\",\"type\":\"web_page\",\"url\":\"http://patch-test.example/\","
                                + "\"adapter\":\"test\",\"config\":{\"list_selector\":\"li\"}}")
                                .formatted(name)))
                .andExpect(status().isCreated())
                .andReturn();
        JsonNode node = objectMapper.readTree(
                result.getResponse().getContentAsString(StandardCharsets.UTF_8));
        return node.path("data").path("id").asText();
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
