package com.sense2act.backend.domain.report;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.OffsetDateTime;

/** 行动建议(action_suggestions,V5)。adopted_at 留给 E5 反馈回填。 */
@TableName("action_suggestions")
public class ActionSuggestion {

    @TableId
    private String id;

    private String reportId;

    private String text;

    private String priority;

    private OffsetDateTime adoptedAt;

    private OffsetDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getReportId() {
        return reportId;
    }

    public void setReportId(String reportId) {
        this.reportId = reportId;
    }

    public String getText() {
        return text;
    }

    public void setText(String text) {
        this.text = text;
    }

    public String getPriority() {
        return priority;
    }

    public void setPriority(String priority) {
        this.priority = priority;
    }

    public OffsetDateTime getAdoptedAt() {
        return adoptedAt;
    }

    public void setAdoptedAt(OffsetDateTime adoptedAt) {
        this.adoptedAt = adoptedAt;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
