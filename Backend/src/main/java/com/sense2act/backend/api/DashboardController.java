package com.sense2act.backend.api;

import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.dashboard.DashboardService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** 看板总览(E5-2,契约 §2 看板组)。读接口,任意登录角色。 */
@RestController
public class DashboardController {

    private final DashboardService dashboardService;

    public DashboardController(DashboardService dashboardService) {
        this.dashboardService = dashboardService;
    }

    @GetMapping("/api/v1/dashboard/summary")
    public ApiResponse<Map<String, Object>> summary() {
        return ApiResponse.ok(dashboardService.summary());
    }
}
