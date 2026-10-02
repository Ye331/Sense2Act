package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/** 调查表。signal_id 唯一约束兜底"一信号至多一调查"的并发(E2-4)。 */
@Mapper
public interface InvestigationMapper extends BaseMapper<Investigation> {
}
