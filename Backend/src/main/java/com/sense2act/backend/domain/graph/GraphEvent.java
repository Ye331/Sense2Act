package com.sense2act.backend.domain.graph;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/** 事件(events,V5):ReportDraft.event_extraction 落库;created_by_report 回指首建它的报告。 */
@TableName("events")
public class GraphEvent {

    public static final String STATUS_ONGOING = "ongoing";
    public static final String STATUS_CLOSED = "closed";
    public static final String STATUS_SPECULATIVE = "speculative";

    @TableId
    private String id;

    private String title;

    private String type;

    private String summary;

    private LocalDate startDate;

    private String status;

    private String createdByReport;

    private OffsetDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public String getSummary() {
        return summary;
    }

    public void setSummary(String summary) {
        this.summary = summary;
    }

    public LocalDate getStartDate() {
        return startDate;
    }

    public void setStartDate(LocalDate startDate) {
        this.startDate = startDate;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getCreatedByReport() {
        return createdByReport;
    }

    public void setCreatedByReport(String createdByReport) {
        this.createdByReport = createdByReport;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
