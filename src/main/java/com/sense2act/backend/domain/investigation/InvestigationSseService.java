package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.common.events.InvestigationStepEvent;
import org.springframework.context.event.EventListener;
import org.springframework.http.MediaType;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 调查级 SSE(E3-3,契约 §7):/investigations/{id}/stream。
 * 连接即从 steps 表补发 seq > Last-Event-ID 的全部事件,此后实时推送;
 * 每 25s 发一行心跳注释保活。写库即广播(事件在事务提交后到达,见 Events.publishAfterCommit)。
 */
@Service
public class InvestigationSseService {

    public static final long HEARTBEAT_MS = 25_000;

    /** 每个连接持一个"已发到的 seq",防止"补发查询中恰好又来实时事件"造成的重复推送。 */
    private record Conn(SseEmitter emitter, AtomicLong lastSeq) {
    }

    private final Map<String, List<Conn>> byInvestigation = new ConcurrentHashMap<>();
    private final InvestigationStepMapper stepMapper;

    public InvestigationSseService(InvestigationStepMapper stepMapper) {
        this.stepMapper = stepMapper;
    }

    public SseEmitter stream(String investigationId, String lastEventId) {
        long from = parseLastEventId(lastEventId);
        SseEmitter emitter = new SseEmitter(0L);   // 不主动超时,靠心跳与客户端断开清理
        List<Conn> conns = byInvestigation.computeIfAbsent(investigationId, k -> new CopyOnWriteArrayList<>());
        Conn conn = new Conn(emitter, new AtomicLong(from));
        conns.add(conn);
        Runnable remove = () -> conns.remove(conn);
        emitter.onCompletion(remove);
        emitter.onTimeout(remove);
        emitter.onError(e -> remove.run());
        // 先注册再补发:补发期间提交的新事件由 listener 发,靠 lastSeq 去重
        for (InvestigationStep s : stepMapper.selectList(new LambdaQueryWrapper<InvestigationStep>()
                .eq(InvestigationStep::getInvestigationId, investigationId)
                .gt(InvestigationStep::getSeq, from)
                .orderByAsc(InvestigationStep::getSeq))) {
            send(conn, s.getSeq(), eventName(s.getContent()), payload(s.getContent()));
        }
        return emitter;
    }

    @EventListener
    public void onStepEvent(InvestigationStepEvent event) {
        List<Conn> conns = byInvestigation.get(event.investigationId());
        if (conns == null) {
            return;
        }
        for (Conn conn : conns) {
            send(conn, event.seq(), event.eventName(), event.payload());
        }
    }

    /** 心跳:一行注释,EventSource 不会当事件派发(契约 §7)。 */
    @Scheduled(fixedRate = HEARTBEAT_MS)
    public void heartbeat() {
        for (List<Conn> conns : byInvestigation.values()) {
            for (Conn conn : conns) {
                try {
                    conn.emitter().send(SseEmitter.event().comment("hb"));
                } catch (IOException | IllegalStateException e) {
                    conn.emitter().completeWithError(e);
                    conns.remove(conn);
                }
            }
        }
    }

    private void send(Conn conn, int seq, String eventName, Map<String, Object> payload) {
        synchronized (conn) {
            if (seq <= conn.lastSeq().get()) {
                return;   // 已(在补发或更早的推送里)发过
            }
            try {
                conn.emitter().send(SseEmitter.event()
                        .id(String.valueOf(seq))
                        .name(eventName)
                        .data(payload == null ? Map.of() : payload, MediaType.APPLICATION_JSON));
                conn.lastSeq().set(seq);
            } catch (IOException | IllegalStateException e) {
                conn.emitter().completeWithError(e);
                removeConn(conn);
            }
        }
    }

    private void removeConn(Conn conn) {
        for (List<Conn> conns : byInvestigation.values()) {
            conns.remove(conn);
        }
    }

    private static String eventName(Map<String, Object> content) {
        Object event = content == null ? null : content.get("event");
        return event == null ? "message" : String.valueOf(event);
    }

    private static Map<String, Object> payload(Map<String, Object> content) {
        if (content == null) {
            return Map.of();
        }
        Map<String, Object> data = new java.util.LinkedHashMap<>(content);
        Object removed = data.remove("event");
        return data;
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
