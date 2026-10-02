package com.sense2act.backend.domain.backtest;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.sense2act.backend.common.mybatis.JsonbTypeHandler;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.Map;

/** 回测任务(backtest_runs,V7):状态机 queued → running →(done | failed),result 由外部评估写回。 */
@TableName(value = "backtest_runs", autoResultMap = true)
public class BacktestRun {

    public static final String STATUS_QUEUED = "queued";
    public static final String STATUS_RUNNING = "running";
    public static final String STATUS_DONE = "done";
    public static final String STATUS_FAILED = "failed";

    @TableId
    private String id;

    private String ruleId;

    @TableField(typeHandler = JsonbTypeHandler.class)
    private Map<String, Object> paramGrid;

    private LocalDate dateFrom;

    private LocalDate dateTo;

    private String status;

    @TableField(typeHandler = JsonbTypeHandler.class)
    private Map<String, Object> result;

    private OffsetDateTime createdAt;

    private OffsetDateTime updatedAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getRuleId() {
        return ruleId;
    }

    public void setRuleId(String ruleId) {
        this.ruleId = ruleId;
    }

    public Map<String, Object> getParamGrid() {
        return paramGrid;
    }

    public void setParamGrid(Map<String, Object> paramGrid) {
        this.paramGrid = paramGrid;
    }

    public LocalDate getDateFrom() {
        return dateFrom;
    }

    public void setDateFrom(LocalDate dateFrom) {
        this.dateFrom = dateFrom;
    }

    public LocalDate getDateTo() {
        return dateTo;
    }

    public void setDateTo(LocalDate dateTo) {
        this.dateTo = dateTo;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public Map<String, Object> getResult() {
        return result;
    }

    public void setResult(Map<String, Object> result) {
        this.result = result;
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
