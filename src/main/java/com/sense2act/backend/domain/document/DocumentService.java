package com.sense2act.backend.domain.document;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.PageParams;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.config.AppProperties;
import com.sense2act.backend.domain.embedding.EmbeddingService;
import com.sense2act.backend.domain.org.Organization;
import com.sense2act.backend.domain.org.OrganizationMapper;
import com.sense2act.backend.domain.signal.Signal;
import com.sense2act.backend.domain.signal.SignalMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 文档查询(E1-5)与语义检索(E1-6,决策 D12)。
 * 检索三路:纯关键词 / 纯语义(cosine)/ keyword+semantic 的 RRF 融合;
 * 语义端点不可用时降级为关键词(仅有 semantic 时用语义文本当关键词),不报错。
 * sort 在语义模式下被相关性排序覆盖;默认 publish_date desc(NULLS LAST)。
 */
@Service
public class DocumentService {

    private static final Set<String> DOC_TYPES = Set.of("announcement", "policy", "news");
    private static final Set<String> SORTS = Set.of(
            "publish_date", "-publish_date", "amount", "-amount", "created_at", "-created_at");

    private final DocumentMapper documentMapper;
    private final OrganizationMapper organizationMapper;
    private final SignalMapper signalMapper;
    private final EmbeddingService embeddingService;
    private final Path snapshotDir;

    public DocumentService(DocumentMapper documentMapper, OrganizationMapper organizationMapper,
                           SignalMapper signalMapper, EmbeddingService embeddingService, AppProperties props) {
        this.documentMapper = documentMapper;
        this.organizationMapper = organizationMapper;
        this.signalMapper = signalMapper;
        this.embeddingService = embeddingService;
        this.snapshotDir = Path.of(props.snapshotDir() == null ? "snapshots" : props.snapshotDir());
    }

    // ---------- 查询 ----------

    @Transactional(readOnly = true)
    public PageResult<DocView> search(String keyword, String semantic, String docType, String orgId,
                                      String region, String category, String amountGte, String amountLte,
                                      String dateFrom, String dateTo, String sort, PageParams page) {
        String type = validateDocType(docType);
        String orderBy = validateSort(sort);
        BigDecimal gte = parseAmount(amountGte, "amount_gte");
        BigDecimal lte = parseAmount(amountLte, "amount_lte");
        LocalDate from = parseDate(dateFrom, "date_from");
        LocalDate to = parseDate(dateTo, "date_to");
        String sem = blankToNull(semantic);
        String kw = blankToNull(keyword);

        List<Document> docs;
        long total;
        Optional<float[]> queryVec = sem == null ? Optional.empty() : embeddingService.embedQuery(sem);
        if (queryVec.isPresent()) {
            String vectorLiteral = EmbeddingService.toVectorLiteral(queryVec.get());
            List<String> patterns = patterns(kw);
            if (patterns.isEmpty()) {
                docs = documentMapper.searchSemantic(vectorLiteral, type, orgId, region, category,
                        gte, lte, from, to, page.pageSize(), page.offset());
                total = documentMapper.countSemantic(type, orgId, region, category, gte, lte, from, to);
            } else {
                docs = documentMapper.searchRrf(patterns, vectorLiteral, type, orgId, region, category,
                        gte, lte, from, to, page.pageSize(), page.offset());
                total = documentMapper.countRrf(patterns, type, orgId, region, category, gte, lte, from, to);
            }
        } else {
            // D12 降级:端点不可用/未配置,仅有 semantic 无 keyword → 语义文本按关键词兜底
            List<String> patterns = patterns(kw != null ? kw : sem);
            docs = documentMapper.searchKeyword(patterns, type, orgId, region, category,
                    gte, lte, from, to, orderBy, page.pageSize(), page.offset());
            total = documentMapper.countKeyword(patterns, type, orgId, region, category, gte, lte, from, to);
        }
        return PageResult.of(toViews(docs), total, page);
    }

    @Transactional(readOnly = true)
    public DocDetailView detail(String id) {
        Document d = mustGet(id);
        OrgRef org = d.getOrgId() == null ? null : orgRef(d.getOrgId());
        long count = signalMapper.selectCount(new LambdaQueryWrapper<Signal>()
                .eq(Signal::getDocumentId, id));
        List<Map<String, Object>> related = signalMapper.selectList(new LambdaQueryWrapper<Signal>()
                        .eq(Signal::getDocumentId, id)
                        .orderByDesc(Signal::getCreatedAt)
                        .last("LIMIT 10"))
                .stream()
                .map(s -> Map.<String, Object>of(
                        "id", s.getId(), "score", s.getScore().toPlainString(), "status", s.getStatus()))
                .toList();
        return new DocDetailView(d.getId(), d.getDocType(), d.getTitle(), org, d.getAmount(),
                d.getPublishDate(), d.getDeadline(), d.getRegion(), d.getCategory(), d.getUrl(),
                (int) count, d.getContentText(), d.getRaw(), related);
    }

    /** 机构画像的"近期文档":publish_date desc 前 N 条。 */
    @Transactional(readOnly = true)
    public List<DocView> recentByOrg(String orgId, int limit) {
        return toViews(documentMapper.searchKeyword(
                List.of(), null, orgId, null, null, null, null, null, null, "-publish_date", limit, 0));
    }

