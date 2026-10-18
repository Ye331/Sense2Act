package com.sense2act.backend.domain.signal;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/** 信号视图(契约 §4):列表与详情同形。金额按约定序列化为字符串小数。 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record SignalView(String id, DocRef document, OrgRef org, List<Map<String, Object>> hits,
                         @JsonFormat(shape = JsonFormat.Shape.STRING) BigDecimal score,
                         String status, String investigationId, OffsetDateTime createdAt) {

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record DocRef(String id, String title,
                  @JsonFormat(shape = JsonFormat.Shape.STRING) BigDecimal amount, LocalDate publishDate) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record OrgRef(String id, String name) {
    }
}
