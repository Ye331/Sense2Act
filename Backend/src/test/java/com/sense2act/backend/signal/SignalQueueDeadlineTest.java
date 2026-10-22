package com.sense2act.backend.signal;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.feedback.FeedbackEventMapper;
import com.sense2act.backend.domain.investigation.InvestigationMapper;
import com.sense2act.backend.domain.org.OrganizationMapper;
import com.sense2act.backend.domain.org.OrgStatsMapper;
import com.sense2act.backend.domain.policy.PolicyService;
import com.sense2act.backend.domain.rule.SignalRuleService;
import com.sense2act.backend.domain.signal.SignalMapper;
import com.sense2act.backend.domain.signal.SignalService;
import org.junit.jupiter.api.Test;
import org.springframework.context.ApplicationEventPublisher;

import java.time.LocalDate;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class SignalQueueDeadlineTest {
    @Test
    void queueCopiesStoredDeadlineAndKeepsMissingDatesOptional() throws Exception {
        DocumentMapper documents = mock(DocumentMapper.class);
        PolicyService policies = mock(PolicyService.class);
        SignalRuleService rules = mock(SignalRuleService.class);
        Document dated = new Document();
        dated.setId("doc_dated");
        dated.setDocType("announcement");
        dated.setDeadline(LocalDate.of(2026, 10, 23));
        Document unknown = new Document();
        unknown.setId("doc_unknown");
        unknown.setDocType("announcement");
        when(documents.selectList(any())).thenReturn(List.of(dated, unknown));
        when(policies.enabledProfileNames()).thenReturn(List.of());
        when(rules.enabledRules()).thenReturn(List.of());
        SignalService service = new SignalService(mock(SignalMapper.class), documents,
                mock(OrganizationMapper.class), mock(OrgStatsMapper.class), mock(InvestigationMapper.class),
                mock(FeedbackEventMapper.class), policies, mock(ApplicationEventPublisher.class), rules);

        var queue = service.detectionQueue(50);
        assertThat(queue.getFirst().document().deadline()).isEqualTo(LocalDate.of(2026, 10, 23));
        assertThat(queue.get(1).document().deadline()).isNull();
        ObjectMapper json = new ObjectMapper().findAndRegisterModules()
                .disable(com.fasterxml.jackson.databind.SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
                .setSerializationInclusion(JsonInclude.Include.NON_NULL);
        var payload = json.readTree(json.writeValueAsString(queue));
        assertThat(payload.get(0).path("document").path("deadline").asText()).isEqualTo("2026-10-23");
        assertThat(payload.get(1).path("document").has("deadline")).isFalse();
        assertThat(payload.get(0).path("document").has("raw")).isFalse();
    }
}
