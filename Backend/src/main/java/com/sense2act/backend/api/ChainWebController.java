package com.sense2act.backend.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.domain.chain.ChainWebService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/v1")
public class ChainWebController {
    private final ChainWebService service;

    public ChainWebController(ChainWebService service) {
        this.service = service;
    }

    @GetMapping("/chain-web")
    public ApiResponse<PageResult<Map<String, Object>>> list(
            @RequestParam(required = false) Integer page,
            @RequestParam(name = "page_size", required = false) Integer pageSize) {
        return ApiResponse.ok(service.list(page, pageSize));
    }

    @GetMapping("/investigations/{id}/chain-web")
    public ApiResponse<JsonNode> detail(@PathVariable String id) {
        return ApiResponse.ok(service.detail(id));
    }

    @GetMapping("/investigations/{id}/chain-web/status")
    public ApiResponse<Map<String, Object>> status(@PathVariable String id) {
        return ApiResponse.ok(service.status(id));
    }
}
