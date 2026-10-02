package com.sense2act.backend.domain.stream;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.sense2act.backend.common.mybatis.JsonbTypeHandler;

import java.time.OffsetDateTime;
import java.util.Map;

/** 全局通知留痕(global_events,E2-7):BIGSERIAL 主键回填,SSE 的 id 字段即此 id,重连据此补发。 */
@TableName(value = "global_events", autoResultMap = true)
public class GlobalEvent {

    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    private String event;

    @TableField(typeHandler = JsonbTypeHandler.class)
    private Map<String, Object> payload;

    private OffsetDateTime createdAt;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getEvent() {
        return event;
    }

    public void setEvent(String event) {
        this.event = event;
    }

    public Map<String, Object> getPayload() {
        return payload;
    }

    public void setPayload(Map<String, Object> payload) {
        this.payload = payload;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
