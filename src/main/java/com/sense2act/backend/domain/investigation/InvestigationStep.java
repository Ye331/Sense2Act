package com.sense2act.backend.domain.investigation;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.sense2act.backend.common.mybatis.JsonbTypeHandler;

import java.time.OffsetDateTime;
import java.util.Map;

/** 调查步骤留痕(investigation_steps,E3-3)。seq 单调递增且即 SSE 事件 id;content = {event, ...载荷}。 */
@TableName(value = "investigation_steps", autoResultMap = true)
public class InvestigationStep {

    public static final String TYPE_QUESTION = "question";
    public static final String TYPE_TOOL_SELECT = "tool_select";
    public static final String TYPE_TOOL_CALL = "tool_call";
    public static final String TYPE_REFLECTION = "reflection";
    public static final String TYPE_STATUS_CHANGE = "status_change";

    @TableId
    private String id;

    private String investigationId;

    private Integer seq;

    private Integer round;

    private String type;

    /** {event: 事件名, ...事件载荷},SSE 补发按 content.event 还原事件名(契约 §7)。 */
    @TableField(typeHandler = JsonbTypeHandler.class)
    private Map<String, Object> content;

    private Integer tokenUsage;

    private OffsetDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getInvestigationId() {
        return investigationId;
    }

    public void setInvestigationId(String investigationId) {
        this.investigationId = investigationId;
    }

    public Integer getSeq() {
        return seq;
    }

    public void setSeq(Integer seq) {
        this.seq = seq;
    }

    public Integer getRound() {
        return round;
    }

    public void setRound(Integer round) {
        this.round = round;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public Map<String, Object> getContent() {
        return content;
    }

    public void setContent(Map<String, Object> content) {
        this.content = content;
    }

    public Integer getTokenUsage() {
        return tokenUsage;
    }

    public void setTokenUsage(Integer tokenUsage) {
        this.tokenUsage = tokenUsage;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
