package com.sense2act.backend.domain.source;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.sense2act.backend.common.mybatis.JsonbTypeHandler;

import java.time.OffsetDateTime;
import java.util.Map;

/** 信息源(sources)。抓取与解析在外部爬虫服务,本表只存配置与运行状态,到期判定在后端。 */
@TableName(value = "sources", autoResultMap = true)
public class Source {

    @TableId
    private String id;

    private String name;

    private String type;          // web_page / api / rss

    private String url;

    private String adapter;

    private String scheduleCron;  // 空 = 仅手动触发

    @TableField(typeHandler = JsonbTypeHandler.class)
    private Map<String, Object> config;

    private Boolean enabled;

    private String health;        // ok / degraded / down

    private OffsetDateTime lastRunAt;   // NULL = 从未跑过,视为到期

    private String lastRunStatus;

    private Integer consecutiveFailures;

    private OffsetDateTime createdAt;

    private OffsetDateTime updatedAt;

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

    public String getUrl() {
        return url;
    }

    public void setUrl(String url) {
        this.url = url;
    }

    public String getAdapter() {
        return adapter;
    }

    public void setAdapter(String adapter) {
        this.adapter = adapter;
    }

    public String getScheduleCron() {
        return scheduleCron;
    }

    public void setScheduleCron(String scheduleCron) {
        this.scheduleCron = scheduleCron;
    }

    public Map<String, Object> getConfig() {
        return config;
    }

    public void setConfig(Map<String, Object> config) {
        this.config = config;
    }

    public Boolean getEnabled() {
        return enabled;
    }

    public void setEnabled(Boolean enabled) {
        this.enabled = enabled;
    }

    public String getHealth() {
        return health;
    }

    public void setHealth(String health) {
        this.health = health;
    }

    public OffsetDateTime getLastRunAt() {
        return lastRunAt;
    }

    public void setLastRunAt(OffsetDateTime lastRunAt) {
        this.lastRunAt = lastRunAt;
    }

    public String getLastRunStatus() {
        return lastRunStatus;
    }

    public void setLastRunStatus(String lastRunStatus) {
        this.lastRunStatus = lastRunStatus;
    }

    public Integer getConsecutiveFailures() {
        return consecutiveFailures;
    }

    public void setConsecutiveFailures(Integer consecutiveFailures) {
        this.consecutiveFailures = consecutiveFailures;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(OffsetDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
