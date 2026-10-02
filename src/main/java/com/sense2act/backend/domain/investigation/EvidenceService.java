package com.sense2act.backend.domain.investigation;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.config.AppProperties;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.HexFormat;
import java.util.Set;

/**
 * 证据登记与查询(E4-1/E4-5):引本库文档传 doc_id(回填 url/title/published_at),
 * 外部内容由后端抓快照算 SHA-256;抓取失败整条拒收 42201(D4:证据不可只有链接没有快照)。
 */
@Service
public class EvidenceService {

    /** data-model §2:evidences.source_type 词表。 */
    private static final Set<String> SOURCE_TYPES =
            Set.of("announcement", "policy", "news", "company_record", "web", "internal_stat");

    private static final Duration FETCH_TIMEOUT = Duration.ofSeconds(5);
    private static final int FETCH_RETRIES = 2;          // 首次 + 重试 2 次
    private static final int MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024;

    private final EvidenceMapper evidenceMapper;
    private final InvestigationMapper investigationMapper;
    private final DocumentMapper documentMapper;
    private final Path snapshotDir;
    private final HttpClient httpClient;

    public EvidenceService(EvidenceMapper evidenceMapper, InvestigationMapper investigationMapper,
                           DocumentMapper documentMapper, AppProperties props) {
        this.evidenceMapper = evidenceMapper;
        this.investigationMapper = investigationMapper;
        this.documentMapper = documentMapper;
        // normalize 必不可少:默认值 "./snapshots" 在 Windows 下带 "." 分量,resolve().normalize() 后
        // startsWith(带点目录) 恒 false,穿越防护会把合法快照也判成不存在
        this.snapshotDir = Path.of(props.snapshotDir() == null ? "snapshots" : props.snapshotDir()).normalize();
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(FETCH_TIMEOUT)
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
    }

    // ---------- E4-1 登记(内部) ----------

    @Transactional
    public EvidenceView register(String investigationId, String sourceType, String docId, String title,
                                 String url, String excerpt, String publishedAt) {
        Investigation inv = investigationMapper.selectById(investigationId);
        if (inv == null) {
            throw BusinessException.notFound("调查不存在: " + investigationId);
        }
        if (!Investigation.STATUS_INVESTIGATING.equals(inv.getStatus())) {
            throw BusinessException.conflict("只有 investigating 状态可登记证据,当前: " + inv.getStatus());
        }
        if (sourceType == null || !SOURCE_TYPES.contains(sourceType)) {
            throw BusinessException.badRequest("source_type 非法: " + sourceType);
        }
        if (excerpt == null || excerpt.isBlank()) {
            throw BusinessException.badRequest("excerpt 必填(证据须带摘录)");
        }

        Evidence ev = new Evidence();
        ev.setId(IdGen.next("ev"));
        ev.setInvestigationId(investigationId);
        ev.setSourceType(sourceType);
        ev.setExcerpt(excerpt.strip());
        ev.setFetchedAt(OffsetDateTime.now());

        if (docId != null && !docId.isBlank()) {
            // 变体一:引本库文档 —— url/title/published_at/hash/快照一律回填自 documents
            Document doc = documentMapper.selectById(docId);
            if (doc == null) {
                throw BusinessException.unprocessable("doc_id 引用的文档不存在: " + docId);
            }
            ev.setDocId(doc.getId());
            ev.setTitle(title == null || title.isBlank() ? doc.getTitle() : title.strip());
            ev.setUrl(doc.getUrl());
            if (doc.getPublishDate() != null) {
                ev.setPublishedAt(doc.getPublishDate().atStartOfDay(ZoneId.systemDefault()).toOffsetDateTime());
            }
            ev.setContentHash(doc.getContentHash());
            ev.setSnapshotKey(doc.getSnapshotKey());
        } else {
            // 变体二:外部内容 —— url/title/published_at 缺一不可,后端抓快照算 hash
            if (url == null || url.isBlank() || title == null || title.isBlank()) {
                throw BusinessException.badRequest("外部证据 url/title 必填");
            }
            OffsetDateTime published = parsePublishedAt(publishedAt);
            if (published == null) {
                throw BusinessException.badRequest("外部证据 published_at 必填(yyyy-MM-dd 或 ISO 时间)");
            }
            ev.setUrl(url.strip());
            ev.setTitle(title.strip());
            ev.setPublishedAt(published);
            byte[] snapshot = fetchSnapshot(ev.getUrl());
            String hash = sha256Hex(snapshot);
            ev.setContentHash(hash);
            ev.setSnapshotKey("ev_" + hash);
            writeSnapshot(ev.getSnapshotKey(), snapshot);
        }

        evidenceMapper.insert(ev);
        return detail(ev.getId());
    }

