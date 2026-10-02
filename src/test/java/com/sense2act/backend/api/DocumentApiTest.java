package com.sense2act.backend.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.org.Organization;
import com.sense2act.backend.domain.org.OrganizationMapper;
import com.sense2act.backend.domain.org.OrgStatsMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.web.util.UriComponentsBuilder;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.math.BigDecimal;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * E1-5 验收:多条件检索(§3 全部筛选 + 排序 + 分页)、详情、快照、机构画像,
 * 以及 §1 的双认证(数据查询接口 JWT 与 X-Internal-Key 都接受)。
 */
@SpringBootTest(properties = "app.internal-key=test-key")
@AutoConfigureMockMvc
@Testcontainers
@TestInstance(TestInstance.Lifecycle.PER_CLASS)   // 单实例共享一份种子,total 断言不被方法累积打爆
class DocumentApiTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");

    static Path snapshotDir;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        try {
            snapshotDir = Files.createTempDirectory("snapshots-doc-test");
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
    DocumentMapper documentMapper;

    @Autowired
    OrganizationMapper organizationMapper;

    @Autowired
    OrgStatsMapper orgStatsMapper;

    @Test
    void 列表默认按publish_date降序_分页形状() throws Exception {
        Fix f = seed();
        mockMvc.perform(get(docsUri(null, null)).header("Authorization", "Bearer " + login("viewer@sense2act.local", "viewer123")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(5))
                .andExpect(jsonPath("$.data.page").value(1))
                .andExpect(jsonPath("$.data.page_size").value(20))
                .andExpect(jsonPath("$.data.items.length()").value(5))
                .andExpect(jsonPath("$.data.items[0].title").value(f.title("新闻")))
                .andExpect(jsonPath("$.data.items[4].title").value(f.title("数据平台")));
    }

    @Test
    void keyword子串匹配标题与正文_多词AND() throws Exception {
        Fix f = seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(get(docsUri("keyword", "采购")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(2))
                .andExpect(jsonPath("$.data.items.length()").value(2))
                .andExpect(jsonPath("$.data.items[0].title").value(f.title("CT公告")))   // 默认 publish desc
                .andExpect(jsonPath("$.data.items[1].title").value(f.title("AI公告")));
        mockMvc.perform(get(docsUri("keyword", "数据要素")).header("Authorization", token))   // 只在正文
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.items[0].title").value(f.title("新闻")));
        mockMvc.perform(get(docsUri("keyword", "采购 公告")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(2));
    }

    @Test
    void 条件筛选_type_org_region前缀_category_金额区间_日期区间() throws Exception {
        Fix f = seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(get(docsUri("doc_type", "policy")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.items[0].title").value(f.title("政策")));
        mockMvc.perform(get(docsUri("org_id", f.orgA)).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(2))
                .andExpect(jsonPath("$.data.items[0].title").value(f.title("CT公告")));   // publish_date desc
        mockMvc.perform(get(docsUri("region", "华东")).header("Authorization", token))     // 层级前缀
                .andExpect(jsonPath("$.data.total").value(2));
        mockMvc.perform(get(docsUri("region", "华东/某省某市")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(2));
        mockMvc.perform(get(docsUri("category", "医疗设备")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(1));
        mockMvc.perform(get(docsUri("amount_gte", "150", "amount_lte", "400")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(1))                              // 100/500 出局,NULL 出局
                .andExpect(jsonPath("$.data.items[0].title").value(f.title("CT公告")));
        mockMvc.perform(get(docsUri("date_from", "2026-08-01", "date_to", "2026-09-07")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(3));   // CT09-05/AI09-01/政策08-20 在窗内
    }

    @Test
    void 排序与分页() throws Exception {
        Fix f = seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(get(docsUri("sort", "amount")).header("Authorization", token))
                .andExpect(jsonPath("$.data.items[0].title").value(f.title("AI公告")))      // 100 最小在前
                .andExpect(jsonPath("$.data.items[3].title").value(f.title("新闻")))        // NULL 最后按 publish desc
                .andExpect(jsonPath("$.data.items[4].title").value(f.title("政策")));
        mockMvc.perform(get(docsUri("page_size", "2", "page", "2")).header("Authorization", token))
                .andExpect(jsonPath("$.data.total").value(5))
                .andExpect(jsonPath("$.data.items.length()").value(2))
                .andExpect(jsonPath("$.data.items[0].title").value(f.title("AI公告")))      // 全局第 3 条
                .andExpect(jsonPath("$.data.items[1].title").value(f.title("政策")));
    }

    @Test
    void 非法参数_40001() throws Exception {
        String token = "Bearer " + login("admin@sense2act.local", "admin123");
        for (String[] pairs : new String[][]{
                {"doc_type", "tender"}, {"date_from", "not-a-date"}, {"amount_gte", "abc"},
                {"sort", "nope"}, {"page_size", "101"}}) {
            mockMvc.perform(get(docsUri(pairs)).header("Authorization", token))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value(40001));
        }
    }

    @Test
    void 详情_契约字段与raw() throws Exception {
        Fix f = seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        mockMvc.perform(get("/api/v1/documents/{id}", f.doc("AI公告")).header("Authorization", token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").value(f.doc("AI公告")))
                .andExpect(jsonPath("$.data.doc_type").value("announcement"))
                .andExpect(jsonPath("$.data.title").value(f.title("AI公告")))
                .andExpect(jsonPath("$.data.org.id").value(f.orgA))
                .andExpect(jsonPath("$.data.org.name").value(f.orgAName))
                .andExpect(jsonPath("$.data.amount").value("100.00"))          // 金额=字符串小数
                .andExpect(jsonPath("$.data.publish_date").value("2026-09-01"))
                .andExpect(jsonPath("$.data.region").value("华东/某省某市"))
                .andExpect(jsonPath("$.data.category").value("医疗信息化"))
                .andExpect(jsonPath("$.data.content_text").value("智慧医院建设总体方案"))
                .andExpect(jsonPath("$.data.raw.org_name").exists())
                .andExpect(jsonPath("$.data.signal_count").value(0))           // E2-2 前占位
                .andExpect(jsonPath("$.data.related_signals.length()").value(0));
        mockMvc.perform(get("/api/v1/documents/doc_NOPE").header("Authorization", token))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }

    @Test
    void 快照_原文返回_无快照40401() throws Exception {
        Fix f = seed();
        String token = "Bearer " + login("analyst@sense2act.local", "analyst123");
        MvcResult ok = mockMvc.perform(get("/api/v1/documents/{id}/snapshot", f.doc("AI公告"))
                        .header("Authorization", token))
                .andExpect(status().isOk())
                .andReturn();
        assertThat(ok.getResponse().getContentType()).startsWith("text/html");
        assertThat(ok.getResponse().getContentAsString(StandardCharsets.UTF_8))
                .isEqualTo(f.snapshotHtml);
        mockMvc.perform(get("/api/v1/documents/{id}/snapshot", f.doc("CT公告")).header("Authorization", token))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }

    @Test
    void 双认证_JWT任意角色或内部key() throws Exception {
        Fix f = seed();
        // 无凭证 → 40101
        mockMvc.perform(get("/api/v1/documents"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
        // 内部 key(§1/§9.3:Agent 服务读数据)
        mockMvc.perform(get("/api/v1/documents").header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(5));
        mockMvc.perform(get("/api/v1/organizations/{id}", f.orgA).header("X-Internal-Key", "test-key"))
                .andExpect(status().isOk());
        // 错 key → 40101,不静默落到 JWT
        mockMvc.perform(get("/api/v1/documents").header("X-Internal-Key", "wrong"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40101));
    }

    @Test
    void 机构画像_stats与近期文档() throws Exception {
        Fix f = seed();
        orgStatsMapper.recompute(f.orgA, "医疗信息化");
        orgStatsMapper.recompute(f.orgA, "医疗设备");
        String token = "Bearer " + login("viewer@sense2act.local", "viewer123");
        MvcResult result = mockMvc.perform(get("/api/v1/organizations/{id}", f.orgA)
                        .header("Authorization", token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").value(f.orgA))
                .andExpect(jsonPath("$.data.name").value(f.orgAName))
                .andExpect(jsonPath("$.data.stats.length()").value(2))
                .andExpect(jsonPath("$.data.recent_documents.length()").value(2))
                .andExpect(jsonPath("$.data.recent_documents[0].title").value(f.title("CT公告")))
                .andReturn();
        JsonNode stats = objectMapper.readTree(
                result.getResponse().getContentAsString(StandardCharsets.UTF_8)).path("data").path("stats");
        JsonNode medDevice = java.util.stream.IntStream.range(0, stats.size())
                .mapToObj(stats::get)
                .filter(s -> "医疗设备".equals(s.path("category").asText()))
                .findFirst().orElseThrow();
        assertThat(medDevice.path("sample_count").asInt()).isEqualTo(1);
        assertThat(medDevice.path("amount_mean").asText()).isEqualTo("200.00");   // 金额=字符串小数
        mockMvc.perform(get("/api/v1/organizations/org_NOPE").header("Authorization", token))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40401));
    }

    // ---------- 种子与工具 ----------

    private Fix fix;

    /** 五篇文档:两机构、三类型、金额/日期/地区/类别各异,一篇挂快照。全部方法共享这一份。 */
    private Fix seed() throws Exception {
        if (fix != null) {
            return fix;
        }
        String suffix = String.valueOf(System.nanoTime());
        String orgAName = "某市人民医院" + suffix;
        String orgBName = "某省中医院" + suffix;
        Organization orgA = insertOrg(orgAName);
        Organization orgB = insertOrg(orgBName);

        String snapKey = "snap-" + suffix + ".html";
        String html = "<html>原始页面-" + suffix + "</html>";
        Files.writeString(snapshotDir.resolve(snapKey), html, StandardCharsets.UTF_8);

        insertDoc("AI公告", orgA.getId(), "announcement", "AI辅助诊断系统采购公告", "100.00",
                "2026-09-01", "华东/某省某市", "医疗信息化", "智慧医院建设总体方案", snapKey, suffix);
        insertDoc("CT公告", orgA.getId(), "announcement", "CT设备采购公告", "200.00",
                "2026-09-05", "华东/某省某市", "医疗设备", "影像设备更新", null, suffix);
        insertDoc("政策", orgB.getId(), "policy", "信息化建设规划政策", null,
                "2026-08-20", "华北/北京市", "政策法规", "三年规划纲要", null, suffix);
        insertDoc("新闻", null, "news", "医疗信息化行业动态", null,
                "2026-09-10", null, null, "数据要素流通加速", null, suffix);
        insertDoc("数据平台", orgB.getId(), "announcement", "数据平台建设项目", "500.00",
                "2026-07-15", "华南/某省某市", "数据平台", "数据中台建设", null, suffix);
        fix = new Fix(orgA.getId(), orgAName, suffix, html);
        return fix;
    }

    private Organization insertOrg(String name) {
        Organization o = new Organization();
        o.setId(IdGen.next("org"));
        o.setName(name);
        o.setAliases(java.util.List.of());
        organizationMapper.insert(o);
        return o;
    }

    private void insertDoc(String shortName, String orgId, String docType, String title, String amount,
                           String publishDate, String region, String category, String contentText,
                           String snapshotKey, String suffix) {
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(null);
        d.setDocType(docType);
        d.setTitle(title + "-" + suffix);
        d.setContentText(contentText);
        d.setOrgId(orgId);
        d.setAmount(amount == null ? null : new BigDecimal(amount));
        d.setPublishDate(LocalDate.parse(publishDate));
        d.setRegion(region);
        d.setCategory(category);
        d.setUrl("http://doc-test.example/" + shortName + "/" + suffix);
        d.setRaw(Map.of("org_name", "原始名-" + shortName));
        d.setContentHash("hash-" + shortName + "-" + suffix);
        d.setSnapshotKey(snapshotKey);
        d.setSignalScanned(false);
        documentMapper.insert(d);
        ids.put(shortName + "-" + suffix, d.getId());
        fullTitles.put(shortName + "-" + suffix, d.getTitle());
    }

    /** 每个测试自己的种子集合(短名 → id / 完整标题)。 */
    private final Map<String, String> ids = new java.util.HashMap<>();
    private final Map<String, String> fullTitles = new java.util.HashMap<>();

    private class Fix {
        final String orgA;
        final String orgAName;
        final String suffix;
        final String snapshotHtml;

        Fix(String orgA, String orgAName, String suffix, String snapshotHtml) {
            this.orgA = orgA;
            this.orgAName = orgAName;
            this.suffix = suffix;
            this.snapshotHtml = snapshotHtml;
        }

        String doc(String shortName) {
            return ids.get(shortName + "-" + suffix);
        }

        String title(String shortName) {
            return fullTitles.get(shortName + "-" + suffix);
        }
    }

    private static URI docsUri(String... pairs) {
        UriComponentsBuilder b = UriComponentsBuilder.fromUriString("/api/v1/documents");
        if (pairs != null && pairs[0] != null) {
            for (int i = 0; i < pairs.length; i += 2) {
                b.queryParam(pairs[i], pairs[i + 1]);
            }
        }
        return b.build().encode().toUri();
    }

    private String login(String email, String password) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/auth/token")
                        .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"%s\",\"password\":\"%s\"}".formatted(email, password)))
                .andExpect(status().isOk())
                .andReturn();
        JsonNode node = objectMapper.readTree(
                result.getResponse().getContentAsString(StandardCharsets.UTF_8));
        return node.path("data").path("token").asText();
    }
}
