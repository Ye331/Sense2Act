package com.sense2act.backend.api;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.domain.feedback.ReportFeedbackService;
import com.sense2act.backend.domain.report.ReportService;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * 报告页数据(E4-4)与导出(E4-6)。读接口任意登录角色;
 * md 同步返回,pdf 回 202+task_id 占位(真实排版 S8,OQ5)。
 * 反馈(E5-1)写操作,analyst/admin 可用(viewer 由方法级规则挡)。
 */
@RestController
public class ReportController {

    private final ReportService reportService;
    private final ReportFeedbackService feedbackService;

    public ReportController(ReportService reportService, ReportFeedbackService feedbackService) {
        this.reportService = reportService;
        this.feedbackService = feedbackService;
    }

    @GetMapping("/api/v1/reports")
    public ApiResponse<PageResult<ReportService.ReportListItem>> list(
            @RequestParam(required = false) String investigation_id,
            @RequestParam(required = false) String date_from,
            @RequestParam(required = false) String date_to,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer page_size) {
        return ApiResponse.ok(reportService.list(investigation_id, date_from, date_to, page, page_size));
    }

    @GetMapping("/api/v1/reports/{id}")
    public ApiResponse<ReportService.ReportView> detail(@PathVariable String id) {
        return ApiResponse.ok(reportService.detail(id));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record FeedbackPost(List<String> adoptedSuggestionIds, String comment, String flag) {
    }

    /** 报告反馈(契约 §6):采纳建议回填 adopted_at,评语/标误报写 feedback_events,user 取自 JWT。 */
    @PostMapping("/api/v1/reports/{id}/feedback")
    public ApiResponse<ReportFeedbackService.FeedbackView> feedback(@PathVariable String id,
                                                                    @RequestBody FeedbackPost req,
                                                                    @AuthenticationPrincipal Jwt jwt) {
        return ApiResponse.ok(feedbackService.submit(id, req.adoptedSuggestionIds(), req.comment(),
                req.flag(), jwt.getSubject()));
    }

    @GetMapping("/api/v1/reports/{id}/export")
    public Object export(@PathVariable String id, @RequestParam(required = false) String format) {
        String f = format == null || format.isBlank() ? "md" : format.trim();
        return switch (f) {
            case "md" -> md(id);
            case "pdf" -> ResponseEntity.accepted().body(ApiResponse.ok(reportService.exportPdfTask(id)));
            default -> throw com.sense2act.backend.common.BusinessException.badRequest(
                    "format 只支持 md/pdf: " + f);
        };
    }

    private ResponseEntity<byte[]> md(String id) {
        byte[] body = reportService.exportMarkdown(id).getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType("text/markdown;charset=UTF-8"))
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + id + ".md\"")
                .body(body);
    }
}
