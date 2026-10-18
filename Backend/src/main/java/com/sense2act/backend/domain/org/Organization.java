package com.sense2act.backend.domain.org;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.sense2act.backend.common.mybatis.JsonbTypeHandler;

import java.time.OffsetDateTime;
import java.util.List;

/** 机构(organizations)。name 为规范化名,aliases 存归一别名。 */
@TableName(value = "organizations", autoResultMap = true)
public class Organization {

    @TableId
    private String id;

    private String name;

    @TableField(typeHandler = JsonbTypeHandler.class)
    private List<String> aliases;

    private String type;      // buyer / supplier / gov

    private String uscc;

    private String region;

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

    public List<String> getAliases() {
        return aliases;
    }

    public void setAliases(List<String> aliases) {
        this.aliases = aliases;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public String getUscc() {
        return uscc;
    }

    public void setUscc(String uscc) {
        this.uscc = uscc;
    }

    public String getRegion() {
        return region;
    }

    public void setRegion(String region) {
        this.region = region;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
