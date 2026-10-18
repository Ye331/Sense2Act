package com.sense2act.backend.api;

import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.graph.GraphQueryService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** 事件图谱查询(E5-3,契约 §2 事件图谱组)。读接口,任意登录角色。 */
@RestController
public class EventController {

    private final GraphQueryService graphQueryService;

    public EventController(GraphQueryService graphQueryService) {
        this.graphQueryService = graphQueryService;
    }

    @GetMapping("/api/v1/events")
    public ApiResponse<Map<String, Object>> list(@RequestParam(required = false) Integer page,
                                                 @RequestParam(required = false) Integer page_size) {
        return ApiResponse.ok(graphQueryService.list(page, page_size));
    }

    @GetMapping("/api/v1/events/{id}")
    public ApiResponse<Map<String, Object>> detail(@PathVariable String id) {
        return ApiResponse.ok(graphQueryService.detail(id));
    }

    @GetMapping("/api/v1/events/{id}/graph")
    public ApiResponse<Map<String, Object>> graph(@PathVariable String id) {
        return ApiResponse.ok(graphQueryService.graph(id));
    }
}
