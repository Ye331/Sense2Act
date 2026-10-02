package com.sense2act.backend.domain.graph;

import com.baomidou.mybatisplus.annotation.TableName;

import java.time.OffsetDateTime;

/** 事件-主体关联(event_entities,V5)。复合主键,幂等写入走 insertIgnore。 */
@TableName("event_entities")
public class EventEntityLink {

    private String eventId;

    private String entityId;

    private String role;

    private OffsetDateTime since;

    public EventEntityLink() {
    }

    public EventEntityLink(String eventId, String entityId, String role) {
        this.eventId = eventId;
        this.entityId = entityId;
        this.role = role;
    }

    public String getEventId() {
        return eventId;
    }

    public void setEventId(String eventId) {
        this.eventId = eventId;
    }

    public String getEntityId() {
        return entityId;
    }

    public void setEntityId(String entityId) {
        this.entityId = entityId;
    }

    public String getRole() {
        return role;
    }

    public void setRole(String role) {
        this.role = role;
    }

    public OffsetDateTime getSince() {
        return since;
    }

    public void setSince(OffsetDateTime since) {
        this.since = since;
    }
}
