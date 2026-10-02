package com.sense2act.backend.domain.backtest;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.common.PageParams;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.feedback.FeedbackEvent;
import com.sense2act.backend.domain.feedback.FeedbackEventMapper;
import com.sense2act.backend.domain.rule.SignalRule;
import com.sense2act.backend.domain.rule.SignalRuleMapper;
import com.sense2act.backend.domain.signal.Signal;
import com.sense2act.backend.domain.signal.SignalMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 回测任务(E5-4,契约 §8):后端建任务(queued)、导出数据集(领取即转 running),
 * 评估在外部执行,结果由外部写回(done/failed)。数据集 = 区间内文档 + 信号 + 信号级反馈标注。
 */
@Service
public class BacktestService {

    private static final int SECTION_LIMIT = 2000;

    private final BacktestRunMapper backtestMapper;
    private final SignalRuleMapper ruleMapper;
    private final DocumentMapper documentMapper;
    private final SignalMapper signalMapper;
    private final FeedbackEventMapper feedbackMapper;

    public BacktestService(BacktestRunMapper backtestMapper, SignalRuleMapper ruleMapper,
                           DocumentMapper documentMapper, SignalMapper signalMapper,
                           FeedbackEventMapper feedbackMapper) {
        this.backtestMapper = backtestMapper;
        this.ruleMapper = ruleMapper;
        this.documentMapper = documentMapper;
        this.signalMapper = signalMapper;
        this.feedbackMapper = feedbackMapper;
    }

    @Transactional
    public RunView create(String ruleId, Map<String, Object> paramGrid, LocalDate dateFrom, LocalDate dateTo) {
        if (ruleId == null || ruleId.isBlank()) {
            throw BusinessException.badRequest("rule_id 必填");
        }
        if (dateFrom == null || dateTo == null) {
            throw BusinessException.badRequest("date_from / date_to 必填");
        }
        if (dateTo.isBefore(dateFrom)) {
            throw BusinessException.badRequest("date_to 不得早于 date_from");
        }
        if (ruleMapper.selectCount(new LambdaQueryWrapper<SignalRule>().eq(SignalRule::getId, ruleId)) == 0) {
            throw BusinessException.notFound("规则不存在: " + ruleId);
        }
        BacktestRun run = new BacktestRun();
        run.setId(IdGen.next("bt"));
        run.setRuleId(ruleId);
        run.setParamGrid(paramGrid == null ? Map.of() : paramGrid);
        run.setDateFrom(dateFrom);
        run.setDateTo(dateTo);
        run.setStatus(BacktestRun.STATUS_QUEUED);
        run.setCreatedAt(OffsetDateTime.now());
        run.setUpdatedAt(OffsetDateTime.now());
        backtestMapper.insert(run);
        return view(run);
    }

    @Transactional(readOnly = true)
    public PageResult<RunView> list(Integer page, Integer pageSize) {
        PageParams params = PageParams.of(page, pageSize);
        Long total = backtestMapper.selectCount(null);
        List<RunView> items = backtestMapper.selectList(new LambdaQueryWrapper<BacktestRun>()
                        .orderByDesc(BacktestRun::getCreatedAt)
                        .last("LIMIT " + params.pageSize() + " OFFSET " + (params.page() - 1) * params.pageSize()))
                .stream().map(BacktestService::view).toList();
        return PageResult.of(items, total, params);
    }

    @Transactional(readOnly = true)
    public RunView detail(String id) {
        return view(require(id));
    }

    /** 导出数据集;queued → running(评估方领取即开跑)。 */
    @Transactional
    public Map<String, Object> dataset(String id) {
        BacktestRun run = require(id);
        if (BacktestRun.STATUS_QUEUED.equals(run.getStatus())) {
            run.setStatus(BacktestRun.STATUS_RUNNING);
            run.setUpdatedAt(OffsetDateTime.now());
            backtestMapper.updateById(run);
        }
        LocalDate from = run.getDateFrom();
        LocalDate to = run.getDateTo();

        List<Map<String, Object>> documents = documentMapper.selectList(
                        new LambdaQueryWrapper<Document>()
                                .between(Document::getPublishDate, from, to)
                                .orderByAsc(Document::getPublishDate)
                                .last("LIMIT " + SECTION_LIMIT))
                .stream().map(d -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("id", d.getId());
                    m.put("doc_type", d.getDocType());
                    m.put("title", d.getTitle());
                    m.put("org_id", d.getOrgId());
                    m.put("amount", d.getAmount() == null ? null : d.getAmount().toPlainString());
                    m.put("publish_date", d.getPublishDate() == null ? null : d.getPublishDate().toString());
                    return m;
                }).toList();

