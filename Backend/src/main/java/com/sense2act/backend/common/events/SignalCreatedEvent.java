package com.sense2act.backend.common.events;

import java.math.BigDecimal;
import java.time.Instant;

/** 信号新建落库(SSE signal_created 的载荷源)。E2-7 的全局 SSE 流由此转发;重复推命中已有信号不发。 */
public record SignalCreatedEvent(String signalId, String documentId, String title, String orgId,
                                 BigDecimal score, Instant occurredAt) {
}
