package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Update;

/** 调查表。signal_id 唯一约束兜底"一信号至多一调查"的并发(E2-4)。 */
@Mapper
public interface InvestigationMapper extends BaseMapper<Investigation> {

    /**
     * D14 并发闸的事务级串行锁:并发领取的"先计数后 CAS"在 READ COMMITTED 下有竞态,取号前先过独木桥。
     * 用 @Update 声明避免 @Select+void 的结果映射问题(MyBatis 会试图把 void 列构造成返回类型)。
     */
    @Update("SELECT pg_advisory_xact_lock(9701)")
    void lockStartGate();
}
