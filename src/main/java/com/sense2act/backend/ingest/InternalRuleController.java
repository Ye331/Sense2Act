package com.sense2act.backend.ingest;

import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.rule.SignalRuleService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import java.util.List;

/** 检测服务只读启用规则，无需管理员凭据。 */
@RestController
public class InternalRuleController {
    private final SignalRuleService rules;

    public InternalRuleController(SignalRuleService rules) {
        this.rules = rules;
    }

    @GetMapping("/api/v1/internal/signal-rules")
    public ApiResponse<List<SignalRuleService.RuleView>> list() {
        return ApiResponse.ok(rules.enabledRules());
    }
}
