package com.sense2act.backend.domain.org;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;

/** 机构 Mapper。 */
public interface OrganizationMapper extends BaseMapper<Organization> {
    @Insert("INSERT INTO organizations (id, name, aliases) VALUES (#{id}, #{name}, '[]'::jsonb) ON CONFLICT (name) DO NOTHING")
    int insertIgnoreConflict(Organization organization);
}
