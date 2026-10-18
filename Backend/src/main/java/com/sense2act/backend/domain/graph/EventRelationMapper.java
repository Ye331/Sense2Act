package com.sense2act.backend.domain.graph;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;

public interface EventRelationMapper extends BaseMapper<EventRelation> {

    @Insert("INSERT INTO event_relations (id, source_type, source_id, target_type, target_id, relation, evidence_id, since) "
            + "VALUES (#{id}, #{sourceType}, #{sourceId}, #{targetType}, #{targetId}, #{relation}, #{evidenceId}, now()) "
            + "ON CONFLICT (source_type, source_id, target_type, target_id, relation) DO NOTHING")
    int insertIgnore(EventRelation r);
}