    /** 原始页面快照文件;不存在 40401。路径做穿越防护(key 理论上是服务端生成的 sha256 文件名)。 */
    @Transactional(readOnly = true)
    public Path snapshot(String id) {
        Document d = mustGet(id);
        if (d.getSnapshotKey() == null) {
            throw BusinessException.notFound("该文档没有原始页面快照: " + id);
        }
        Path file = snapshotDir.resolve(d.getSnapshotKey()).normalize();
        if (!file.startsWith(snapshotDir) || !Files.exists(file)) {
            throw BusinessException.notFound("快照文件不存在: " + d.getSnapshotKey());
        }
        return file;
    }

    // ---------- 视图 ----------

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record OrgRef(String id, String name) {
    }

    /** 列表页字段 = api-design §3 示例。amount 按约定序列化为字符串小数。 */
    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record DocView(String id, String docType, String title, OrgRef org,
                          @JsonFormat(shape = JsonFormat.Shape.STRING) BigDecimal amount,
                          LocalDate publishDate, LocalDate deadline, String region, String category,
                          String url, Integer signalCount) {
    }

    /** 详情 = 列表字段 + content_text + raw + related_signals(近 10 条,含 id/score/status,E2-2 起接真值)。 */
    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record DocDetailView(String id, String docType, String title, OrgRef org,
                                @JsonFormat(shape = JsonFormat.Shape.STRING) BigDecimal amount,
                                LocalDate publishDate, LocalDate deadline, String region, String category,
                                String url, Integer signalCount, String contentText,
                                Map<String, Object> raw, List<Map<String, Object>> relatedSignals) {
    }

    private List<DocView> toViews(List<Document> docs) {
        Set<String> orgIds = docs.stream().map(Document::getOrgId).filter(Objects::nonNull).collect(Collectors.toSet());
        Map<String, String> names = orgIds.isEmpty() ? Map.of()
                : organizationMapper.selectBatchIds(orgIds).stream()
                        .collect(Collectors.toMap(Organization::getId, Organization::getName));
        Map<String, Long> counts = signalCounts(docs.stream().map(Document::getId).toList());
        return docs.stream()
                .map(d -> new DocView(d.getId(), d.getDocType(), d.getTitle(),
                        d.getOrgId() == null ? null : new OrgRef(d.getOrgId(), names.get(d.getOrgId())),
                        d.getAmount(), d.getPublishDate(), d.getDeadline(), d.getRegion(), d.getCategory(),
                        d.getUrl(), counts.getOrDefault(d.getId(), 0L).intValue()))
                .toList();
    }

    private OrgRef orgRef(String orgId) {
        Organization org = organizationMapper.selectById(orgId);
        return org == null ? new OrgRef(orgId, null) : new OrgRef(org.getId(), org.getName());
    }

    /** 列表页 signal_count 批量计数(按 document_id 聚一次,避免逐行查)。 */
    private Map<String, Long> signalCounts(java.util.Collection<String> docIds) {
        if (docIds.isEmpty()) {
            return Map.of();
        }
        return signalMapper.selectMaps(new QueryWrapper<Signal>()
                        .select("document_id", "count(*) as cnt")
                        .in("document_id", docIds)
                        .groupBy("document_id"))
                .stream()
                .collect(Collectors.toMap(r -> (String) r.get("document_id"), r -> ((Number) r.get("cnt")).longValue()));
    }

    // ---------- 参数解析与校验 ----------

    private static String validateDocType(String docType) {
        String t = blankToNull(docType);
        if (t != null && !DOC_TYPES.contains(t)) {
            throw BusinessException.badRequest("doc_type 必须是 announcement/policy/news 之一: " + t);
        }
        return t;
    }

    private static String validateSort(String sort) {
        String s = blankToNull(sort);
        if (s != null && !SORTS.contains(s)) {
            throw BusinessException.badRequest("sort 非法,可选: publish_date/-publish_date/amount/-amount/created_at/-created_at");
        }
        return s == null ? "-publish_date" : s;
    }

    private static BigDecimal parseAmount(String raw, String name) {
        String s = blankToNull(raw);
        if (s == null) {
            return null;
        }
        try {
            return new BigDecimal(s);
        } catch (NumberFormatException e) {
            throw BusinessException.badRequest(name + " 需为数字: " + s);
        }
    }

    private static LocalDate parseDate(String raw, String name) {
        String s = blankToNull(raw);
        if (s == null) {
            return null;
        }
        try {
            return LocalDate.parse(s);
        } catch (java.time.format.DateTimeParseException e) {
            throw BusinessException.badRequest(name + " 需为 yyyy-MM-dd: " + s);
        }
    }

    /** 关键词按空白拆词(AND 语义),逐词转义 % _ \ 后包成 ILIKE 子串。 */
    private static List<String> patterns(String keyword) {
        if (keyword == null || keyword.isBlank()) {
            return List.of();
        }
        return java.util.Arrays.stream(keyword.trim().split("\\s+"))
                .map(t -> "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
                .toList();
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s.trim();
    }

    private Document mustGet(String id) {
        Document d = documentMapper.selectById(id);
        if (d == null) {
            throw BusinessException.notFound("文档不存在: " + id);
        }
        return d;
    }
}
