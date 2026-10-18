package com.sense2act.backend.domain.graph;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;

public interface EventEntityLinkMapper extends BaseMapper<EventEntityLink> {

    @Insert("INSERT INTO event_entities (event_id, entity_id, role, since) "
            + "VALUES (#{eventId}, #{entityId}, #{role}, now()) ON CONFLICT (event_id, entity_id) DO NOTHING")
    int insertIgnore(EventEntityLink link);
}
