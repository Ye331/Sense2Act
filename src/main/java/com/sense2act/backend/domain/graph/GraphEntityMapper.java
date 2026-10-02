package com.sense2act.backend.domain.graph;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;

public interface GraphEntityMapper extends BaseMapper<GraphEntity> {

    /** 幂等落主体:撞 name 唯一约束不炸事务(25P02 坑位),再按名回读复用。 */
    @Insert("INSERT INTO entities (id, name, type, ref_org_id, aliases, created_at) VALUES ("
            + "#{id}, #{name}, #{type}, #{refOrgId},"
            + " #{aliases,typeHandler=com.sense2act.backend.common.mybatis.JsonbTypeHandler}, now()) "
            + "ON CONFLICT (name) DO NOTHING")
    int insertIgnore(GraphEntity e);
}
