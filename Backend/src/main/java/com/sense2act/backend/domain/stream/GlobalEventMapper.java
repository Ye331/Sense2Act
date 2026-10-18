package com.sense2act.backend.domain.stream;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Update;

public interface GlobalEventMapper extends BaseMapper<GlobalEvent> {
    /** 让全局通知 ID 的分配顺序与事务提交顺序一致。 */
    @Update("SELECT pg_advisory_xact_lock(9702)")
    void lockJournal();
}
