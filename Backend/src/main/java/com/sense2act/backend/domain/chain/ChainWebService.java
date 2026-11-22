package com.sense2act.backend.domain.chain;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.config.AppProperties;
import com.sense2act.backend.domain.investigation.InvestigationMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/** Agent 共享卷的只读 chain_web.v1 投影。绝不修改产物或台账。 */
@Service
public class ChainWebService {
    private static final Logger log = LoggerFactory.getLogger(ChainWebService.class);
    private static final Pattern SAFE_ID = Pattern.compile("inv_[A-Za-z0-9_-]{1,120}");
    private static final String STATUS_SCHEMA = "chain_artifact_status.v1";
    private static final String WEB_SCHEMA = "chain_web.v1";
    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 100;

    private final Path root;
    private final long maxBytes;
    private final ObjectMapper objectMapper;
    private final InvestigationMapper investigationMapper;

    public ChainWebService(AppProperties props, ObjectMapper objectMapper,
                           InvestigationMapper investigationMapper) {
        this.root = Path.of(props.agentDataDir()).toAbsolutePath().normalize();
        this.maxBytes = props.attachmentMaxBytes() == null ? 10_485_760L : props.attachmentMaxBytes();
        this.objectMapper = objectMapper;
        this.investigationMapper = investigationMapper;
    }

    public PageResult<Map<String, Object>> list(Integer page, Integer pageSize) {
        int p = page == null ? 1 : page;
        int size = pageSize == null ? DEFAULT_PAGE_SIZE : pageSize;
        if (p < 1 || size < 1 || size > MAX_PAGE_SIZE) {
            throw BusinessException.badRequest("page/page_size 非法");
        }
        List<Map<String, Object>> all = new ArrayList<>();
        if (Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS) && !Files.isSymbolicLink(root)) {
            try (DirectoryStream<Path> stream = Files.newDirectoryStream(root)) {
                for (Path dir : stream) {
                    if (!Files.isDirectory(dir, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(dir)) continue;
                    String id = dir.getFileName().toString();
                    if (!SAFE_ID.matcher(id).matches()) continue;
                    Map<String, Object> view = readStatusVisible(id);
                    if (view != null) all.add(view);
                }
            } catch (IOException exc) {
                throw new IllegalStateException("读取证据链目录失败", exc);
            }
        }
        all.sort(Comparator.comparing(ChainWebService::generatedAtSortKey).reversed());
        int from = Math.min((p - 1) * size, all.size());
        int to = Math.min(from + size, all.size());
        return new PageResult<>(all.subList(from, to), all.size(), p, size);
    }

    public Map<String, Object> status(String investigationId) {
        requireInvestigation(investigationId);
        Map<String, Object> status = readStatusVisible(investigationId);
        if (status == null) throw BusinessException.notFound("该调查尚未生成证据链: " + investigationId);
        return status;
    }

    public JsonNode detail(String investigationId) {
        requireInvestigation(investigationId);
        Map<String, Object> status = status(investigationId);
        if (!"ready".equals(status.get("status"))) {
            throw BusinessException.unprocessable("证据链当前不可用: " + status.get("status"));
        }
        Path artifact = safeFile(investigationId, "chain.web.json");
        JsonNode payload = readJson(artifact);
        if (!validPayload(payload, investigationId)) {
            log.warn("invalid chain artifact investigation={} path={}", investigationId, artifact);
            throw BusinessException.unprocessable("证据链产物未通过 schema/归属校验");
        }
        return payload;
    }

    private Map<String, Object> readStatusVisible(String id) {
        Path path;
        try {
            path = safeFile(id, "chain.status.json");
        } catch (BusinessException exc) {
            log.warn("unsafe chain status investigation={}", id);
            return Map.of("investigation_id", id, "status", "invalid",
                    "error_code", "unsafe_path", "error_message", "证据链状态文件不安全");
        }
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) return null;
        try {
            JsonNode node = readJson(path);
            if (!STATUS_SCHEMA.equals(node.path("schema_version").asText())
                    || !id.equals(node.path("investigation_id").asText())) {
                throw new IllegalArgumentException("status schema/归属不匹配");
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> raw = objectMapper.convertValue(node, Map.class);
            return raw;
        } catch (RuntimeException exc) {
            log.warn("invalid chain status investigation={} reason={}", id, exc.getClass().getSimpleName());
            return Map.of("investigation_id", id, "status", "invalid",
                    "error_code", "invalid_status", "error_message", "证据链状态文件无效");
        }
    }

    private Path safeFile(String id, String file) {
        if (id == null || !SAFE_ID.matcher(id).matches()) {
            throw BusinessException.badRequest("investigation id 非法");
        }
        Path dir = root.resolve(id).normalize();
        Path runs = dir.resolve("runs").normalize();
        Path target = runs.resolve(file).normalize();
        if (!dir.startsWith(root) || !runs.startsWith(dir) || !target.startsWith(runs)
                || Files.isSymbolicLink(dir) || Files.isSymbolicLink(runs) || Files.isSymbolicLink(target)) {
            throw BusinessException.badRequest("非法证据链路径");
        }
        return target;
    }

    private JsonNode readJson(Path path) {
        try {
            if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(path)) {
                throw BusinessException.notFound("证据链文件不存在");
            }
            long size = Files.size(path);
            if (size <= 0 || size > maxBytes) throw BusinessException.unprocessable("证据链文件为空或超限");
            try (InputStream input = Files.newInputStream(path)) {
                return objectMapper.readTree(input);
            }
        } catch (BusinessException exc) {
            throw exc;
        } catch (IOException exc) {
            throw BusinessException.unprocessable("证据链 JSON 无法读取");
        }
    }

    private boolean validPayload(JsonNode payload, String investigationId) {
        return payload != null && payload.isObject()
                && WEB_SCHEMA.equals(payload.path("schema_version").asText())
                && payload.path("event").path("event_id").isTextual()
                && payload.path("nodes").isArray() && payload.path("edges").isArray()
                && payload.path("meta").isObject()
                && investigationId.equals(payload.path("meta").path("investigation_id").asText(investigationId));
    }

    private void requireInvestigation(String id) {
        if (id == null || !SAFE_ID.matcher(id).matches()) throw BusinessException.badRequest("investigation id 非法");
        if (investigationMapper.selectById(id) == null) throw BusinessException.notFound("调查不存在: " + id);
    }

    private static OffsetDateTime generatedAtSortKey(Map<String, Object> item) {
        try {
            return OffsetDateTime.parse(String.valueOf(item.getOrDefault("generated_at", "1970-01-01T00:00:00Z")));
        } catch (DateTimeParseException exc) {
            return OffsetDateTime.MIN;
        }
    }
}
