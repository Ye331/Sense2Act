package com.sense2act.backend.ingest;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.config.AppProperties;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.org.OrgService;
import com.sense2act.backend.domain.org.OrgStatsMapper;
import com.sense2act.backend.domain.source.Source;
import com.sense2act.backend.domain.source.SourceMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * E1-3 批量文档接入(§9.1 POST /internal/documents):
 * URL 归一 → content_hash 去重 → 机构归一(org_id)→ raw_html 快照 → 入库 → 重算 org_stats。
 * 单条失败跳过计入 failed,不阻塞批次其余条目。
 *
 * 条目按"能违反库约束的字段全部先验后写"处理(枚举/长度/日期/金额/去重),
 * 使 insert 实际不可能撞约束;单批一个事务,避免坏条目污染整个事务。
 */
@Service
public class DocumentIngestService {

    private static final Logger log = LoggerFactory.getLogger(DocumentIngestService.class);

    private static final Set<String> DOC_TYPES = Set.of("announcement", "policy", "news");
    private static final int MAX_BATCH = 500;

    private final SourceMapper sourceMapper;
    private final DocumentMapper documentMapper;
    private final OrgService orgService;
    private final OrgStatsMapper orgStatsMapper;
    private final Path snapshotDir;

    public DocumentIngestService(SourceMapper sourceMapper, DocumentMapper documentMapper,
                                 OrgService orgService, OrgStatsMapper orgStatsMapper,
                                 AppProperties props) {
        this.sourceMapper = sourceMapper;
        this.documentMapper = documentMapper;
        this.orgService = orgService;
        this.orgStatsMapper = orgStatsMapper;
        this.snapshotDir = Path.of(props.snapshotDir() == null ? "snapshots" : props.snapshotDir());
    }

