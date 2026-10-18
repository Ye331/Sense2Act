package com.sense2act.backend.api;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.policy.InvestigationPolicy;
import com.sense2act.backend.domain.policy.PolicyService;
import com.sense2act.backend.domain.policy.WatchProfile;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;

/** 管理面(契约 §8):调查策略与关注画像(D13)。/api/v1/admin/** 整组仅 admin(SecurityConfig)。 */
@RestController
@RequestMapping("/api/v1/admin")
public class AdminPolicyController {

    private final PolicyService policyService;

    public AdminPolicyController(PolicyService policyService) {
        this.policyService = policyService;
    }

    @GetMapping("/investigation-policies")
    public ApiResponse<PolicyView> policies() {
        return ApiResponse.ok(PolicyView.of(policyService.policies()));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record PolicyPut(BigDecimal autoInvestigateThreshold, Integer maxConcurrentInvestigations,
                            Integer defaultMaxRounds, Integer defaultTokenBudget) {
    }

    @PutMapping("/investigation-policies")
    public ApiResponse<PolicyView> update(@RequestBody PolicyPut req) {
        return ApiResponse.ok(PolicyView.of(policyService.updatePolicies(req.autoInvestigateThreshold(),
                req.maxConcurrentInvestigations(), req.defaultMaxRounds(), req.defaultTokenBudget())));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record PolicyView(BigDecimal autoInvestigateThreshold, Integer maxConcurrentInvestigations,
                      Integer defaultMaxRounds, Integer defaultTokenBudget, OffsetDateTime updatedAt) {

        static PolicyView of(InvestigationPolicy p) {
            return new PolicyView(p.getAutoInvestigateThreshold(), p.getMaxConcurrentInvestigations(),
                    p.getDefaultMaxRounds(), p.getDefaultTokenBudget(), p.getUpdatedAt());
        }
    }

    // ---------- 关注画像(D13) ----------

    @GetMapping("/watch-profiles")
    public ApiResponse<List<ProfileView>> listProfiles() {
        return ApiResponse.ok(policyService.listProfiles().stream().map(ProfileView::of).toList());
    }

    public record ProfilePost(String name, String note) {
    }

    @PostMapping("/watch-profiles")
    public ApiResponse<ProfileView> createProfile(@RequestBody ProfilePost req) {
        return ApiResponse.ok(ProfileView.of(policyService.createProfile(req.name(), req.note())));
    }

    @DeleteMapping("/watch-profiles/{id}")
    public ApiResponse<Object> deleteProfile(@PathVariable String id) {
        policyService.deleteProfile(id);
        return ApiResponse.ok(null);
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record ProfileView(String id, String name, boolean enabled, String note, OffsetDateTime createdAt) {

        static ProfileView of(WatchProfile p) {
            return new ProfileView(p.getId(), p.getName(), Boolean.TRUE.equals(p.getEnabled()),
                    p.getNote(), p.getCreatedAt());
        }
    }
}
