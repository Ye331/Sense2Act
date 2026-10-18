package com.sense2act.backend.ingest;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.investigation.InvestigationService;
import com.sense2act.backend.domain.report.ReportService;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Agent 收尾通道(契约 §9.3,E4-2/E4-3):提交报告、完成、失败。 */
@RestController
@RequestMapping("/api/v1/internal/investigations")
public class InternalReportController {

    private final ReportService reportService;
    private final InvestigationService investigationService;

    public InternalReportController(ReportService reportService, InvestigationService investigationService) {
        this.reportService = reportService;
        this.investigationService = investigationService;
    }

    @PostMapping("/{id}/report")
    public ApiResponse<ReportService.ReportView> submit(@PathVariable String id,
                                                        @RequestBody ReportService.Draft draft) {
        return ApiResponse.ok(reportService.submit(id, draft));
    }

    @PostMapping("/{id}/complete")
    public ApiResponse<InvestigationService.InvView> complete(@PathVariable String id) {
        return ApiResponse.ok(investigationService.complete(id));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record FailPost(String error) {
    }

    @PostMapping("/{id}/fail")
    public ApiResponse<InvestigationService.InvView> fail(@PathVariable String id, @RequestBody FailPost req) {
        return ApiResponse.ok(investigationService.fail(id, req.error()));
    }
}
