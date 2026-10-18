package com.sense2act.backend.domain.rule;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;
import com.sense2act.backend.common.mybatis.JsonbTypeHandler;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.Map;

/**
 * 信号规则(signal_rules,E2-6):复合主键 (id, version),不放 @TableId ——
 * MP 不支持复合主键,本表只走 insert/wrapper 查询,版本切换是"插新行 + 停旧行"。
 */
@TableName(value = "signal_rules", autoResultMap = true)
public class SignalRule {

    public static final java.util.Set<String> TYPES =
            java.util.Set.of("amount_anomaly", "frequency_burst", "semantic_match", "composite");

    private String id;

    private Integer version;

    private String name;

    private String type;

    @TableField(typeHandler = JsonbTypeHandler.class)
    private Map<String, Object> params;

    private BigDecimal weight;

    private Boolean enabled;

    @TableField(typeHandler = JsonbTypeHandler.class)
    private Map<String, Object> backtestResult;

    private OffsetDateTime createdAt;

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public Integer getVersion() {
        return version;
    }

    public void setVersion(Integer version) {
        this.version = version;
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

    public Map<String, Object> getParams() {
        return params;
    }

    public void setParams(Map<String, Object> params) {
        this.params = params;
    }

    public BigDecimal getWeight() {
        return weight;
    }

    public void setWeight(BigDecimal weight) {
        this.weight = weight;
    }

    public Boolean getEnabled() {
        return enabled;
    }

    public void setEnabled(Boolean enabled) {
        this.enabled = enabled;
    }

    public Map<String, Object> getBacktestResult() {
        return backtestResult;
    }

    public void setBacktestResult(Map<String, Object> backtestResult) {
        this.backtestResult = backtestResult;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
