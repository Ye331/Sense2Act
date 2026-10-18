package com.sense2act.backend.api;

import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.domain.signal.SignalService;
import com.sense2act.backend.domain.signal.SignalView;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** 信号查询与决策(契约 §4)。读任意登录角色;决策/开调查按方法级规则需 admin/analyst。 */
@RestController
@RequestMapping("/api/v1/signals")
public class SignalController {

    private final SignalService signalService;

    public SignalController(SignalService signalService) {
        this.signalService = signalService;
    }

    @GetMapping
    public ApiResponse<PageResult<SignalView>> list(@RequestParam(required = false) String status,
                                                    @RequestParam(required = false) String rule_type,
                                                    @RequestParam(required = false) String score_gte,
                                                    @RequestParam(required = false) String org_id,
                                                    @RequestParam(required = false) String date_from,
                                                    @RequestParam(required = false) String date_to,
                                                    @RequestParam(required = false) Integer page,
                                                    @RequestParam(required = false) Integer page_size) {
        return ApiResponse.ok(signalService.list(status, rule_type, score_gte, org_id,
                date_from, date_to, page, page_size));
    }

    @GetMapping("/{id}")
    public ApiResponse<SignalView> detail(@PathVariable String id) {
        return ApiResponse.ok(signalService.detail(id));
    }

    public record StatusPatch(String status, String reason) {
    }

    @PatchMapping("/{id}/status")
    public ApiResponse<SignalView> decide(@PathVariable String id, @RequestBody StatusPatch req,
                                          @AuthenticationPrincipal Jwt jwt) {
        return ApiResponse.ok(signalService.decide(id, req.status(), req.reason(), jwt.getSubject()));
    }

    @PostMapping("/{id}/investigate")
    public ApiResponse<Map<String, Object>> investigate(@PathVariable String id) {
        return ApiResponse.ok(signalService.investigate(id));
    }
}
