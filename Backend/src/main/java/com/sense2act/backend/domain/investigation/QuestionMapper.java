package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/** 待确认问题表(E3-4)。 */
@Mapper
public interface QuestionMapper extends BaseMapper<Question> {
}
