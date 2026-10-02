package com.sense2act.backend.ingest;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.investigation.InvestigationService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 调查 Agent 接入面(§9.3,X-Internal-Key 认证由 InternalKeyFilter 完成):
 * 领取 → start → context → 按轮上报 steps。
 */
@RestController
@RequestMapping("/api/v1/internal/investigations")
public class InternalInvestigationController {

    private final InvestigationService investigationService;

    public InternalInvestigationController(InvestigationService investigationService) {
        this.investigationService = investigationService;
    }

    @GetMapping
    public ApiResponse<Map<String, Object>> claim(@RequestParam(required = false) String status,
                                                  @RequestParam(required = false) Integer limit) {
        List<InvestigationService.InvView> items = investigationService.claimList(status, limit);
        return ApiResponse.ok(Map.of("items", items));
    }

    @PostMapping("/{id}/start")
    public ApiResponse<InvestigationService.InvView> start(@PathVariable String id) {
        return ApiResponse.ok(investigationService.start(id));
    }

    @GetMapping("/{id}/context")
    public ApiResponse<InvestigationService.ContextView> context(@PathVariable String id) {
        return ApiResponse.ok(investigationService.context(id));
    }

    /** 契约 §9.3:{event, round, payload, token_usage};事件名映射见 §7,token_usage 累加进预算。 */
    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record StepPost(String event, Integer round, Map<String, Object> payload, Integer tokenUsage) {
    }

    @PostMapping("/{id}/steps")
    public ApiResponse<InvestigationService.StepView> postStep(@PathVariable String id,
                                                               @RequestBody StepPost req) {
        return ApiResponse.ok(investigationService.appendStep(id, req.event(), req.round(),
                req.payload(), req.tokenUsage()));
    }
}