    @com.fasterxml.jackson.databind.annotation.JsonNaming(
            com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record BatchResult(int accepted, int duplicates, int failed, List<String> documentIds) {
    }

    @Transactional
    public BatchResult push(String sourceId, List<Map<String, Object>> items) {
        Source source = sourceMapper.selectById(sourceId);
        if (source == null) {
            throw BusinessException.notFound("信息源不存在: " + sourceId);
        }
        if (items == null || items.isEmpty()) {
            throw BusinessException.badRequest("items 不能为空");
        }
        if (items.size() > MAX_BATCH) {
            throw BusinessException.badRequest("单批上限 " + MAX_BATCH + " 条,请分批推送");
        }

        int accepted = 0;
        int duplicates = 0;
        int failed = 0;
        List<String> documentIds = new ArrayList<>();
        Set<String> affected = new LinkedHashSet<>();   // "orgId\u0000category"

        for (Map<String, Object> item : items) {
            try {
                Document doc = convert(source.getId(), item);
                if (doc == null) {
                    duplicates++;
                    continue;
                }
                documentMapper.insert(doc);
                documentIds.add(doc.getId());
                accepted++;
                if (doc.getOrgId() != null && doc.getCategory() != null) {
                    affected.add(doc.getOrgId() + "\u0000" + doc.getCategory());
                }
            } catch (Exception e) {
                failed++;
                log.warn("文档条目入库失败 url={} 原因={}", str(item, "url"), e.getMessage());
            }
        }
        for (String pair : affected) {
            int sep = pair.indexOf('\u0000');
            orgStatsMapper.recompute(pair.substring(0, sep), pair.substring(sep + 1));
        }
        log.info("批量接入完成 source={} accepted={} duplicates={} failed={}",
                sourceId, accepted, duplicates, failed);
        return new BatchResult(accepted, duplicates, failed, documentIds);
    }

    /** 单条转换;URL 或内容已存在返回 null(计重复);坏条目抛异常(计 failed)。 */
    private Document convert(String sourceId, Map<String, Object> item) {
        String docType = str(item, "doc_type");
        String title = str(item, "title");
        if (docType == null || !DOC_TYPES.contains(docType)) {
            throw new IllegalArgumentException("doc_type 缺失或非法: " + docType);
        }
        if (title == null || title.isBlank()) {
            throw new IllegalArgumentException("title 必填");
        }
        if (title.length() > 500) {
            throw new IllegalArgumentException("title 超长(>500)");
        }
        String url = UrlNormalizer.normalize(str(item, "url"));
        if (url == null || url.length() > 1000) {
            throw new IllegalArgumentException("url 缺失、非法或超长");
        }
        BigDecimal amount = parseAmount(str(item, "amount"));
        LocalDate publishDate = parseDate(str(item, "publish_date"));
        LocalDate deadline = parseDate(str(item, "deadline"));
        String region = lenLimited(str(item, "region"), 100);
        String category = lenLimited(str(item, "category"), 100);
        String contentText = str(item, "content_text");
        String orgId = orgService.resolveOrgId(str(item, "org_name"));
        String hash = contentHash(docType, title, publishDate, contentText);

        Long dup = documentMapper.selectCount(new LambdaQueryWrapper<Document>()
                .eq(Document::getUrl, url).or().eq(Document::getContentHash, hash));
        if (dup != null && dup > 0) {
            return null;
        }
        Document d = new Document();
        d.setId(IdGen.next("doc"));
        d.setSourceId(sourceId);
        d.setDocType(docType);
        d.setTitle(title);
        d.setContentText(contentText);
        d.setOrgId(orgId);
        d.setAmount(amount);
        d.setPublishDate(publishDate);
        d.setDeadline(deadline);
        d.setRegion(region);
        d.setCategory(category);
        d.setUrl(url);
        d.setRaw(rawWithoutHtml(item));
        d.setContentHash(hash);
        d.setSnapshotKey(writeSnapshot(str(item, "raw_html")));
        d.setSignalScanned(false);   // 检测幂等位,等信号服务来扫
        return d;
    }

    private Map<String, Object> rawWithoutHtml(Map<String, Object> item) {
        Map<String, Object> raw = new LinkedHashMap<>(item);
        raw.remove("raw_html");
        return raw;
    }

    /** raw_html 内容寻址落盘:<sha256>.html;相同页面只存一份。返回文件名作为 snapshot_key。 */
    private String writeSnapshot(String rawHtml) {
        if (rawHtml == null || rawHtml.isBlank()) {
            return null;
        }
        try {
            String key = sha256Hex(rawHtml) + ".html";
            Path file = snapshotDir.resolve(key);
            Files.createDirectories(snapshotDir);
            if (!Files.exists(file)) {
                Files.writeString(file, rawHtml, StandardCharsets.UTF_8);
            }
            return key;
        } catch (IOException e) {
            throw new IllegalStateException("快照写入失败: " + e.getMessage(), e);
        }
    }

    private static String contentHash(String docType, String title, LocalDate publishDate, String contentText) {
        String canonical = String.join("\n",
                docType, title, publishDate == null ? "" : publishDate.toString(),
                contentText == null ? "" : contentText);
        return sha256Hex(canonical);
    }

    private static String sha256Hex(String s) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(md.digest(s.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static BigDecimal parseAmount(String amount) {
        if (amount == null || amount.isBlank()) {
            return null;
        }
        try {
            BigDecimal v = new BigDecimal(amount.trim());
            return v.signum() < 0 ? null : v;
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("amount 非数字: " + amount);
        }
    }

    private static LocalDate parseDate(String date) {
        if (date == null || date.isBlank()) {
            return null;
        }
        return LocalDate.parse(date.trim());   // DateTimeParseException → 计 failed
    }

    private static String str(Map<String, Object> item, String key) {
        Object v = item.get(key);
        if (v == null) {
            return null;
        }
        String s = String.valueOf(v).trim();
        return s.isEmpty() ? null : s;
    }

    private static String lenLimited(String s, int max) {
        if (s == null) {
            return null;
        }
        return s.length() <= max ? s : s.substring(0, max);
    }
}
