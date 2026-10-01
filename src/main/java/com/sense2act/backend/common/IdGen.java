package com.sense2act.backend.common;

import com.github.f4b6a3.ulid.Ulid;
import com.github.f4b6a3.ulid.UlidFactory;

/** 带前缀 ULID 生成器:如 usr_01H...,全库唯一且同一进程内单调递增(AGENT.md 硬性规则)。 */
public final class IdGen {

    /** 单调工厂:同一毫秒内也保证递增,避免排序抖动 */
    private static final UlidFactory FACTORY = UlidFactory.newMonotonicInstance();

    private IdGen() {
    }

    public static String next(String prefix) {
        Ulid ulid = FACTORY.create();
        return prefix + "_" + ulid.toString();
    }
}
