package com.sense2act.backend.common.events;

import org.springframework.context.ApplicationEventPublisher;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/** 事务提交后才发事件,避免"事件先到、库里还没有"的竞态(DoD 约束)。 */
public final class Events {

    private Events() {
    }

    public static void publishAfterCommit(ApplicationEventPublisher publisher, Object event) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    publisher.publishEvent(event);
                }
            });
        } else {
            publisher.publishEvent(event);
        }
    }
}
