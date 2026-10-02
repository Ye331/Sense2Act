package com.sense2act.backend.ingest;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.investigation.EvidenceService;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 证据登记(契约 §9.3,E4-1):引本库文档传 doc_id;外部内容传 url/title/excerpt,后端抓快照算 hash。 */
@RestController
@RequestMapping("/api/v1/internal/investigations")
public class InternalEvidenceController {

    private final EvidenceService evidenceService;

    public InternalEvidenceController(EvidenceService evidenceService) {
        this.evidenceService = evidenceService;
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record EvidencePost(String sourceType, String docId, String title, String url,
                               String excerpt, String publishedAt) {
    }

    @PostMapping("/{id}/evidences")
    public ApiResponse<EvidenceService.EvidenceView> register(@PathVariable String id,
                                                              @RequestBody EvidencePost req) {
        return ApiResponse.ok(evidenceService.register(id, req.sourceType(), req.docId(), req.title(),
                req.url(), req.excerpt(), req.publishedAt()));
    }
}
