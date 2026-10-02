package com.sense2act.backend.ingest;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.domain.source.Source;
import com.sense2act.backend.domain.source.SourceService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/**
 * 爬虫服务接入面(§9.1,X-Internal-Key 认证由 InternalKeyFilter 完成):
 * 领取到期源 → 批量推文档 → 回报运行结果。领取无副作用不加锁,重复领取无害,入库去重兜底。
 */
@RestController
@RequestMapping("/api/v1/internal")
public class InternalIngestController {

    private final SourceService sourceService;
    private final DocumentIngestService documentIngestService;

    public InternalIngestController(SourceService sourceService, DocumentIngestService documentIngestService) {
        this.sourceService = sourceService;
        this.documentIngestService = documentIngestService;
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record QueueItem(String sourceId, String name, String adapter, String url,
                     Map<String, Object> config, OffsetDateTime lastRunAt) {
    }

    @GetMapping("/ingest-queue")
    public ApiResponse<Map<String, Object>> queue() {
        List<QueueItem> items = sourceService.dueSources().stream()
                .map(s -> new QueueItem(s.getId(), s.getName(), s.getAdapter(), s.getUrl(),
                        s.getConfig(), s.getLastRunAt()))
                .toList();
        return ApiResponse.ok(Map.of("items", items));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record PushRequest(String sourceId, List<Map<String, Object>> items) {
    }

    @PostMapping("/documents")
    public ApiResponse<DocumentIngestService.BatchResult> push(@RequestBody PushRequest req) {
        if (req.sourceId() == null || req.sourceId().isBlank()) {
            throw BusinessException.badRequest("source_id 必填");
        }
        return ApiResponse.ok(documentIngestService.push(req.sourceId(), req.items()));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record RunReport(String sourceId, boolean ok, Map<String, Object> stats,
                     List<SourceService.RunError> errors) {
    }

    @PostMapping("/ingest-runs")
    public ApiResponse<Map<String, Object>> reportRun(@RequestBody RunReport req) {
        if (req.sourceId() == null || req.sourceId().isBlank()) {
            throw BusinessException.badRequest("source_id 必填");
        }
        Source s = sourceService.recordRun(req.sourceId(), req.ok(),
                req.errors() == null ? List.of() : req.errors());
        return ApiResponse.ok(Map.of("source_id", s.getId(), "health", s.getHealth(),
                "last_run_status", s.getLastRunStatus()));
    }
}
