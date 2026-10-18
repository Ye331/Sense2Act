package com.sense2act.backend.api;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.rule.SignalRuleService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

/** 规则版本管理(E2-6,契约 §8)。/admin/** 整组仅 admin(配置层已封,analyst 40301)。 */
@RestController
@RequestMapping("/api/v1/admin/signal-rules")
public class AdminSignalRuleController {

    private final SignalRuleService ruleService;

    public AdminSignalRuleController(SignalRuleService ruleService) {
        this.ruleService = ruleService;
    }

    @GetMapping
    public ApiResponse<List<SignalRuleService.RuleView>> list() {
        return ApiResponse.ok(ruleService.list());
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record RulePost(String name, String type, Map<String, Object> params, BigDecimal weight) {
    }

    @PostMapping
    public ApiResponse<SignalRuleService.RuleView> create(@RequestBody RulePost req) {
        return ApiResponse.ok(ruleService.create(req.name(), req.type(), req.params(), req.weight()));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record RulePatch(String name, String type, Map<String, Object> params, BigDecimal weight,
                            Boolean enabled) {
    }

    /** PATCH 生成新版本并停用旧版;同 id 只有一个版本 enabled。 */
    @PatchMapping("/{id}")
    public ApiResponse<SignalRuleService.RuleView> patch(@PathVariable String id,
                                                         @RequestBody RulePatch req) {
        return ApiResponse.ok(ruleService.patch(id, req.name(), req.type(), req.params(),
                req.weight(), req.enabled()));
    }
}
