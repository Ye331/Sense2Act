package com.sense2act.backend.api;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.domain.backtest.BacktestService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.Map;

/** 回测任务(E5-4,契约 §8)。/admin/** 整组仅 admin。评估在外部执行,结果经 PUT 写回。 */
@RestController
@RequestMapping("/api/v1/admin/backtests")
public class AdminBacktestController {

    private final BacktestService backtestService;

    public AdminBacktestController(BacktestService backtestService) {
        this.backtestService = backtestService;
    }

    @GetMapping
    public ApiResponse<PageResult<BacktestService.RunView>> list(@RequestParam(required = false) Integer page,
                                                                 @RequestParam(required = false) Integer page_size) {
        return ApiResponse.ok(backtestService.list(page, page_size));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record BacktestPost(String ruleId, Map<String, Object> paramGrid, String dateFrom, String dateTo) {
    }

    @PostMapping
    public ApiResponse<BacktestService.RunView> create(@RequestBody BacktestPost req) {
        return ApiResponse.ok(backtestService.create(req.ruleId(), req.paramGrid(),
                parseDate(req.dateFrom(), "date_from"), parseDate(req.dateTo(), "date_to")));
    }

    @GetMapping("/{id}")
    public ApiResponse<BacktestService.RunView> detail(@PathVariable String id) {
        return ApiResponse.ok(backtestService.detail(id));
    }

    /** 数据集导出:区间内文档 + 信号 + 信号级反馈标注;queued → running。 */
    @GetMapping("/{id}/dataset")
    public ApiResponse<Map<String, Object>> dataset(@PathVariable String id) {
        return ApiResponse.ok(backtestService.dataset(id));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record ResultPut(String status, Map<String, Object> result) {
    }

    @PutMapping("/{id}/result")
    public ApiResponse<BacktestService.RunView> writeResult(@PathVariable String id, @RequestBody ResultPut req) {
        return ApiResponse.ok(backtestService.writeResult(id, req.status(), req.result()));
    }

    private static LocalDate parseDate(String raw, String field) {
        if (raw == null || raw.isBlank()) {
            throw com.sense2act.backend.common.BusinessException.badRequest(field + " 必填(YYYY-MM-DD)");
        }
        try {
            return LocalDate.parse(raw.trim());
        } catch (DateTimeParseException e) {
            throw com.sense2act.backend.common.BusinessException.badRequest(field + " 格式应为 YYYY-MM-DD: " + raw);
        }
    }
}
