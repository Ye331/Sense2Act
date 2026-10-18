package com.sense2act.backend.ingest;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.investigation.InvestigationService;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/** 问题通道(§9.3):Agent 提问 POST,PATCH 推进状态/答案/证据引用。 */
@RestController
@RequestMapping("/api/v1/internal/questions")
public class InternalQuestionController {

    private final InvestigationService investigationService;

    public InternalQuestionController(InvestigationService investigationService) {
        this.investigationService = investigationService;
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record QuestionPost(String investigationId, String text, Integer raisedInRound) {
    }

    @PostMapping
    public ApiResponse<InvestigationService.QuestionView> create(@RequestBody QuestionPost req) {
        return ApiResponse.ok(investigationService.createQuestion(
                req.investigationId(), req.text(), req.raisedInRound()));
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record QuestionPatch(String status, String answerSummary, List<String> evidenceIds) {
    }

    @PatchMapping("/{id}")
    public ApiResponse<InvestigationService.QuestionView> update(@PathVariable String id,
                                                                 @RequestBody QuestionPatch req) {
        return ApiResponse.ok(investigationService.updateQuestion(
                id, req.status(), req.answerSummary(), req.evidenceIds()));
    }
}
