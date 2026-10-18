package com.sense2act.backend.domain.stream;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.common.events.InvestigationStepEvent;
import com.sense2act.backend.common.events.PendingEvent;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
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
 * 日志在业务事务中写入；提交后通知连接从持久日志有序补发。
 * 按用户过滤:当前系统无按用户的数据隔离,全员同流;后续引入私有画像时在此处按用户分流。
 */
@Service
public class GlobalNotificationService {

    public static final long HEARTBEAT_MS = 25_000;

    @EventListener
    public void closeConnections(org.springframework.context.event.ContextClosedEvent ignored) {
        for (Conn conn : conns) conn.emitter().complete();
        conns.clear();
    }

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
        catchUp(conn);
        return emitter;
    }

    @EventListener
    @Transactional
    public void journal(PendingEvent pending) {
        Object event = pending.event();
        if (event instanceof SignalCreatedEvent e) onSignalCreated(e);
        else if (event instanceof SourceDegradedEvent e) onSourceDegraded(e);
        else if (event instanceof InvestigationStepEvent e) onStep(e);
    }

    public void onSignalCreated(SignalCreatedEvent e) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("signal_id", e.signalId());
        payload.put("document_id", e.documentId());
        payload.put("title", e.title());
        payload.put("org_id", e.orgId());
        payload.put("score", e.score() == null ? null : e.score().toPlainString());
        persistAndBroadcast("signal_created", payload);
    }

    public void onSourceDegraded(SourceDegradedEvent e) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("source_id", e.sourceId());
        payload.put("name", e.name());
        payload.put("health", e.health());
        payload.put("occurred_at", e.occurredAt() == null ? null : e.occurredAt().toString());
        persistAndBroadcast("source_degraded", payload);
    }

    /** 调查步骤事件里只有这两类进全局流(契约 §7 全局四类,其余是调查内事件)。 */
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

    /** 先在当前业务事务内记日志，提交成功后才让连接读取。 */
    private void persistAndBroadcast(String name, Map<String, Object> payload) {
        GlobalEvent ge = new GlobalEvent();
        ge.setEvent(name);
        ge.setPayload(payload);
        globalEventMapper.lockJournal();
        globalEventMapper.insert(ge); // 与业务写入共用事务，回滚时日志也回滚
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                for (Conn conn : conns) {
                    try {
                        catchUp(conn);
                    } catch (RuntimeException ex) {
                        org.slf4j.LoggerFactory.getLogger(GlobalNotificationService.class)
                                .warn("全局通知补发失败，下一次心跳重试", ex);
                    }
                }
            }
        });
    }

    /** 心跳:一行注释,EventSource 不会当事件派发(契约 §7,25s 一次)。 */
    @Scheduled(fixedRate = HEARTBEAT_MS)
    public void heartbeat() {
        for (Conn conn : conns) {
            try {
                catchUp(conn);
                conn.emitter().send(SseEmitter.event().comment("hb"));
            } catch (IOException | IllegalStateException e) {
                conn.emitter().completeWithError(e);
                conns.remove(conn);
            }
        }
    }

    /** 从持久游标读取有序事件，回放和实时通知共用连接锁，不跳过较早事件。 */
    private void catchUp(Conn conn) {
        synchronized (conn) {
            for (GlobalEvent ge : globalEventMapper.selectList(new LambdaQueryWrapper<GlobalEvent>()
                    .gt(GlobalEvent::getId, conn.lastId().get()).orderByAsc(GlobalEvent::getId))) {
                send(conn, ge.getId(), ge.getEvent(), ge.getPayload());
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
