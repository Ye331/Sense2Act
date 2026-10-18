package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

/** 步骤留痕表。seq 分配走 max+1(单 Agent 持有调查,UNIQUE(investigation_id, seq) 兜底并发)。 */
@Mapper
public interface InvestigationStepMapper extends BaseMapper<InvestigationStep> {

    @Select("SELECT COALESCE(MAX(seq), 0) + 1 FROM investigation_steps WHERE investigation_id = #{investigationId}")
    int nextSeq(String investigationId);
}
