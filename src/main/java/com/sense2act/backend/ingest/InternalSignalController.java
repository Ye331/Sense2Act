package com.sense2act.backend.ingest;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.domain.signal.SignalService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

/**
 * 信号发现服务接入面(§9.2,X-Internal-Key 认证由 InternalKeyFilter 完成):
 * 拉检测队列 → 推送命中(幂等)→ 回报扫描完成。
 */
@RestController
@RequestMapping("/api/v1/internal")
public class InternalSignalController {

    private final SignalService signalService;

    public InternalSignalController(SignalService signalService) {
        this.signalService = signalService;
    }

    @GetMapping("/detection-queue")
    public ApiResponse<Map<String, Object>> queue(@RequestParam(required = false) Integer limit) {
        List<SignalService.QueueDoc> items = signalService.detectionQueue(limit);
        return ApiResponse.ok(Map.of("items", items));
    }

    public record SignalPush(Map<String, String> document, List<Map<String, Object>> hits,
                             BigDecimal score, String detector) {
    }

    @PostMapping("/signals")
    public ApiResponse<SignalService.PushResult> push(@RequestBody SignalPush req) {
        if (req.document() == null || req.document().get("id") == null) {
            throw BusinessException.badRequest("document.id 必填");
        }
        return ApiResponse.ok(signalService.push(req.document().get("id"), req.hits(),
                req.score(), req.detector()));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record ScanComplete(List<String> documentIds, String detector) {
    }

    @PostMapping("/detection-scan-complete")
    public ApiResponse<Map<String, Object>> scanComplete(@RequestBody ScanComplete req) {
        int updated = signalService.scanComplete(req.documentIds());
        return ApiResponse.ok(Map.of("updated", updated));
    }
}
