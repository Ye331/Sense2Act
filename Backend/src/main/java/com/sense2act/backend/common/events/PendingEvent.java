package com.sense2act.backend.common.events;

/** 在业务事务内发布，仅用于将通知日志与业务写入一同提交。 */
public record PendingEvent(Object event) {
}
