package com.sense2act.backend.api;

import com.sense2act.backend.domain.stream.GlobalNotificationService;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * 全局通知流(E2-7,契约 §7):四类事件 + 25s 心跳 + Last-Event-ID 补发。
 * JWT 经 ?token= 传入(SseTokenParamFilter 折叠成头;无效 token 由认证链回 40101)。
 */
@RestController
public class GlobalStreamController {

    private final GlobalNotificationService notificationService;

    public GlobalStreamController(GlobalNotificationService notificationService) {
        this.notificationService = notificationService;
    }

    @GetMapping(value = "/api/v1/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@RequestHeader(value = "Last-Event-ID", required = false) String lastEventId) {
        return notificationService.stream(lastEventId);
    }
}
