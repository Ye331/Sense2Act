package com.sense2act.backend.domain.graph;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.sense2act.backend.common.mybatis.JsonbTypeHandler;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Set;

/** 主体(entities,V5):跨报告沉淀,name 唯一,重复抽取按名复用。 */
@TableName(value = "entities", autoResultMap = true)
public class GraphEntity {

    public static final Set<String> TYPES = Set.of("org", "project", "product", "region", "policy");

    @TableId
    private String id;

    private String name;

    private String type;

    private String refOrgId;

    @TableField(typeHandler = JsonbTypeHandler.class)
    private List<String> aliases;

    private OffsetDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public String getRefOrgId() {
        return refOrgId;
    }

    public void setRefOrgId(String refOrgId) {
        this.refOrgId = refOrgId;
    }

    public List<String> getAliases() {
        return aliases;
    }

    public void setAliases(List<String> aliases) {
        this.aliases = aliases;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
