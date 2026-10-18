package com.sense2act.backend.api;

import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.domain.investigation.InvestigationService;
import com.sense2act.backend.domain.investigation.InvestigationSseService;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.Map;

/**
 * 调查查询与停止(契约 §5/§7)。读任意登录角色;stop 按方法级规则需 admin/analyst。
 * stream 用 SSE:EventSource 带不了 Authorization 头,JWT 走 ?token=(SseTokenParamFilter 折叠成头)。
 */
@RestController
@RequestMapping("/api/v1/investigations")
public class InvestigationController {

    private final InvestigationService investigationService;
    private final InvestigationSseService sseService;

    public InvestigationController(InvestigationService investigationService,
                                   InvestigationSseService sseService) {
        this.investigationService = investigationService;
        this.sseService = sseService;
    }

    @GetMapping
    public ApiResponse<PageResult<InvestigationService.InvView>> list(
            @RequestParam(required = false) String signal_id,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String date_from,
            @RequestParam(required = false) String date_to,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer page_size) {
        return ApiResponse.ok(investigationService.list(signal_id, status, date_from, date_to, page, page_size));
    }

    @GetMapping("/{id}")
    public ApiResponse<InvestigationService.InvDetailView> detail(@PathVariable String id) {
        return ApiResponse.ok(investigationService.detail(id));
    }

    @GetMapping("/{id}/steps")
    public ApiResponse<Map<String, Object>> steps(@PathVariable String id,
                                                  @RequestParam(required = false) Integer cursor,
                                                  @RequestParam(required = false) Integer limit) {
        return ApiResponse.ok(investigationService.steps(id, cursor, limit));
    }

    /** 事件流(契约 §7):连接即补发 seq > Last-Event-ID 的步骤,25s 心跳;token 经 ?token= 传入。 */
    @GetMapping(path = "/{id}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@PathVariable String id,
                             @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId) {
        investigationService.detail(id);   // 40401 早失败,不建立空流
        return sseService.stream(id, lastEventId);
    }

    @PostMapping("/{id}/stop")
    public ApiResponse<InvestigationService.InvView> stop(@PathVariable String id) {
        return ApiResponse.ok(investigationService.stop(id));
    }
}
