package com.sense2act.backend.readiness;

import com.sense2act.backend.common.events.InvestigationStepEvent;
import com.sense2act.backend.domain.investigation.*;
import com.sense2act.backend.domain.stream.*;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

/** 把实时到达固定安排在历史查询中，验证历史事件不会被高水位跳过。 */
class StreamOrderingTest {
    @Test void investigationReplayCompletesBeforeLiveCatchUp() throws Exception {
        InvestigationStepMapper mapper=mock(InvestigationStepMapper.class);
        InvestigationSseService service=new InvestigationSseService(mapper);
        AtomicReference<Future<?>> live=new AtomicReference<>();
        try(ExecutorService pool=Executors.newSingleThreadExecutor();
            var emitters=mockConstruction(SseEmitter.class)) {
            when(mapper.selectList(any())).thenAnswer(call -> {
                CountDownLatch started=new CountDownLatch(1);
                live.set(pool.submit(() -> {
                    started.countDown();
                    service.onStepEvent(new InvestigationStepEvent("inv",3,"round_started",Map.of()));
                }));
                assertThat(started.await(5,TimeUnit.SECONDS)).isTrue();
                assertThatThrownBy(() -> live.get().get(200,TimeUnit.MILLISECONDS)).isInstanceOf(TimeoutException.class);
                return List.of(step(1),step(2));
            }).thenReturn(List.of(step(3)));
            service.stream("inv",null);
            live.get().get(5,TimeUnit.SECONDS);
            assertThat(ids(emitters.constructed().get(0),3)).containsExactly("id:1","id:2","id:3");
        }
    }

    @Test void globalReplayCompletesBeforeHeartbeatCatchUp() throws Exception {
        GlobalEventMapper mapper=mock(GlobalEventMapper.class);
        GlobalNotificationService service=new GlobalNotificationService(mapper);
        AtomicReference<Future<?>> live=new AtomicReference<>();
        try(ExecutorService pool=Executors.newSingleThreadExecutor();
            var emitters=mockConstruction(SseEmitter.class)) {
            when(mapper.selectList(any())).thenAnswer(call -> {
                CountDownLatch started=new CountDownLatch(1);
                live.set(pool.submit(() -> { started.countDown(); service.heartbeat(); }));
                assertThat(started.await(5,TimeUnit.SECONDS)).isTrue();
                assertThatThrownBy(() -> live.get().get(200,TimeUnit.MILLISECONDS)).isInstanceOf(TimeoutException.class);
                return List.of(event(1),event(2));
            }).thenReturn(List.of(event(3)));
            service.stream(null);
            live.get().get(5,TimeUnit.SECONDS);
            assertThat(ids(emitters.constructed().get(0),4)).containsExactly("id:1","id:2","id:3");
        }
    }
    static InvestigationStep step(int seq) {
        InvestigationStep s=new InvestigationStep(); s.setSeq(seq); s.setContent(Map.of("event","round_started")); return s;
    }
    static GlobalEvent event(long id) {
        GlobalEvent e=new GlobalEvent(); e.setId(id); e.setEvent("signal_created"); e.setPayload(Map.of()); return e;
    }
    static List<String> ids(SseEmitter emitter,int sends) throws Exception {
        var capture=ArgumentCaptor.forClass(SseEmitter.SseEventBuilder.class);
        verify(emitter,times(sends)).send(capture.capture());
        return capture.getAllValues().stream().flatMap(builder -> builder.build().stream())
                .map(data -> data.getData().toString()).flatMap(text -> text.lines())
                .filter(line -> line.startsWith("id:")).toList();
    }
}
