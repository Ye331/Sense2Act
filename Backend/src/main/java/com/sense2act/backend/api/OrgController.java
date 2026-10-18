package com.sense2act.backend.api;

import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.org.OrgService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 机构画像(E1-5):基本信息 + org_stats + 近期文档。读接口,JWT 或 X-Internal-Key 均可(§1)。 */
@RestController
@RequestMapping("/api/v1/organizations")
public class OrgController {

    private final OrgService orgService;

    public OrgController(OrgService orgService) {
        this.orgService = orgService;
    }

    @GetMapping("/{id}")
    public ApiResponse<OrgService.OrgDetailView> detail(@PathVariable String id) {
        return ApiResponse.ok(orgService.detail(id));
    }
}