        OffsetDateTime fromTs = from.atStartOfDay().atOffset(OffsetDateTime.now().getOffset());
        OffsetDateTime toTs = to.plusDays(1).atStartOfDay().atOffset(OffsetDateTime.now().getOffset());
        List<Map<String, Object>> signals = signalMapper.selectList(
                        new LambdaQueryWrapper<Signal>()
                                .between(Signal::getCreatedAt, fromTs, toTs)
                                .orderByAsc(Signal::getCreatedAt)
                                .last("LIMIT " + SECTION_LIMIT))
                .stream().map(s -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("id", s.getId());
                    m.put("document_id", s.getDocumentId());
                    m.put("org_id", s.getOrgId());
                    m.put("score", s.getScore() == null ? null : s.getScore().toPlainString());
                    m.put("status", s.getStatus());
                    m.put("hits", s.getHits());
                    return m;
                }).toList();

        List<Map<String, Object>> feedbacks = feedbackMapper.selectList(
                        new LambdaQueryWrapper<FeedbackEvent>()
                                .eq(FeedbackEvent::getTargetType, "signal")
                                .between(FeedbackEvent::getCreatedAt, fromTs, toTs)
                                .orderByAsc(FeedbackEvent::getCreatedAt)
                                .last("LIMIT " + SECTION_LIMIT))
                .stream().map(f -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("target_id", f.getTargetId());
                    m.put("action", f.getAction());
                    m.put("reason", f.getReason());
                    m.put("user_id", f.getUserId());
                    return m;
                }).toList();

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("backtest_id", run.getId());
        data.put("rule_id", run.getRuleId());
        data.put("date_from", from.toString());
        data.put("date_to", to.toString());
        data.put("documents", documents);
        data.put("signals", signals);
        data.put("feedback_events", feedbacks);
        return data;
    }

    /** 外部评估写回:status 只收 done/failed;done 必带 result;终态不可再改。 */
    @Transactional
    public RunView writeResult(String id, String status, Map<String, Object> result) {
        if (!Set.of(BacktestRun.STATUS_DONE, BacktestRun.STATUS_FAILED).contains(status)) {
            throw BusinessException.badRequest("status 只能是 done / failed");
        }
        if (BacktestRun.STATUS_DONE.equals(status) && (result == null || result.isEmpty())) {
            throw BusinessException.badRequest("done 必须带回 result");
        }
        BacktestRun run = require(id);
        if (BacktestRun.STATUS_DONE.equals(run.getStatus()) || BacktestRun.STATUS_FAILED.equals(run.getStatus())) {
            throw BusinessException.conflict("回测已终结: " + run.getStatus());
        }
        run.setStatus(status);
        if (result != null) {
            run.setResult(result);
        }
        run.setUpdatedAt(OffsetDateTime.now());
        backtestMapper.updateById(run);
        return view(run);
    }

    private BacktestRun require(String id) {
        BacktestRun run = backtestMapper.selectById(id);
        if (run == null) {
            throw BusinessException.notFound("回测任务不存在: " + id);
        }
        return run;
    }

    public static RunView view(BacktestRun r) {
        return new RunView(r.getId(), r.getRuleId(), r.getParamGrid(),
                r.getDateFrom() == null ? null : r.getDateFrom().toString(),
                r.getDateTo() == null ? null : r.getDateTo().toString(),
                r.getStatus(), r.getResult(), r.getCreatedAt(), r.getUpdatedAt());
    }

    @com.fasterxml.jackson.databind.annotation.JsonNaming(
            com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record RunView(String id, String ruleId, Map<String, Object> paramGrid,
                          String dateFrom, String dateTo, String status,
                          Map<String, Object> result, OffsetDateTime createdAt, OffsetDateTime updatedAt) {
    }
}
