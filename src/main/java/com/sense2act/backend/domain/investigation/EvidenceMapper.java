package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/** 证据表(E3 只读校验引用;登记 API 是 E4-1)。 */
@Mapper
public interface EvidenceMapper extends BaseMapper<Evidence> {
}
