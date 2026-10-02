package com.sense2act.backend.api;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.PageParams;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.domain.source.Source;
import com.sense2act.backend.domain.source.SourceService;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;
import java.util.Map;

/** 信息源管理(E1-1)。写操作仅 admin,越权 40301;config 原样存取。 */
@RestController
@RequestMapping("/api/v1/sources")
public class SourceController {

    private final SourceService sourceService;

    public SourceController(SourceService sourceService) {
        this.sourceService = sourceService;
    }

    @GetMapping
    public ApiResponse<PageResult<SourceView>> list(@RequestParam(required = false) Integer page,
                                                    @RequestParam(required = false) Integer page_size) {
        PageParams params = PageParams.of(page, page_size);
        PageResult<Source> result = sourceService.list(params);
        return ApiResponse.ok(PageResult.of(
                result.items().stream().map(SourceController::toView).toList(),
                result.total(), params));
    }

    @PostMapping
    @PreAuthorize("hasRole('ADMIN')")
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<SourceView> create(@RequestBody SourceService.SourceReq req) {
        return ApiResponse.ok(toView(sourceService.create(req)));
    }

    @PatchMapping("/{id}")
    @PreAuthorize("hasRole('ADMIN')")
    public ApiResponse<SourceView> update(@PathVariable String id, @RequestBody SourceService.SourceReq req) {
        return ApiResponse.ok(toView(sourceService.update(id, req)));
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasRole('ADMIN')")
    public ApiResponse<Void> delete(@PathVariable String id) {
        sourceService.delete(id);
        return ApiResponse.ok();
    }

    /** D2:手动触发 = 置为到期,下轮 queue 立即可领。 */
    @PostMapping("/{id}/run")
    @PreAuthorize("hasRole('ADMIN')")
    public ApiResponse<SourceView> run(@PathVariable String id) {
        return ApiResponse.ok(toView(sourceService.triggerRun(id)));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record SourceView(String id, String name, String type, String url, String adapter,
                      String scheduleCron, Map<String, Object> config, Boolean enabled,
                      String health, OffsetDateTime lastRunAt, String lastRunStatus,
                      OffsetDateTime createdAt, OffsetDateTime updatedAt) {
    }

    static SourceView toView(Source s) {
        return new SourceView(s.getId(), s.getName(), s.getType(), s.getUrl(), s.getAdapter(),
                s.getScheduleCron(), s.getConfig(), s.getEnabled(), s.getHealth(),
                s.getLastRunAt(), s.getLastRunStatus(), s.getCreatedAt(), s.getUpdatedAt());
    }
}
