package com.sense2act.backend.common.events;

import java.util.Map;

/** 调查步骤已落库(事务提交后发布):驱动 /investigations/{id}/stream 的实时推送(契约 §7)。 */
public record InvestigationStepEvent(String investigationId, int seq, String eventName,
                                      Map<String, Object> payload) {
}