    // ---------- E4-5 查询(用户) ----------

    @Transactional(readOnly = true)
    public EvidenceView detail(String id) {
        Evidence ev = evidenceMapper.selectById(id);
        if (ev == null) {
            throw BusinessException.notFound("证据不存在: " + id);
        }
        return EvidenceView.from(ev);
    }

    /** 快照文件;无 snapshot_key 或文件缺失 40401。路径穿越防护同 DocumentService。 */
    @Transactional(readOnly = true)
    public Path snapshotFile(String id) {
        Evidence ev = evidenceMapper.selectById(id);
        if (ev == null) {
            throw BusinessException.notFound("证据不存在: " + id);
        }
        if (ev.getSnapshotKey() == null) {
            throw BusinessException.notFound("该证据没有快照: " + id);
        }
        Path file = snapshotDir.resolve(ev.getSnapshotKey()).normalize();
        if (!file.startsWith(snapshotDir) || !Files.exists(file)) {
            throw BusinessException.notFound("快照文件不存在: " + ev.getSnapshotKey());
        }
        return file;
    }

    // ---------- 内部 ----------

    /** GET 抓快照:5s 超时,最多 1+2 次尝试,2xx 才算成;任一次失败重试,全败 42201(D4 整条拒收)。 */
    private byte[] fetchSnapshot(String url) {
        URI uri;
        try {
            uri = URI.create(url);
        } catch (IllegalArgumentException e) {
            throw BusinessException.badRequest("url 非法: " + url);
        }
        if (!"http".equalsIgnoreCase(uri.getScheme()) && !"https".equalsIgnoreCase(uri.getScheme())) {
            throw BusinessException.badRequest("url 只支持 http/https: " + url);
        }
        HttpRequest request = HttpRequest.newBuilder(uri).timeout(FETCH_TIMEOUT).GET().build();
        IOException last = null;
        for (int attempt = 0; attempt <= FETCH_RETRIES; attempt++) {
            try {
                HttpResponse<byte[]> resp = httpClient.send(request, HttpResponse.BodyHandlers.ofByteArray());
                if (resp.statusCode() / 100 == 2) {
                    byte[] body = resp.body();
                    if (body.length > MAX_SNAPSHOT_BYTES) {
                        throw BusinessException.unprocessable(
                                "快照超过大小上限(" + body.length + " > " + MAX_SNAPSHOT_BYTES + "): " + url);
                    }
                    return body;
                }
                // 4xx/5xx 视同本次失败,继续重试
            } catch (IOException e) {
                last = e;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw BusinessException.unprocessable("快照抓取被中断: " + url);
            }
        }
        String cause = last == null ? "非 2xx 响应" : last.getMessage();
        throw BusinessException.unprocessable("快照抓取失败(" + (FETCH_RETRIES + 1) + " 次尝试," + cause + "): " + url);
    }

    private void writeSnapshot(String key, byte[] bytes) {
        try {
            Files.createDirectories(snapshotDir);
            Files.write(snapshotDir.resolve(key), bytes);
        } catch (IOException e) {
            throw new IllegalStateException("写快照文件失败: " + key, e);
        }
    }

    private static String sha256Hex(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }

    /** published_at 宽松解析:yyyy-MM-dd 或带时区的 ISO 时间;空返回 null 由调用方判定必填。 */
    private static OffsetDateTime parsePublishedAt(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        String v = raw.trim();
        try {
            return OffsetDateTime.parse(v);
        } catch (Exception ignore) {
            // 落到日期解析
        }
        try {
            return LocalDate.parse(v).atStartOfDay(ZoneId.systemDefault()).toOffsetDateTime();
        } catch (Exception ignore) {
            // 再试 Instant(以 Z 结尾的 UTC 时间)
        }
        try {
            return Instant.parse(v).atOffset(OffsetDateTime.now().getOffset());
        } catch (Exception e) {
            throw BusinessException.badRequest("published_at 需为 yyyy-MM-dd 或 ISO 时间: " + raw);
        }
    }

    // ---------- 视图 ----------

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record EvidenceView(String id, String investigationId, String sourceType, String title, String url,
                               String excerpt, OffsetDateTime publishedAt, OffsetDateTime fetchedAt,
                               String contentHash, String snapshotKey, String docId) {
        static EvidenceView from(Evidence ev) {
            return new EvidenceView(ev.getId(), ev.getInvestigationId(), ev.getSourceType(), ev.getTitle(),
                    ev.getUrl(), ev.getExcerpt(), ev.getPublishedAt(), ev.getFetchedAt(),
                    ev.getContentHash(), ev.getSnapshotKey(), ev.getDocId());
        }
    }
}
