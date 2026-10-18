package com.sense2act.backend.domain.feedback;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.domain.report.ActionSuggestion;
import com.sense2act.backend.domain.report.ActionSuggestionMapper;
import com.sense2act.backend.domain.report.Report;
import com.sense2act.backend.domain.report.ReportMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 报告反馈(E5-1,契约 §6):采纳建议回填 action_suggestions.adopted_at;
 * 评语/标误报写 feedback_events(user 取自 JWT),与信号级反馈(E2-5)共用一张表。
 */
@Service
public class ReportFeedbackService {

    private static final int COMMENT_MAX = 1000;   // feedback_events.reason 列宽

    private final ReportMapper reportMapper;
    private final ActionSuggestionMapper suggestionMapper;
    private final FeedbackEventMapper feedbackEventMapper;

    public ReportFeedbackService(ReportMapper reportMapper, ActionSuggestionMapper suggestionMapper,
                                 FeedbackEventMapper feedbackEventMapper) {
        this.reportMapper = reportMapper;
        this.suggestionMapper = suggestionMapper;
        this.feedbackEventMapper = feedbackEventMapper;
    }

    @Transactional
    public FeedbackView submit(String reportId, List<String> adoptedSuggestionIds,
                               String comment, String flag, String userId) {
        boolean hasAdopted = adoptedSuggestionIds != null && !adoptedSuggestionIds.isEmpty();
        boolean hasComment = comment != null && !comment.isBlank();
        boolean hasFlag = flag != null && !flag.isBlank();
        if (!hasAdopted && !hasComment && !hasFlag) {
            throw BusinessException.badRequest("至少提供 adopted_suggestion_ids / comment / flag 之一");
        }
        if (flag != null && !flag.isBlank() && !"false_positive".equals(flag)) {
            throw BusinessException.badRequest("flag 只支持 false_positive");
        }
        if (hasComment && comment.length() > COMMENT_MAX) {
            throw BusinessException.badRequest("comment 最长 " + COMMENT_MAX + " 字");
        }
        Report report = reportMapper.selectById(reportId);
        if (report == null) {
            throw BusinessException.notFound("报告不存在: " + reportId);
        }
        if (!Report.STATUS_PUBLISHED.equals(report.getStatus())) {
            throw BusinessException.conflict("报告未发布,还不能接收反馈");
        }

        // 采纳:建议必须属于本报告;重复采纳幂等(已回填的不重复写事件)
        List<String> newlyAdopted = new ArrayList<>();
        if (hasAdopted) {
            Set<String> ownIds = new HashSet<>();
            for (ActionSuggestion s : suggestionMapper.selectList(new LambdaQueryWrapper<ActionSuggestion>()
                    .eq(ActionSuggestion::getReportId, reportId))) {
                ownIds.add(s.getId());
            }
            for (String sid : adoptedSuggestionIds) {
                if (!ownIds.contains(sid)) {
                    throw BusinessException.unprocessable("建议不属于该报告: " + sid);
                }
            }
            for (String sid : adoptedSuggestionIds) {
                int updated = suggestionMapper.update(null, new LambdaUpdateWrapper<ActionSuggestion>()
                        .eq(ActionSuggestion::getId, sid)
                        .isNull(ActionSuggestion::getAdoptedAt)
                        .set(ActionSuggestion::getAdoptedAt, OffsetDateTime.now()));
                if (updated > 0) {
                    newlyAdopted.add(sid);
                    insertEvent(userId, "suggestion", sid, "adopt", null);
                }
            }
        }

        if (hasComment) {
            insertEvent(userId, "report", reportId, "comment", comment.trim());
        }
        if (hasFlag) {
            insertEvent(userId, "report", reportId, "flag_false_positive", null);
        }
        return new FeedbackView(reportId, newlyAdopted, hasComment, hasFlag);
    }

    private void insertEvent(String userId, String targetType, String targetId, String action, String reason) {
        FeedbackEvent fb = new FeedbackEvent();
        fb.setId(IdGen.next("fbe"));
        fb.setUserId(userId);
        fb.setTargetType(targetType);
        fb.setTargetId(targetId);
        fb.setAction(action);
        fb.setReason(reason);
        fb.setCreatedAt(OffsetDateTime.now());
        feedbackEventMapper.insert(fb);
    }

    @com.fasterxml.jackson.databind.annotation.JsonNaming(
            com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record FeedbackView(String reportId, List<String> newlyAdopted,
                               boolean commentRecorded, boolean flagged) {
    }
}
