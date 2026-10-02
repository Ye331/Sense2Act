package com.sense2act.backend.domain.stream;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.common.events.InvestigationStepEvent;
import com.sense2act.backend.common.events.SignalCreatedEvent;
import com.sense2act.backend.common.events.SourceDegradedEvent;
import org.springframework.context.event.EventListener;
import org.springframework.http.MediaType;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 全局通知流(E2-7,契约 §7):GET /api/v1/stream。
 * 四类事件(signal_created / investigation_completed / report_ready / source_degraded)
 * 先落 global_events 拿 BIGSERIAL id,再广播;SSE 的 id 字段即该 id,
 * 断线重连带 Last-Event-ID 从表里补发 —— 事件可追溯,不丢已发事件。
 * 事件源均为事务提交后发布的 Spring 事件(Events.publishAfterCommit)。
 * 按用户过滤:当前系统无按用户的数据隔离,全员同流;后续引入私有画像时在此处按用户分流。
 */
@Service
public class GlobalNotificationService {

    public static final long HEARTBEAT_MS = 25_000;

    /** 每连接持"已发到的 id",补发与实时推送之间靠它去重(同调查级 SSE 的做法)。 */
    private record Conn(SseEmitter emitter, AtomicLong lastId) {
    }

    private final List<Conn> conns = new CopyOnWriteArrayList<>();
    private final GlobalEventMapper globalEventMapper;

    public GlobalNotificationService(GlobalEventMapper globalEventMapper) {
        this.globalEventMapper = globalEventMapper;
    }

    public SseEmitter stream(String lastEventId) {
        long from = parseLastEventId(lastEventId);
        SseEmitter emitter = new SseEmitter(0L);   // 不主动超时,靠心跳与客户端断开清理
        Conn conn = new Conn(emitter, new AtomicLong(from));
        conns.add(conn);
        Runnable remove = () -> conns.remove(conn);
        emitter.onCompletion(remove);
        emitter.onTimeout(remove);
        emitter.onError(e -> remove.run());
        // 先注册再补发:补发期间新到的事件由 listener 发,靠 lastId 去重
        for (GlobalEvent ge : globalEventMapper.selectList(new LambdaQueryWrapper<GlobalEvent>()
                .gt(GlobalEvent::getId, from).orderByAsc(GlobalEvent::getId))) {
            send(conn, ge.getId(), ge.getEvent(), ge.getPayload());
        }
        return emitter;
    }

    @EventListener
    public void onSignalCreated(SignalCreatedEvent e) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("signal_id", e.signalId());
        payload.put("document_id", e.documentId());
        payload.put("title", e.title());
        payload.put("org_id", e.orgId());
        payload.put("score", e.score() == null ? null : e.score().toPlainString());
        persistAndBroadcast("signal_created", payload);
    }

    @EventListener
    public void onSourceDegraded(SourceDegradedEvent e) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("source_id", e.sourceId());
        payload.put("name", e.name());
        payload.put("health", e.health());
        payload.put("occurred_at", e.occurredAt() == null ? null : e.occurredAt().toString());
        persistAndBroadcast("source_degraded", payload);
    }

    /** 调查步骤事件里只有这两类进全局流(契约 §7 全局四类,其余是调查内事件)。 */
    @EventListener
    public void onStep(InvestigationStepEvent e) {
        if (!"report_ready".equals(e.eventName()) && !"investigation_completed".equals(e.eventName())) {
            return;
        }
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("investigation_id", e.investigationId());
        if (e.payload() != null) {
            payload.putAll(e.payload());
        }
        persistAndBroadcast(e.eventName(), payload);
    }

    /**
     * 单写者串行:先插表拿 id,再按 id 顺序广播 —— 并发事件不会乱序,
     * 也不会出现"5 已发、4 后到被 lastId 去重丢掉"的竞态。
     */
    private synchronized void persistAndBroadcast(String name, Map<String, Object> payload) {
        GlobalEvent ge = new GlobalEvent();
        ge.setEvent(name);
        ge.setPayload(payload);
        globalEventMapper.insert(ge);   // BIGSERIAL 主键回填 id
        for (Conn conn : conns) {
            send(conn, ge.getId(), name, payload);
        }
    }

    /** 心跳:一行注释,EventSource 不会当事件派发(契约 §7,25s 一次)。 */
    @Scheduled(fixedRate = HEARTBEAT_MS)
    public void heartbeat() {
        for (Conn conn : conns) {
            try {
                conn.emitter().send(SseEmitter.event().comment("hb"));
            } catch (IOException | IllegalStateException e) {
                conn.emitter().completeWithError(e);
                conns.remove(conn);
            }
        }
    }

    private void send(Conn conn, long id, String eventName, Map<String, Object> payload) {
        synchronized (conn) {
            if (id <= conn.lastId().get()) {
                return;   // 补发阶段已含此事件
            }
            try {
                conn.emitter().send(SseEmitter.event()
                        .id(String.valueOf(id))
                        .name(eventName)
                        .data(payload == null ? Map.of() : payload, MediaType.APPLICATION_JSON));
                conn.lastId().set(id);
            } catch (IOException | IllegalStateException e) {
                conn.emitter().completeWithError(e);
                conns.remove(conn);
            }
        }
    }

    private static long parseLastEventId(String raw) {
        if (raw == null || raw.isBlank()) {
            return 0;
        }
        try {
            return Math.max(0, Long.parseLong(raw.trim()));
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
