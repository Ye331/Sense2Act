package com.sense2act.backend.common.events;

import java.time.Instant;

/** 源健康降级(ok → degraded / down)。E2-7 的全局 SSE source_degraded 事件由此转发给订阅者。 */
public record SourceDegradedEvent(String sourceId, String name, String health, Instant occurredAt) {
}
