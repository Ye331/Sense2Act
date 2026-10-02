package com.sense2act.backend.domain.signal;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;

/** 信号表。列表筛选走 MP wrapper(含 jsonb 的 rule_type EXISTS 条件),分页用 last() 拼 limit/offset。 */
@Mapper
public interface SignalMapper extends BaseMapper<Signal> {

    /**
     * 幂等写入:撞 (document_id, detector, hits) 唯一约束时静默跳过(返回 0),不抛错——
     * PG 里约束异常会打断整个事务(25P02),靠异常再回读走不通,这里从根上不产生异常。
     */
    @Insert("INSERT INTO signals (id, document_id, org_id, detector, hits, score, status) VALUES ("
            + "#{id}, #{documentId}, #{orgId}, #{detector}, "
            + "#{hits,typeHandler=com.sense2act.backend.common.mybatis.JsonbTypeHandler}::jsonb, "
            + "#{score}, #{status}) "
            + "ON CONFLICT (document_id, detector, hits) DO NOTHING")
    int insertIgnoreConflict(Signal s);
}
